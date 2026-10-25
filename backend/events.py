"""Turn raw audit-trail entries into short, human-readable feed lines for the dashboard."""

from __future__ import annotations

from typing import Any

AGENT_LABELS = {"communication": "Communication", "report_search": "Report Search",
                "report_reader": "Report Reader", "presentation": "Presentation", "solo": "Solo researcher",
                "system": "System"}
MAJOR_TOOLS = {"shortlist_reports", "open_report", "record_finding", "set_finding_status", "compile_brief",
               "record_findings", "decide_findings",
               "search_reports", "read_passages", "mark_report"}


def _clip(text: str, n: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _concepts(args: dict[str, Any]) -> str:
    return " · ".join("/".join(c[:2]) for c in args.get("concepts", []))


def _tool_result(tool: str, r: Any) -> str:
    if not isinstance(r, dict):
        return f"{tool} returned."
    if r.get("ok") is False:
        return f"{tool} refused: {_clip(r.get('error') or r.get('errors'), 140)}"
    if tool == "search_reports":
        top = ", ".join(f"{x['report_id']} ({x['score']:.2f})" for x in r.get("results", [])[:3])
        plan = r.get("plan") or {}
        pri = "; ".join(f"{p['priority']} → {p.get('report_id') or 'no match'}" for p in plan.get("priorities", []))
        return (f"Ranked {r.get('candidates_considered', 0)} candidates → top: {top or 'none'}"
                + (f" · plan: {pri} + newest {', '.join(plan.get('newest_sweep', []))}" if pri else ""))
    if tool == "shortlist_reports":
        return "Shortlisted " + ", ".join(f["report_id"] for f in r.get("filed", []))
    if tool == "open_report":
        return f"Opened {r.get('report_id')} ({r.get('page_count')} pages; only the outline was returned)"
    if tool == "rank_report_pages":
        return f"Best pages in {r.get('report_id')}: " + ", ".join(str(p["pdf_page"]) for p in r.get("pages", []))
    if tool == "read_passages":
        pages = ", ".join(str(p["pdf_page"]) for p in r.get("pages", []))
        return f"Read passages from {r.get('report_id')} p. {pages} (page budget {r.get('pages_read')})"
    if tool == "record_finding":
        warn = " ⚠ " + r["warning"] if r.get("warning") else ""
        return f"Recorded finding #{r.get('finding_id')} (quote match {r.get('match_score', 0):.2f}){warn}"
    if tool == "record_findings":
        res = r.get("results", [])
        ok = [x for x in res if x.get("ok")]
        return f"Recorded {len(ok)} of {len(res)} findings" + (
            f"; refused {len(res) - len(ok)} (quote didn't match its page)" if len(ok) < len(res) else "")
    if tool == "check_findings":
        return f"Checked {len(r.get('checks', []))} findings against their pages"
    if tool == "decide_findings":
        res = r.get("results", [])
        v = sum(1 for x in res if x.get("status") == "verified")
        return f"Verified {v}, rejected {sum(1 for x in res if x.get('status') == 'rejected')}" + (
            f", {sum(1 for x in res if not x.get('ok'))} failed the checks" if any(not x.get("ok") for x in res) else "")
    if tool == "check_finding":
        miss = r.get("numbers_in_claim_not_in_quote") or []
        return f"Checked finding #{r.get('finding_id')}: match {r.get('match_score', 0):.2f}" + (
            f", unbacked numbers {miss}" if miss else ", numbers OK")
    if tool == "set_finding_status":
        shot = " + screenshot" if r.get("evidence") else ""
        return f"Finding #{r.get('finding_id')} {r.get('status')}{shot}"
    if tool == "compile_brief":
        return f"Compiled the evidence brief ({r.get('findings_cited')} findings cited)"
    if tool == "list_findings":
        return f"Listed {len(r.get('findings', []))} findings"
    if tool == "get_shortlist":
        return f"Shortlist has {len(r.get('shortlist', []))} reports"
    if tool == "get_case_status":
        return f"Case status: {r.get('findings')} · budget {r.get('budget')}"
    return f"{tool} done"


def _finished(agent: str, out: dict[str, Any]) -> str:
    if agent == "report_search":
        return f"Filed {len(out.get('shortlisted', []))} report(s); used {out.get('searches_used')} searches"
    if agent == "report_reader":
        return (f"{len(out.get('finding_ids', []))} findings from {len(out.get('reports_read', []))} report(s), "
                f"{out.get('pages_read')} pages · coverage: {out.get('coverage')}")
    if agent == "presentation":
        return f"Brief ready: {len(out.get('verified_ids', []))} verified, {len(out.get('rejected', []))} rejected"
    if agent == "communication":
        return f"Replied to the user ({out.get('status')})"
    if agent == "solo":
        return f"Quick pass finished ({out.get('status')})"
    return "Finished"


FRIENDLY = {"communication": "Coordinator", "report_search": "Researcher", "report_reader": "Reader",
            "presentation": "Fact-checker", "solo": "Solo researcher", "system": "Code check"}


def _title(t: str | None, n: int = 70) -> str:
    return f"“{_clip(t or '', n)}”"


def plain(e: dict[str, Any]) -> str | None:
    """A newcomer-friendly sentence for the progress feed (None = not worth showing)."""
    ev, d = e["event"], e.get("detail", {})
    if ev == "delegation":
        return {"report_search": "Asked the Researcher to find the most relevant reports",
                "report_reader": "Asked the Reader to pull key facts from those reports",
                "presentation": "Asked the Fact-checker to verify every quote"}.get(d.get("to_agent"))
    if ev == "output_rejected":
        return "Caught an unsupported number in the draft answer and sent it back for a fix"
    if ev == "output_repaired":
        return "Removed a sentence whose number no verified quote supports (code)"
    if ev == "auto_verified":
        v, r = len(d.get("verified", [])), len(d.get("rejected", []))
        return f"Checked every quote against its PDF page in code: verified {v}" + (f", left out {r}" if r else "") + \
            " (screenshots captured)"
    if ev == "escalated":
        return f"Quick pass wasn't enough ({e.get('reasoning', '').rstrip('.').lower()}); handing over to the full team"
    if ev == "error":
        return "Something went wrong; the team is recovering"
    if ev != "tool_result":
        return None
    tool, r = d.get("tool"), d.get("result") or {}
    if not isinstance(r, dict):
        return None
    if r.get("ok") is False:
        if tool == "record_finding":
            return "Rejected a quote that didn't match the page exactly"
        if tool == "set_finding_status":
            return "A finding failed verification and was left out"
        return None
    if tool == "search_reports":
        res = r.get("results", [])
        if not res:
            return "Searched the catalog: no good matches for that wording"
        plan = r.get("plan") or {}
        if plan.get("priorities"):
            names = ", ".join(p["priority"] for p in plan["priorities"])
            return (f"Research plan: focus on {names}, plus the {len(plan.get('newest_sweep', []))} newest relevant "
                    f"report(s); found a report for {sum(1 for p in plan['priorities'] if p.get('title'))} of "
                    f"{len(plan['priorities'])} priorities")
        top = res[0]
        years = sorted({(x.get("pub_date") or "")[:4] for x in res if x.get("pub_date")}, reverse=True)
        return (f"Ranked {r.get('candidates_considered', 0)} reports, newest first; best match: "
                f"{_title(top['title'], 60)} ({(top.get('pub_date') or '')[:4]})"
                + (f" · results from {years[-1]}–{years[0]}" if len(years) > 1 else ""))
    if tool == "shortlist_reports":
        return f"Picked {len(r.get('filed', []))} report(s) to read: " + "; ".join(_title(f["title"], 50) for f in r.get("filed", []))
    if tool == "open_report":
        return f"Opened {_title(r.get('title'))} ({r.get('page_count')} pages)"
    if tool == "rank_report_pages":
        pages = ", ".join(str(p["pdf_page"]) for p in r.get("pages", [])[:4])
        return f"Found the most relevant pages: {pages}" if pages else None
    if tool == "read_passages":
        pages = ", ".join(str(p["pdf_page"]) for p in r.get("pages", []))
        return f"Reading short passages on page(s) {pages}"
    if tool == "record_finding":
        return f"Noted a fact with its exact quote (finding #{r.get('finding_id')})"
    if tool == "record_findings":
        res = r.get("results", [])
        ok = sum(1 for x in res if x.get("ok"))
        return f"Noted {ok} fact(s) with exact quotes" + (f"; {len(res) - ok} quote(s) rejected for not matching the page"
                                                           if ok < len(res) else "")
    if tool == "decide_findings":
        res = r.get("results", [])
        v = sum(1 for x in res if x.get("status") == "verified")
        out = f"Verified {v} fact(s) against the PDF and captured screenshots"
        rej = sum(1 for x in res if x.get("status") == "rejected")
        return out + (f"; left out {rej}" if rej else "")
    if tool == "set_finding_status":
        return (f"Verified finding #{r.get('finding_id')} against the PDF and captured a screenshot"
                if r.get("status") == "verified" else f"Left out finding #{r.get('finding_id')} after checking it")
    if tool == "compile_brief":
        return f"Wrote up the evidence ({r.get('findings_cited')} verified facts)"
    return None


def summarize(e: dict[str, Any]) -> tuple[str, str]:
    """(summary, importance) where importance is 'major' or 'minor'."""
    ev, d, agent = e["event"], e.get("detail", {}), e.get("agent") or "system"
    if ev == "case_run_started":
        return f"Case opened on the {d.get('dataset')} dataset ({d.get('catalog_size'):,} reports)", "major"
    if ev == "agent_started":
        return "Started", "minor"
    if ev == "delegation":
        return f"→ {AGENT_LABELS.get(d.get('to_agent'), d.get('to_agent'))}: {_clip(d.get('task', ''))}", "major"
    if ev == "delegation_refused":
        return f"Delegation refused: {_clip(d.get('error', ''))}", "major"
    if ev == "tool_blocked":
        return f"Blocked tool {d.get('tool')} (not allowed for this role)", "major"
    if ev == "tool_call":
        return f"Calling {d.get('tool')}" + (f" with {_concepts(d.get('args', {}))}" if d.get("args", {}).get("concepts") else ""), "minor"
    if ev == "tool_result":
        tool = d.get("tool", "")
        r = d.get("result")
        major = tool in MAJOR_TOOLS or (isinstance(r, dict) and r.get("ok") is False)
        return _tool_result(tool, r), "major" if major else "minor"
    if ev == "model_step":
        return _clip(e.get("reasoning") or "Thinking", 200), "minor"
    if ev == "output_rejected":
        return f"Safety check blocked a reply with unbacked numbers {d.get('numbers')}; agent asked to fix it", "major"
    if ev == "output_repaired":
        return f"Code removed sentences with unbacked numbers {d.get('numbers')} after the last retry", "major"
    if ev == "agent_finished":
        return _finished(agent, d.get("output", {})), "major"
    if ev == "auto_verified":
        return (f"Code verified {len(d.get('verified', []))} finding(s), rejected {len(d.get('rejected', []))}"), "major"
    if ev == "escalated":
        return f"Escalated to the full team: {e.get('reasoning')}", "major"
    if ev == "budget_exceeded":
        return "Token budget reached; the team stopped", "major"
    if ev == "error":
        return f"Error: {_clip(d.get('error', ''))}", "major"
    if ev == "case_run_finished":
        u = d.get("usage", {})
        return (f"Done in {d.get('seconds')}s · {u.get('input_tokens', 0):,} input tokens "
                f"({u.get('cache_read_tokens', 0):,} cached) · {u.get('output_tokens', 0):,} output"), "major"
    return ev, "minor"


def extras(e: dict[str, Any]) -> dict[str, Any]:
    """Small structured fields the dashboard's workflow view needs (tool name, research plan, verified count)."""
    d = e.get("detail", {})
    out: dict[str, Any] = {}
    if e["event"] in ("tool_call", "tool_result"):
        out["tool"] = d.get("tool")
        r = d.get("result")
        if d.get("tool") == "search_reports" and isinstance(r, dict) and (r.get("plan") or {}).get("priorities"):
            out["priorities"] = [p["priority"] for p in r["plan"]["priorities"]]
    if e["event"] == "auto_verified":
        out["verified"] = len(d.get("verified", []))
    return out
