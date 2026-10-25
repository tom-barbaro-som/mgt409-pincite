"""Shared agent builder: prompt loading, role-filtered MCP tools, and delegation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models import Model

from mcp_server.pdfs import numbers_missing

from ..config import CACHE_LAYOUT, MAX_DELEGATION_DEPTH, MAX_DELEGATIONS_PER_CASE, PROMPTS_DIR, model_settings
from ..mcp_client import ROLE_TOOLS, role_toolset
from ..models import AgentDeps, AgentName

TEAM_RULES_FILE = "team_rules.md"


@dataclass(frozen=True)
class AgentSpec:
    name: AgentName
    title: str
    prompt_file: str
    output_type: type[BaseModel]
    delegate_for: str                       # one line other agents see when deciding whom to delegate to
    may_delegate_to: tuple[AgentName, ...]  # directed delegation graph (keeps each context small)
    check_reply_numbers: bool = False       # reject final output whose numbers aren't in verified quotes


def _read_prompt(file_name: str) -> str:
    return (PROMPTS_DIR / file_name).read_text(encoding="utf-8").strip()


def roster_text(spec: AgentSpec, specs: list[AgentSpec]) -> str:
    if not spec.may_delegate_to:
        return "## Delegation\nYou do not delegate. Do your step and return your report."
    lines = ["## Teammates you may delegate to (one at a time, with `delegate_task`)"]
    for s in specs:
        if s.name in spec.may_delegate_to:
            lines.append(f"- `{s.name}` ({s.title}): {s.delegate_for}")
    return "\n".join(lines)


def tools_text(spec: AgentSpec) -> str:
    return ("## Your MCP tools\n" + ", ".join(sorted(ROLE_TOOLS[spec.name]))
            + (", plus `delegate_task`." if spec.may_delegate_to else "."))


def team_handbook(specs: list[AgentSpec]) -> str:
    """One identical static prefix for EVERY agent, so a single cached copy serves the whole team.
    Everything that differs per agent (role, tools, run context) goes after the cache breakpoint."""
    parts = [_read_prompt(TEAM_RULES_FILE), "# Team handbook",
             "Every agent receives this same handbook (it is cached once for the whole team). Follow ONLY the "
             "section for the role named under \"Your role\" after the handbook; the other sections tell you "
             "what your teammates do.",
             "## The roles\n" + "\n".join(f"- `{s.name}` ({s.title}): {s.delegate_for}" for s in specs)]
    parts += [f"---\n{_read_prompt(s.prompt_file)}" for s in specs]
    return "\n\n".join(parts)


def build_agent(spec: AgentSpec, model: Model, research: MCPToolset, specs: list[AgentSpec]) -> Agent:
    if CACHE_LAYOUT == "shared":
        static = team_handbook(specs)
        role = "\n\n".join([f"## Your role\nYou are `{spec.name}`: follow the \"{spec.title}\" section of the handbook.",
                              roster_text(spec, specs), tools_text(spec)])
    else:  # static text first, so every call of this agent shares one cacheable prompt prefix
        static = "\n\n".join([_read_prompt(TEAM_RULES_FILE), _read_prompt(spec.prompt_file),
                               roster_text(spec, specs), tools_text(spec)])
        role = ""
    agent = Agent(
        model,
        name=spec.name,
        deps_type=AgentDeps,
        output_type=spec.output_type,
        instructions=static,
        toolsets=[role_toolset(research, spec.name)],
        model_settings=model_settings(spec.name),
        retries=OUTPUT_RETRIES,
    )

    if role:
        @agent.instructions
        def your_role() -> str:  # after the cache breakpoint: differs per agent
            return role

    @agent.instructions
    def run_context(ctx: RunContext[AgentDeps]) -> str:
        d = ctx.deps
        return (
            f"## This run\nYou are `{d.agent}`. Case id: {d.run.case_id} (tools fill it in for you). "
            f"Delegation chain: {' -> '.join(d.chain)} (depth {d.depth}/{MAX_DELEGATION_DEPTH}). "
            f"Delegations used on this case: {d.run.delegations}/{MAX_DELEGATIONS_PER_CASE}. "
            f"Token budget for the whole case: {d.run.token_budget:,}; be economical."
        )

    if spec.may_delegate_to:
        # sequential=True: delegations run one at a time, never fanned out in parallel.
        @agent.tool(sequential=True)
        async def delegate_task(ctx: RunContext[AgentDeps], to_agent: AgentName, task: str, reason: str) -> dict:
            """Hand a specific step to a teammate and wait for its structured report.

            Give a precise task (what to look for, which concepts/synonyms, what to return)
            and a one-line reason for the audit trail. Results live on the case board, so
            refer to report ids and finding ids instead of pasting text.

            Args:
                to_agent: One of the teammates listed in your instructions.
                task: Exactly what you need done and returned.
                reason: Why this agent, why now (recorded in the audit trail).
            """
            return await ctx.deps.team.delegate(ctx, to_agent, task, reason)

    if spec.check_reply_numbers:
        @agent.output_validator
        async def numbers_must_be_quoted(ctx: RunContext[AgentDeps], output: BaseModel) -> BaseModel:
            """Deterministic hallucination guard: every number in the reply must appear in a verified quote.
            In lean mode, code first verifies the Solo researcher's findings (there is no AI fact-checker)."""
            team, case_id = ctx.deps.team, ctx.deps.run.case_id
            if ctx.deps.agent == "solo" and output.status in ("answered", "partial"):
                checked = await team.call_research_tool("auto_verify", {"case_id": case_id})
                team.audit.append("auto_verified", run_id=ctx.deps.run.run_id, case_id=case_id, agent="system",
                                  reasoning="Code checked every recorded quote against its PDF page.", detail=checked)
                fixable = [r for r in checked.get("rejected", []) if "exact match" in r["reason"] or "numbers" in r["reason"]]
                if fixable and ctx.retry == 0:  # one chance to re-record near-miss quotes word for word
                    detail = "; ".join(f"finding {r['finding_id']}: {r['reason']}" for r in fixable)[:600]
                    raise ModelRetry(f"Code could not verify: {detail}. Re-record these with record_findings, copying "
                                     "the passage text exactly (and claim numbers exactly as in the quote), then return.")
            found = await team.call_research_tool("list_findings", {"case_id": case_id, "status": "verified"})
            brief = await team.call_research_tool("get_brief", {"case_id": case_id})
            sources = [f["quote"] for f in found.get("findings", [])]
            sources += [f["citation"]["text"] for s in brief.get("brief", {}).get("sections", []) for f in s["findings"]]
            # Small bare counts ("3 reports", "2 findings") are allowed; facts and figures are not.
            text = (getattr(output, "reply", "") or "") + " " + (getattr(output, "headline", "") or "")
            prose = re.sub(r"\S*(?:/|\\|\.html|\.md|case-)\S*", " ", text)  # paths, links, case ids
            missing = [n for n in numbers_missing(prose, " ".join(sources))
                       if not (n.isdigit() and int(n) <= 12)]
            if missing:
                team.audit.append(
                    "output_rejected", run_id=ctx.deps.run.run_id, case_id=case_id,
                    agent=ctx.deps.agent, reasoning="Reply stated numbers not found in any verified quote.",
                    detail={"numbers": missing, "reply": text.strip()})
                ctx.deps.number_rejections += 1
                if ctx.deps.number_rejections <= NUMBER_RETRIES and ctx.retry < OUTPUT_RETRIES:
                    raise ModelRetry(f"Your reply states numbers that are not in any verified quote: {missing}. "
                                     "Remove them or use only numbers from verified findings.")
                # After one retry: never lose a run's verified evidence over one sentence.
                # Code deletes the sentences with unbacked numbers instead (the check itself is unchanged).
                output = _drop_unbacked(output, " ".join(sources), [f["claim"] for f in found.get("findings", [])])
                team.audit.append("output_repaired", run_id=ctx.deps.run.run_id, case_id=case_id, agent="system",
                                  reasoning="Code removed sentences whose numbers no verified quote contains.",
                                  detail={"numbers": missing})
            return output

    return agent


OUTPUT_RETRIES = 2
NUMBER_RETRIES = 1  # one chance to fix an unbacked number; after that, code removes the sentence
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*\-(])|\n+")


def _unbacked(text: str, sources: str) -> list[str]:
    prose = re.sub(r"\S*(?:/|\\|\.html|\.md|case-)\S*", " ", text)
    return [n for n in numbers_missing(prose, sources) if not (n.isdigit() and int(n) <= 12)]


def _drop_unbacked(output: BaseModel, sources: str, verified_claims: list[str]) -> BaseModel:
    """Remove every sentence (or bullet) that states a number no verified quote contains."""
    update = {}
    for field in ("reply", "headline"):
        text = getattr(output, field, None)
        if not text:
            continue
        kept = [part for part in _SENTENCE.split(text) if part.strip() and not _unbacked(part, sources)]
        new = " ".join(kept).strip() if field == "headline" else "\n".join(kept).strip()
        if not new:
            new = verified_claims[0] if field == "headline" and verified_claims else "See the verified evidence below."
        update[field] = new[:280] if field == "headline" else new
    return output.model_copy(update=update)
