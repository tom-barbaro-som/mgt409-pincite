"""Data types shared by the research team."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["communication", "report_search", "report_reader", "presentation", "solo"]
AGENT_NAMES: tuple[AgentName, ...] = ("communication", "report_search", "report_reader", "presentation", "solo")

AuditEvent = Literal[
    "case_run_started",
    "agent_started",
    "model_step",
    "tool_call",
    "tool_result",
    "tool_blocked",
    "delegation",
    "delegation_refused",
    "output_rejected",
    "agent_finished",
    "budget_exceeded",
    "case_run_finished",
    "error",
    "auto_verified",
    "escalated",
]

Mode = Literal["auto", "lean", "team"]


# --------------------------------------------------------------------------- #
# Structured agent outputs (what each agent hands back to whoever delegated)
# --------------------------------------------------------------------------- #
class ShortlistItem(BaseModel):
    report_id: str
    title: str
    why: str = Field(description="One sentence on why it should answer the request.")


class SearchReport(BaseModel):
    agent: Literal["report_search"] = "report_search"
    shortlisted: list[ShortlistItem] = Field(description="Reports you filed this round, best first (1-3).")
    concepts_used: list[list[str]] = Field(description="The concept/synonym groups you searched with.")
    searches_used: int
    more_likely_available: bool = Field(description="True if the score_cliff hint said strong results remain.")
    match_quality: Literal["strong", "weak", "none"] = Field(
        description="strong: top results match the request's specific concepts; weak: only generic or tangential "
                    "concepts match; none: nothing relevant.")
    notes: str = Field(default="", description="Anything the requester should know (e.g. GAO PDF must be added by hand).")
    reasoning: str = Field(description="Short explanation for the audit trail.")


class ReaderReport(BaseModel):
    agent: Literal["report_reader"] = "report_reader"
    finding_ids: list[int] = Field(description="Ids of findings you recorded on the case board.")
    reports_read: list[str]
    pages_read: int
    coverage: Literal["sufficient", "partial", "insufficient"] = Field(
        description="Do the recorded findings answer the request?")
    gaps: list[str] = Field(default_factory=list, description="What is still missing, if anything.")
    asked_search_for_more: bool = False
    reasoning: str


class RejectedFinding(BaseModel):
    finding_id: int
    reason: str


class PresentationReport(BaseModel):
    agent: Literal["presentation"] = "presentation"
    brief_html: str | None = Field(description="Path returned by compile_brief, or null if no brief was built.")
    headline: str
    verified_ids: list[int]
    rejected: list[RejectedFinding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    reasoning: str


class LeanAnswer(BaseModel):
    """The Solo researcher's answer (lean mode). Code verifies its findings and builds the brief."""

    status: Literal["answered", "partial", "needs_clarification", "no_evidence"]
    headline: str = Field(max_length=280,
                          description="One sentence (under 280 characters); numbers only from your quotes.")
    clarifying_question: str | None = None
    gaps: list[str] = Field(default_factory=list, description="What is still missing (used to decide escalation).")
    reasoning: str


class ManagerReply(BaseModel):
    """The Communication agent's final answer to the user."""

    status: Literal["answered", "partial", "needs_clarification", "no_evidence"]
    reply: str = Field(description="Message to the user (Markdown, <= 200 words). Only numbers that appear in "
                                   "verified quotes; point to the brief for details.")
    clarifying_question: str | None = None
    brief_html: str | None = None
    reasoning: str


# --------------------------------------------------------------------------- #
# Run results and audit entries
# --------------------------------------------------------------------------- #
class UsageSummary(BaseModel):
    requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CaseRunResult(BaseModel):
    run_id: str
    case_id: str
    request: str
    outcome: Literal["completed", "budget_exceeded", "error"]
    mode: Mode = "team"
    escalated: bool = False
    escalation_reason: str | None = None
    reply: ManagerReply | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)
    usage_by_agent: dict[str, UsageSummary] = Field(default_factory=dict)
    delegations: int = 0
    seconds: float = 0.0
    message: str | None = None


class AuditEntry(BaseModel):
    """One line of output/audit_trail.json (JSON Lines, append-only)."""

    ts: str
    event: AuditEvent
    run_id: str | None = None
    case_id: str | None = None
    agent: AgentName | Literal["system"] | None = None
    depth: int | None = None
    step: int | None = None
    reasoning: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Run-time dependencies passed to every agent (PydanticAI deps)
# --------------------------------------------------------------------------- #
@dataclass
class CaseRunState:
    """Shared by every agent working on the same case."""

    run_id: str
    case_id: str
    token_budget: int
    delegations: int = 0
    steps: int = 0
    seen_delegations: set[tuple[str, str, str]] = field(default_factory=set)
    pair_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    tool_counts: dict[tuple[str, str], int] = field(default_factory=dict)  # (agent, tool) -> calls, for per-agent caps
    agents_used: set[str] = field(default_factory=set)

    def next_step(self) -> int:
        self.steps += 1
        return self.steps


@dataclass
class AgentDeps:
    """Per-agent-run context: who is acting, how deep in the delegation chain, and the team."""

    agent: AgentName
    run: CaseRunState
    team: Any  # backend.team.ResearchTeam (Any avoids a circular import)
    depth: int = 0
    chain: tuple[str, ...] = ()
    number_rejections: int = 0  # replies sent back for unbacked numbers in this agent run (see agents/base.py)
