"""Multi-domain evaluation: run a fixed question set through the real agent team and score each run.

  python scripts/eval_suite.py                 # every question in EVAL_SET
  python scripts/eval_suite.py --only aca,vague   # questions whose id starts with these prefixes

Each run is a normal case (visible in the dashboard afterwards). Results go to
output/evals/<timestamp>.json plus a Markdown table. Automatic checks are listed per question;
relevance and accuracy still need a human read of each brief (see docs/evals.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.audit import read_audit_trail  # noqa: E402
from backend.config import AUDIT_TRAIL_PATH  # noqa: E402
from backend.team import ResearchTeam, save_run  # noqa: E402

EVIDENCE = {"answered", "partial"}
# CRS reports on the Navy's largest shipbuilding programs (Columbia SSBN, Virginia SSN, Ford CVN, DDG-51/1000).
# The ship question should cite at least one: a new report on a small program must not crowd them all out.
FLAGSHIP_SHIP_REPORTS = {"R41129", "RL32418", "RS20643", "RL32109"}
EVAL_SET = [
    # id, domain, question, acceptable reply statuses, terms that should appear in cited titles/quotes
    ("aca-budget-1", "health × budget", "What impact did passing Obamacare have on the federal budget?",
     EVIDENCE, ["affordable care act", "aca", "health"]),
    ("aca-budget-2", "health × budget", "What impact did passing Obamacare have on the federal budget?",
     EVIDENCE, ["affordable care act", "aca", "health"]),
    ("medicare-drugs", "health", "What did CRS report about Medicare prescription drug price negotiation and its "
     "expected savings?", EVIDENCE, ["medicare", "drug"]),
    ("student-loans", "education", "What are the costs of federal student loan forgiveness programs such as Public "
     "Service Loan Forgiveness?", EVIDENCE, ["loan", "student", "forgiveness"]),
    ("highway-trust-fund", "infrastructure", "How is the Highway Trust Fund financed, and when is it projected to "
     "run short of money?", EVIDENCE, ["highway trust fund"]),
    ("immigration-courts", "immigration", "How large is the immigration court backlog and how has it changed over "
     "time?", EVIDENCE, ["immigration", "court", "backlog"]),
    ("energy-credits", "energy × tax", "What are the estimated budget costs of clean energy tax credits?",
     EVIDENCE, ["energy", "tax", "credit"]),
    ("ships-regression", "defense", "Please find me data regarding first-of-class ship cost overruns",
     EVIDENCE, ["ship", "ddg", "lcs", "frigate", "cost"]),
    # Judgment checks: the manager should not waste tokens on these.
    ("vague", "judgment", "Tell me about the government.", {"needs_clarification"}, []),
    ("out-of-scope", "judgment", "What do official reports say about the economics of a Mars colony on Olympus Mons?",
     {"no_evidence", "partial", "needs_clarification"}, []),
]


def score(qid: str, domain: str, question: str, ok_status: set[str], terms: list[str], result, case: dict,
          brief: dict | None) -> dict:
    events = [e for e in read_audit_trail(AUDIT_TRAIL_PATH, result.run_id)]
    count = lambda name: sum(1 for e in events if e["event"] == name)  # noqa: E731
    refusals = sum(1 for e in events if e["event"] == "tool_result" and isinstance(e["detail"].get("result"), dict)
                   and e["detail"]["result"].get("ok") is False)
    findings = case.get("findings", {})
    cited = [f for s in (brief or {}).get("sections", []) for f in s["findings"]]
    text = " ".join(f["citation"]["text"] + " " + f["quote"] for f in cited).lower()
    status = result.reply.status if result.reply else None
    cited_ids = {f.get("report_id") or f["citation"]["text"].split()[1].rstrip(",") for f in cited}
    flagship = sorted(cited_ids & FLAGSHIP_SHIP_REPORTS)
    checks = {
        "completed": result.outcome == "completed",
        "status_ok": status in ok_status,
        "verified_ge_2": (findings.get("verified", 0) >= 2) if ok_status & EVIDENCE == ok_status else True,
        "on_topic_terms": (not terms) or any(re.search(rf"\b{re.escape(t)}\b", text) for t in terms),
        "every_cited_has_screenshot": all(f.get("evidence_path") for f in cited),
        "no_errors": count("error") == 0,
        **({"covers_flagship_programs": bool(flagship)} if qid.startswith("ships") else {}),
    }
    u = result.usage
    return {
        "id": qid, "domain": domain, "question": question, "case_id": result.case_id, "status": status,
        "checks": checks, "passed": all(checks.values()), "seconds": result.seconds,
        "tokens": {"input": u.input_tokens, "cached": u.cache_read_tokens, "cache_write": u.cache_write_tokens,
                   "output": u.output_tokens, "requests": u.requests},
        "mode": result.mode, "escalated": result.escalated,
        "budget": case.get("budget"), "findings": findings, "delegations": result.delegations,
        "reports_cited": sorted({f["citation"]["text"].split(",")[0] for f in cited}),
        "flagship_cited": flagship,
        "plan": [p["priority"] for p in (case.get("plan") or {}).get("priorities", [])],
        "headline": (brief or {}).get("headline"),
        "newest_source": (brief or {}).get("newest_source_date"),
        "oldest_source": (brief or {}).get("oldest_source_date"),
        "safety_events": {"reply_number_rejections": count("output_rejected"), "tool_refusals": refusals,
                          "delegation_refusals": count("delegation_refused")},
    }


def table(rows: list[dict]) -> str:
    out = ["| Question | Domain | Status | Pass | Verified | Newest source | Reports opened | Pages read | Input (cached) | Output | Time |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t, b = r["tokens"], r["budget"] or {}
        fails = ", ".join(k for k, v in r["checks"].items() if not v)
        out.append(f"| {r['id']} | {r['domain']} | {r['status']} | {'✅' if r['passed'] else '❌ ' + fails} | "
                   f"{r['findings'].get('verified', 0)} | {r.get('newest_source') or '—'} | {b.get('reports_opened')} | {b.get('pages_read')} | "
                   f"{t['input']:,} ({t['cached']:,}) | {t['output']:,} | {r['seconds']}s |")
    return "\n".join(out)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated id prefixes to run (default: all)")
    ap.add_argument("--mode", choices=["auto", "lean", "team"], default="auto")
    args = ap.parse_args()
    prefixes = [p for p in args.only.split(",") if p] or [""]
    todo = [q for q in EVAL_SET if any(q[0].startswith(p) for p in prefixes)]
    out_dir = ROOT / "output" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.mode}"
    rows = []
    async with ResearchTeam() as team:
        for qid, domain, question, ok_status, terms in todo:
            print(f"\n▶ {qid}: {question}", flush=True)
            result = await team.run_case(question, mode=args.mode)
            save_run(result)
            case = await team.call_research_tool("get_case_status", {"case_id": result.case_id})
            brief = await team.call_research_tool("get_brief", {"case_id": result.case_id, "include_quotes": True})
            row = score(qid, domain, question, ok_status, terms, result, case, brief.get("brief"))
            rows.append(row)
            print(f"  {row['status']} · pass={row['passed']} · verified={row['findings'].get('verified', 0)} · "
                  f"{row['tokens']['input']:,} in / {row['tokens']['output']:,} out · {row['seconds']}s", flush=True)
            (out_dir / f"{stamp}.json").write_text(json.dumps(rows, indent=2))
    (out_dir / f"{stamp}.md").write_text(table(rows) + "\n")
    print("\n" + table(rows))
    print(f"\n{sum(r['passed'] for r in rows)}/{len(rows)} passed · results: output/evals/{stamp}.json")


if __name__ == "__main__":
    asyncio.run(main())
