"""The research team: agent loops, delegation, budgets, and audit.

Flow for one request:
  run_case(request) -> Communication loop (model -> tools -> model ...)
                         |- delegate_task -> report_search   (ranks catalog, files 1-3 reports)
                         |- delegate_task -> report_reader   (ranks pages, reads passages, records findings)
                         |                    '- may delegate -> report_search for more (<= 2 times)
                         |- delegate_task -> presentation    (checks findings, screenshots, compiles brief)
                         |                    '- may delegate -> report_reader once to fix a finding
                         '- ManagerReply
The delegation graph is directed (not full connectivity) so each agent carries only
the context its own step needs. Every model step, tool call/result, delegation, and
final report is appended to output/audit_trail.json.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .agents import ALL_SPECS, build_agent
from .audit import AuditTrail
from .config import (
    AUDIT_TRAIL_PATH,
    PROJECT_DIR,
    CASE_REQUEST_LIMIT,
    CASE_TOKEN_BUDGET,
    CASE_TOOL_CALL_LIMIT,
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATIONS_PER_CASE,
    MODEL_NAME,
    build_model,
)
from .mcp_client import build_research_toolset
from .models import AgentDeps, AgentName, CaseRunResult, CaseRunState, LeanAnswer, ManagerReply, Mode, UsageSummary

SPECS = {s.name: s for s in ALL_SPECS}
# How many times each delegator may hand work to each teammate on one case.
DEFAULT_MODE: Mode = "auto"
STRONG_MATCH = 0.4  # best concept coverage of any search; off-topic questions score <= 0.2, on-topic ones >= 0.5
MIN_VERIFIED_LEAN = 2  # auto mode escalates when the quick pass read reports but verified fewer facts than this
PAIR_LIMITS = {
    ("communication", "report_search"): 2,
    ("communication", "report_reader"): 2,
    ("communication", "presentation"): 1,
    ("report_reader", "report_search"): 2,
    ("presentation", "report_reader"): 1,
}


RUNS_DIR = PROJECT_DIR / "output" / "runs"


def save_run(result: CaseRunResult) -> Path:
    """Write the run summary (reply, usage per agent, timing) to output/runs/<case>.json."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{result.case_id}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def usage_summary(usage: RunUsage) -> UsageSummary:
    return UsageSummary(
        requests=usage.requests,
        tool_calls=usage.tool_calls,
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


class ResearchTeam:
    """Owns the shared MCP connection and the four agents. Use as an async context manager."""

    def __init__(self, model: Model | None = None, audit: AuditTrail | None = None):
        self.audit = audit or AuditTrail(AUDIT_TRAIL_PATH)
        self.research = build_research_toolset(self.audit)
        self._model = model
        self._agents: dict[str, Agent] | None = None
        self._by_agent: dict[str, UsageSummary] = {}

    @property
    def agents(self) -> dict[str, Agent]:
        """Built on first use, so MCP-only callers never need the model or the key."""
        if self._agents is None:
            self._model = self._model or build_model()
            self._agents = {s.name: build_agent(s, self._model, self.research, ALL_SPECS) for s in ALL_SPECS}
        return self._agents

    async def __aenter__(self) -> "ResearchTeam":
        await self.research.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.research.__aexit__(*exc)

    # ------------------------------------------------------------------ #
    async def call_research_tool(self, name: str, args: dict[str, Any]) -> Any:
        """System-level MCP call (bypasses agent role checks; never exposed to agents)."""
        return await self.research.direct_call_tool(name, args)

    @staticmethod
    def _limits(run: CaseRunState) -> UsageLimits:
        return UsageLimits(request_limit=CASE_REQUEST_LIMIT, tool_calls_limit=CASE_TOOL_CALL_LIMIT,
                           total_tokens_limit=run.token_budget)

    def _log_model_step(self, response: ModelResponse, deps: AgentDeps, usage: RunUsage) -> None:
        u = response.usage
        mine = self._by_agent.setdefault(deps.agent, UsageSummary())
        mine.requests += 1
        mine.input_tokens += u.input_tokens
        mine.cache_read_tokens += u.cache_read_tokens
        mine.cache_write_tokens += u.cache_write_tokens
        mine.output_tokens += u.output_tokens
        mine.total_tokens += u.input_tokens + u.output_tokens

        texts = [p.content for p in response.parts if isinstance(p, TextPart) and p.content.strip()]
        thoughts = [p.content for p in response.parts if isinstance(p, ThinkingPart) and p.content]
        calls = [{"tool": p.tool_name, "args": p.args_as_dict()} for p in response.parts if isinstance(p, ToolCallPart)]
        mine.tool_calls += len(calls)
        names = [c["tool"] for c in calls]
        if thoughts or texts:
            reasoning = " ".join(thoughts + texts)
        elif names == ["final_result"]:
            reasoning = "Finished; returning structured report."
        else:
            reasoning = f"Decided to call: {', '.join(names)}." if names else "No tool calls."
        self.audit.append(
            "model_step", run_id=deps.run.run_id, case_id=deps.run.case_id, agent=deps.agent, depth=deps.depth,
            step=deps.run.next_step(), reasoning=reasoning,
            detail={"planned_tool_calls": calls,
                    "request_usage": {"input": u.input_tokens, "cached_input": u.cache_read_tokens,
                                      "cache_write": u.cache_write_tokens, "output": u.output_tokens},
                    "case_usage_so_far": usage_summary(usage).model_dump()},
        )

    async def run_agent(self, name: AgentName, task: str, deps: AgentDeps, usage: RunUsage) -> Any:
        """One agent loop, logging every step to the audit trail."""
        agent = self.agents[name]
        deps.run.agents_used.add(name)
        common = dict(run_id=deps.run.run_id, case_id=deps.run.case_id, agent=name, depth=deps.depth)
        source = deps.chain[-2] if len(deps.chain) > 1 else "user"
        self.audit.append("agent_started", **common, reasoning=f"Task received from {source}.", detail={"task": task})
        async with agent.iter(task, deps=deps, usage=usage, usage_limits=self._limits(deps.run)) as run:
            async for node in run:
                if Agent.is_call_tools_node(node):
                    self._log_model_step(node.model_response, deps, usage)
            output = run.result.output
        self.audit.append("agent_finished", **common, reasoning=getattr(output, "reasoning", None),
                          detail={"output": output.model_dump(), "case_usage_so_far": usage_summary(usage).model_dump()})
        return output

    async def delegate(self, ctx: RunContext[AgentDeps], to_agent: str, task: str, reason: str) -> dict[str, Any]:
        """Directed delegation with guardrails: allowed edges only, no cycles, depth/count/pair caps."""
        deps, run = ctx.deps, ctx.deps.run
        common = dict(run_id=run.run_id, case_id=run.case_id, agent=deps.agent, depth=deps.depth)
        key = (deps.agent, to_agent, " ".join(task.lower().split()))
        pair = (deps.agent, to_agent)
        error = None
        if to_agent not in SPECS[deps.agent].may_delegate_to:
            allowed = ", ".join(SPECS[deps.agent].may_delegate_to) or "nobody"
            error = f"'{deps.agent}' may not delegate to '{to_agent}' (allowed: {allowed})."
        elif to_agent in deps.chain:
            error = f"'{to_agent}' is already waiting on this chain ({' -> '.join(deps.chain)}). Report back instead."
        elif deps.depth + 1 > MAX_DELEGATION_DEPTH:
            error = f"Delegation depth limit ({MAX_DELEGATION_DEPTH}) reached; finish with what you have."
        elif run.delegations >= MAX_DELEGATIONS_PER_CASE:
            error = f"Delegation limit for this case ({MAX_DELEGATIONS_PER_CASE}) reached."
        elif run.pair_counts.get(pair, 0) >= PAIR_LIMITS.get(pair, 1):
            error = f"You already delegated to '{to_agent}' {PAIR_LIMITS.get(pair, 1)} time(s) on this case."
        elif key in run.seen_delegations:
            error = "This exact task was already delegated on this case; use the earlier report."
        if error:
            self.audit.append("delegation_refused", **common, reasoning=reason,
                              detail={"to_agent": to_agent, "task": task, "error": error})
            return {"ok": False, "error": error}

        run.delegations += 1
        run.seen_delegations.add(key)
        run.pair_counts[pair] = run.pair_counts.get(pair, 0) + 1
        self.audit.append("delegation", **common, step=run.next_step(), reasoning=reason,
                          detail={"from": deps.agent, "to_agent": to_agent, "task": task,
                                  "delegations_used": run.delegations})
        child = AgentDeps(agent=to_agent, run=run, team=self, depth=deps.depth + 1, chain=deps.chain + (to_agent,))
        prompt = f"Case {run.case_id}. Delegated to you by `{deps.agent}`.\nReason: {reason}\n\nTask:\n{task}"
        try:
            report = await self.run_agent(to_agent, prompt, child, usage=ctx.usage)
        except UsageLimitExceeded:
            raise  # a budget stop must halt the whole case
        except Exception as exc:  # report failure to the delegator instead of crashing the case
            self.audit.append("error", **common, detail={"to_agent": to_agent, "error": f"{type(exc).__name__}: {exc}"})
            return {"ok": False, "error": f"{to_agent} failed: {type(exc).__name__}: {exc}"}
        return {"ok": True, "agent": to_agent, "report": report.model_dump()}

    # ------------------------------------------------------------------ #
    @staticmethod
    def new_case_id() -> str:
        return f"case-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"

    async def run_case(self, request: str, case_id: str | None = None, mode: Mode = DEFAULT_MODE) -> CaseRunResult:
        """auto: lean first, escalate to the full team only if the lean answer is partial or thin.
        lean: Solo researcher + code verifier only. team: the four-agent team only."""
        case_id = case_id or self.new_case_id()
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        started = time.monotonic()
        created = await self.call_research_tool("create_case", {"case_id": case_id, "request": request})
        state = CaseRunState(run_id=run_id, case_id=case_id, token_budget=CASE_TOKEN_BUDGET)
        usage = RunUsage()  # shared by both passes, so the case budget covers lean + team together
        self._by_agent = {}
        self.audit.append("case_run_started", run_id=run_id, case_id=case_id, agent="system",
                          reasoning=f"User request received ({mode} mode).",
                          detail={"request": request, "model": MODEL_NAME, "mode": mode, "dataset": created.get("dataset"),
                                  "catalog_size": created.get("catalog_size"), "limits": self._limits(state).__dict__})
        result = CaseRunResult(run_id=run_id, case_id=case_id, request=request, outcome="completed", mode=mode)
        try:
            if mode in ("auto", "lean"):
                lean = await self._run_lean(request, state, usage)
                result.reply = lean["reply"]
                reason = self._escalation_reason(lean) if mode == "auto" else None
                if reason:
                    result.escalated, result.escalation_reason = True, reason
                    self.audit.append("escalated", run_id=run_id, case_id=case_id, agent="system", reasoning=reason,
                                      detail={"verified": lean["verified"], "gaps": lean["answer"].gaps})
                    await self.call_research_tool("reset_case_limits", {"case_id": case_id})
                    board = await self.call_research_tool("get_case_status", {"case_id": case_id})
                    result.reply = await self._run_team(self._escalation_prompt(request, lean, board.get("plan")),
                                                        state, usage)
            else:
                result.reply = await self._run_team(f"User request:\n{request}", state, usage)
        except UsageLimitExceeded as exc:
            self.audit.append("budget_exceeded", run_id=run_id, case_id=case_id, agent="system",
                              reasoning="Case token/request budget exhausted; stopping.", detail={"error": str(exc)})
            result.outcome, result.message = "budget_exceeded", str(exc)
        except Exception as exc:
            self.audit.append("error", run_id=run_id, case_id=case_id, agent="system",
                              detail={"error": f"{type(exc).__name__}: {exc}"})
            result.outcome, result.message = "error", f"{type(exc).__name__}: {exc}"

        result.usage = usage_summary(usage)
        result.usage_by_agent = dict(self._by_agent)
        result.delegations = state.delegations
        result.seconds = round(time.monotonic() - started, 1)
        status = await self.call_research_tool("get_case_status", {"case_id": case_id})
        self.audit.append("case_run_finished", run_id=run_id, case_id=case_id, agent="system",
                          reasoning=result.reply.status if result.reply else result.message,
                          detail={"outcome": result.outcome, "mode": mode, "escalated": result.escalated,
                                  "usage": result.usage.model_dump(),
                                  "usage_by_agent": {k: v.model_dump() for k, v in result.usage_by_agent.items()},
                                  "delegations": state.delegations, "agents_used": sorted(state.agents_used),
                                  "case_budget": status.get("budget"), "seconds": result.seconds})
        return result

    async def _run_team(self, prompt: str, state: CaseRunState, usage: RunUsage) -> ManagerReply:
        deps = AgentDeps(agent="communication", run=state, team=self, depth=0, chain=("communication",))
        return await self.run_agent("communication", prompt, deps, usage)

    async def _run_lean(self, request: str, state: CaseRunState, usage: RunUsage) -> dict[str, Any]:
        """Solo researcher, then code: its findings were verified inside its output check; build the brief."""
        deps = AgentDeps(agent="solo", run=state, team=self, depth=0, chain=("solo",))
        answer: LeanAnswer = await self.run_agent("solo", f"User request:\n{request}", deps, usage)
        found = await self.call_research_tool("list_findings", {"case_id": state.case_id, "status": "verified"})
        verified = found.get("findings", [])[:6]
        ids = [f["finding_id"] for f in verified]
        status = answer.status
        if status == "answered" and not ids:
            status = "partial"  # nothing survived verification: never present an unverified answer as complete
        brief_path = None
        if ids and status in ("answered", "partial"):
            caveats = list(answer.gaps) if status == "partial" else []
            try:
                brief = await self.call_research_tool("compile_brief", {
                    "case_id": state.case_id, "headline": answer.headline[:300],
                    "sections": [{"heading": "Verified evidence", "summary": "Exact quotes checked against their PDF pages.",
                                  "finding_ids": ids}],
                    "caveats": caveats + ["Lean mode: one agent researched; code verified every quote."]})
            except Exception as exc:  # the answer still stands on its verified findings; only the file is missing
                brief = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
            brief_path = brief.get("html") if brief.get("ok") else None
            if not brief.get("ok"):
                self.audit.append("error", run_id=state.run_id, case_id=state.case_id, agent="system",
                                  detail={"error": f"compile_brief refused: {brief.get('errors')}"})
        reply = ManagerReply(status=status, reply=self._lean_reply(answer, status, verified),
                             clarifying_question=answer.clarifying_question, brief_html=brief_path,
                             reasoning=answer.reasoning)
        board = await self.call_research_tool("get_case_status", {"case_id": state.case_id})
        opened = int(str((board.get("budget") or {}).get("reports_opened", "0/0")).split("/")[0])
        return {"answer": answer, "reply": reply, "verified": len(ids), "status": status, "opened": opened,
                "best_match": board.get("best_match") or 0.0}

    @staticmethod
    def _lean_reply(answer: LeanAnswer, status: str, verified: list[dict[str, Any]]) -> str:
        """Written by code from verified findings only, so a rejected fact can never reach the user."""
        if status == "needs_clarification":
            return "I need a bit more detail before searching."
        if status == "no_evidence" or not verified:
            gaps = f" Still missing: {'; '.join(answer.gaps)}." if answer.gaps else ""
            return "I could not verify an answer in the reports I checked." + gaps
        lines = [f"**{answer.headline}**", ""]
        for f in verified:
            src = f"{f['report_id']}, PDF p. {f['pdf_page']}"
            lines.append(f"- {f['claim']} ({src})")
        if status == "partial" and answer.gaps:
            lines += ["", "Not yet covered: " + "; ".join(answer.gaps)]
        return "\n".join(lines)

    @staticmethod
    def _escalation_reason(lean: dict[str, Any]) -> str | None:
        """Escalate only when the quick pass is thin on verified evidence. A partial answer that already has
        verified facts comes back fast with its gaps listed; the user can ask the full team to dig deeper.
        (Escalating every partial answer cost about as much as the team alone without improving the
        answers; see docs/evals.md.)"""
        if lean["status"] == "needs_clarification" or lean["verified"] >= MIN_VERIFIED_LEAN:
            return None
        if lean["status"] in ("answered", "partial"):
            return f"The quick pass verified only {lean['verified']} fact(s)."
        if lean.get("opened", 0) > 0:  # it found reports that looked relevant but could not verify a fact in them
            return f"The quick pass read {lean['opened']} report(s) but verified only {lean['verified']} fact(s)."
        if lean.get("best_match", 0.0) >= STRONG_MATCH:  # the catalog has on-topic reports it didn't use
            return f"The quick pass found on-topic reports but verified only {lean['verified']} fact(s)."
        return None  # nothing on topic in the catalog (e.g. a Mars colony): stop at the quick pass's low cost

    @staticmethod
    def _escalation_prompt(request: str, lean: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
        a = lean["answer"]
        gaps = "; ".join(a.gaps) or "not stated"
        priorities = "; ".join(p["priority"] for p in (plan or {}).get("priorities", [])) or "none recorded"
        return (f"User request:\n{request}\n\nStep 1-2 (plan + quick pass) by the Solo researcher already verified "
                f"{lean['verified']} fact(s) on the case board (see get_case_status). It was not enough: {lean['status']}. "
                f"Its research plan's priorities: {priorities}. Gaps it reported: {gaps}. Fill those gaps, covering the "
                f"priorities it could not: Search will skip reports already shortlisted, and the Fact-checker should "
                f"include the existing verified findings in the brief where relevant.")
