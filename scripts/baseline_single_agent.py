"""Cost baseline: ONE unoptimized agent does the research "manually", the way a person would.

  python scripts/baseline_single_agent.py                  # the default question set
  python scripts/baseline_single_agent.py --only ships     # one question

Same model (gpt-6-luna), same catalog, same PDFs as the team, but none of the team's
design: no ranking engine (plain keyword title search), no page ranking or passage
selection (it reads whole pages, up to 10 at a time), no case board, and one context
that keeps growing. Its quotes are then scored with the same deterministic verifier
the team's Fact-checker uses. Results: output/evals/baseline-<timestamp>.json

Hard caps keep it from running away: 40 model requests and 1.5M tokens per question.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent, UsageLimits  # noqa: E402
from pydantic_ai.exceptions import UsageLimitExceeded  # noqa: E402
from pydantic_ai.usage import RunUsage  # noqa: E402

from backend.config import build_model, model_settings  # noqa: E402
from mcp_server import pdfs  # noqa: E402
from mcp_server.catalog import _words  # noqa: E402
from mcp_server.store import DB_PATH, connect  # noqa: E402

QUESTIONS = {  # the eight evidence questions of scripts/eval_suite.py (ids match)
    "aca-budget": "What impact did passing Obamacare have on the federal budget?",
    "medicare-drugs": "What did CRS report about Medicare prescription drug price negotiation and its expected savings?",
    "student-loans": "What are the costs of federal student loan forgiveness programs such as Public Service Loan "
                     "Forgiveness?",
    "highway-trust-fund": "How is the Highway Trust Fund financed, and when is it projected to run short of money?",
    "immigration-courts": "How large is the immigration court backlog and how has it changed over time?",
    "energy-credits": "What are the estimated budget costs of clean energy tax credits?",
    "ships-regression": "Please find me data regarding first-of-class ship cost overruns",
}
PAGES_PER_CALL = 10

INSTRUCTIONS = """You are a research assistant. Answer the user's question using official US government
reports (CRS, GAO) in the catalog. Search the catalog by keywords, read report pages, and answer with
3-6 findings. Each finding must quote the report text exactly and give the report id and PDF page
number so a person can verify it. Never invent numbers or quotes."""


class Finding(BaseModel):
    report_id: str
    pdf_page: int
    quote: str = Field(description="Exact text copied from the page")
    claim: str


class Answer(BaseModel):
    answer: str
    findings: list[Finding]


_local = threading.local()  # sync tools run in worker threads; SQLite connections are per-thread


def db():
    if not hasattr(_local, "conn"):
        _local.conn = connect(DB_PATH)
    return _local.conn


agent = Agent(build_model(), output_type=Answer, instructions=INSTRUCTIONS,
              model_settings=model_settings("baseline"), retries=2)


@agent.tool_plain
def search_titles(keywords: str) -> list[dict]:
    """Keyword search over report titles. Returns up to 10 matches (id, title, date)."""
    words = _words(keywords)
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))  # plain keywords: no concepts, recency, or authority
    rows = db().execute(
        "SELECT r.report_id, r.title, r.pub_date FROM reports_fts JOIN reports r ON r.rowid = reports_fts.rowid "
        "WHERE reports_fts MATCH ? ORDER BY bm25(reports_fts) LIMIT 10", (f"title : ({q})",)).fetchall()
    return [dict(r) for r in rows]


@agent.tool_plain
def read_report_pages(report_id: str, start_page: int, end_page: int) -> dict:
    """Read the full text of a report's pages (1-based, at most 10 pages per call)."""
    rep = db().execute("SELECT * FROM reports WHERE report_id=? COLLATE NOCASE", (report_id,)).fetchone()
    if not rep:
        return {"error": f"Unknown report {report_id}"}
    try:
        n = pdfs.index_pages(db(), rep["report_id"], pdfs.ensure_pdf(db(), rep))
    except pdfs.ReportUnavailable as exc:
        return {"error": str(exc)}
    end_page = min(end_page, start_page + PAGES_PER_CALL - 1, n)
    rows = db().execute("SELECT pdf_page, text FROM pages WHERE report_id=? AND pdf_page BETWEEN ? AND ? "
                        "ORDER BY pdf_page", (rep["report_id"], start_page, end_page)).fetchall()
    return {"report_id": rep["report_id"], "title": rep["title"], "page_count": n,
            "pages": [{"pdf_page": r["pdf_page"], "text": " ".join(r["text"].split())} for r in rows]}


def verify(findings: list[Finding]) -> list[dict]:
    """The same deterministic check the team's Fact-checker must pass."""
    out = []
    for f in findings:
        row = db().execute("SELECT text FROM pages WHERE report_id=? COLLATE NOCASE AND pdf_page=?",
                           (f.report_id, f.pdf_page)).fetchone()
        score = pdfs.match_quote(row["text"], f.quote) if row else 0.0
        missing = pdfs.numbers_missing(f.claim, f.quote)
        out.append({**f.model_dump(), "match_score": score, "numbers_not_in_quote": missing,
                    "would_pass_verification": score >= 0.97 and not missing})
    return out


async def run(qid: str, question: str) -> dict:
    usage, started = RunUsage(), time.monotonic()
    result = {"id": qid, "question": question}
    try:
        res = await agent.run(question, usage=usage,
                              usage_limits=UsageLimits(request_limit=40, total_tokens_limit=1_500_000))
        checked = verify(res.output.findings)
        result.update(outcome="completed", answer=res.output.answer, findings=checked,
                      verified=sum(f["would_pass_verification"] for f in checked))
    except UsageLimitExceeded as exc:
        result.update(outcome="hit_cap", error=str(exc), findings=[], verified=0)
    result.update(seconds=round(time.monotonic() - started, 1),
                  tokens={"input": usage.input_tokens, "cached": usage.cache_read_tokens,
                          "output": usage.output_tokens, "requests": usage.requests, "tool_calls": usage.tool_calls})
    return result


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated question ids: " + ", ".join(QUESTIONS))
    args = ap.parse_args()
    ids = [q for q in args.only.split(",") if q] or list(QUESTIONS)
    out = ROOT / "output" / "evals" / f"baseline-{datetime.now():%Y%m%d-%H%M%S}.json"
    rows = []
    for qid in ids:
        print(f"▶ {qid}: {QUESTIONS[qid]}", flush=True)
        r = await run(qid, QUESTIONS[qid])
        rows.append(r)
        out.write_text(json.dumps(rows, indent=2))
        t = r["tokens"]
        print(f"  {r['outcome']} · {r['verified']}/{len(r['findings'])} quotes verify · {t['input']:,} in "
              f"({t['cached']:,} cached) / {t['output']:,} out · {t['requests']} requests · {r['seconds']}s", flush=True)
    print(f"results: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
