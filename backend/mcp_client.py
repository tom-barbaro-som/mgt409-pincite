"""Connects the agents to the gov-report-research MCP server (the only report-data layer).

The connection comes from .mcp.json, the same config Claude Code uses.
Every agent tool call passes through `_guard_tool_call`, which:
  1. blocks any tool the calling agent's role is not allowed to use
     (the system tool create_case is never allowed),
  2. stamps the run's real case id (and the caller's identity) onto the
     arguments, so an agent cannot read or write another case, and
  3. writes the call and its result to the audit trail.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from fastmcp import Client
from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset

from .audit import AuditTrail
from .config import DATASET, MCP_CONFIG_PATH, MCP_LIMIT_VARS
from .models import AgentDeps

SYSTEM_TOOLS = frozenset({"create_case", "list_cases", "catalog_info", "report_pdf_path", "auto_verify",
                          "reset_case_limits"})

# Default-deny: an agent sees only the tools listed for its role.
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "communication": frozenset({"get_case_status", "get_brief", "get_report_card"}),
    "report_search": frozenset({"search_reports", "get_report_card", "shortlist_reports", "get_shortlist"}),
    "report_reader": frozenset({"get_shortlist", "get_report_card", "open_report", "rank_report_pages",
                                "read_passages", "record_findings", "mark_report"}),
    "presentation": frozenset({"list_findings", "check_findings", "decide_findings", "compile_brief",
                               "get_report_card"}),
    "solo": frozenset({"search_reports", "get_report_card", "shortlist_reports", "get_shortlist", "open_report",
                       "rank_report_pages", "read_passages", "record_findings"}),
}
IDENTITY_ARGS = {"record_finding": "recorded_by", "record_findings": "recorded_by"}
CASELESS_TOOLS = frozenset({"get_report_card"})  # every other tool gets the run's case_id stamped on


def load_mcp_config() -> dict[str, Any]:
    """Read .mcp.json (paths relative to the project) and pass dataset + limit settings to the server."""
    config = json.loads(MCP_CONFIG_PATH.read_text())
    root = MCP_CONFIG_PATH.parent
    for server in config["mcpServers"].values():
        server["cwd"] = str(root)
        command = root / server["command"]
        # Use this interpreter when the configured venv path doesn't exist (e.g. Windows: .venv\Scripts).
        server["command"] = str(command) if command.exists() else sys.executable
        env = server.setdefault("env", {})
        env["DATASET"] = DATASET
        env["PYTHONPATH"] = str(root)
        for var in MCP_LIMIT_VARS + ("INDEX_DB_PATH", "LIBRARY_DIR"):
            if os.environ.get(var):
                env[var] = os.environ[var]
    return config


# The quick pass is meant to be quick: its search limit is enforced here, not only in its prompt.
# (The case-wide MCP limits still apply on top, and reset when Auto hands over to the full team.)
AGENT_CALL_CAPS: dict[str, dict[str, int]] = {"solo": {"search_reports": 2}}


def build_research_toolset(audit: AuditTrail) -> MCPToolset:
    """One MCP connection shared by all agents; each agent gets a role-filtered view."""

    async def _guard_tool_call(ctx: RunContext[AgentDeps], call_tool, name: str, args: dict[str, Any]) -> Any:
        deps = ctx.deps
        common = dict(run_id=deps.run.run_id, case_id=deps.run.case_id, agent=deps.agent, depth=deps.depth)
        if name not in ROLE_TOOLS[deps.agent] or name in SYSTEM_TOOLS:
            audit.append("tool_blocked", **common, detail={"tool": name, "args": args})
            return {"ok": False, "error": f"Tool '{name}' is not permitted for agent '{deps.agent}'."}
        cap = AGENT_CALL_CAPS.get(deps.agent, {}).get(name)
        if cap is not None:
            key = (deps.agent, name)
            used = deps.run.tool_counts.get(key, 0)
            if used >= cap:
                audit.append("tool_blocked", **common, detail={"tool": name, "args": args, "reason": f"quick-pass cap {cap}"})
                return {"ok": False, "error": f"The quick pass may run at most {cap} searches. Shortlist from the results "
                                              "you have, or return no_evidence or partial with your gaps."}
            deps.run.tool_counts[key] = used + 1
        if name not in CASELESS_TOOLS:
            args = {**args, "case_id": deps.run.case_id}
        if name in IDENTITY_ARGS:
            args = {**args, IDENTITY_ARGS[name]: deps.agent}
        step = deps.run.next_step()
        audit.append("tool_call", **common, step=step, detail={"tool": name, "args": args})
        result = await call_tool(name, args)
        audit.append("tool_result", **common, step=step, detail={"tool": name, "result": result})
        return result

    client = Client(load_mcp_config())
    return MCPToolset(client, id="gov-report-research", process_tool_call=_guard_tool_call, max_retries=2)


def role_toolset(research: MCPToolset, agent_name: str):
    allowed = ROLE_TOOLS[agent_name]
    return research.filtered(lambda ctx, tool_def: tool_def.name in allowed)
