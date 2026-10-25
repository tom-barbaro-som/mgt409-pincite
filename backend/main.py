"""FastAPI backend for the Gov Report Research dashboard.

Run from the project folder (venv active):
    uvicorn backend.main:app --port 8010

It serves the API under /api, evidence screenshots and briefs under /files, and the
built React app (frontend/dist) at /, so a single command runs everything.
All report data goes through the MCP server (the team's shared connection).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audit import read_audit_trail
from .config import AUDIT_TRAIL_PATH, CASE_TOKEN_BUDGET, MODEL_NAME, PROJECT_DIR, REASONING_EFFORT
from .events import AGENT_LABELS, FRIENDLY, extras, plain, summarize
from .team import RUNS_DIR, ResearchTeam, save_run

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"
OUTPUT_DIR = PROJECT_DIR / "output"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AskBody(BaseModel):
    request: str = Field(min_length=8, max_length=1000, description="What the user wants the team to find.")
    mode: Literal["auto", "lean", "team"] = Field("auto", description="auto: quick pass first, full team if needed.")


class Job(BaseModel):
    case_id: str
    request: str
    mode: str = "auto"
    status: Literal["running", "completed", "budget_exceeded", "error"]
    started_at: str
    finished_at: str | None = None
    message: str | None = None


class State:
    team: ResearchTeam
    jobs: dict[str, Job] = {}
    tasks: dict[str, asyncio.Task] = {}

    @classmethod
    def running(cls) -> Job | None:
        return next((j for j in cls.jobs.values() if j.status == "running"), None)


@asynccontextmanager
async def lifespan(_: FastAPI):
    State.team = ResearchTeam()
    async with State.team:  # one MCP connection for the whole server
        yield
    for task in State.tasks.values():
        task.cancel()


app = FastAPI(title="Gov Report Research API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",  # Vite dev server on any local port
    allow_methods=["*"],
    allow_headers=["*"],
)


async def tool(name: str, **args: Any) -> Any:
    """System call to an MCP tool (agents use their own guarded path)."""
    return await State.team.call_research_tool(name, args)


# --------------------------------------------------------------------------- #
# Health and catalog
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health() -> dict:
    info = await tool("catalog_info")
    return {
        "ok": True,
        "model": MODEL_NAME,
        "reasoning_effort": REASONING_EFFORT,
        "agents_ready": bool(os.environ.get("PORTKEY_API_KEY")),
        "case_token_budget": CASE_TOKEN_BUDGET,
        "catalog": info,
        "running": State.running().model_dump() if State.running() else None,
    }


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #
async def _run(case_id: str, request: str, mode: str) -> None:
    job = State.jobs[case_id]
    try:
        result = await State.team.run_case(request, case_id=case_id, mode=mode)
        save_run(result)
        job.status, job.message = result.outcome, result.message
    except Exception as exc:  # surfaced to the dashboard; details are in the audit trail
        job.status, job.message = "error", f"{type(exc).__name__}: {exc}"
    job.finished_at = _now()


@app.post("/api/cases", status_code=202)
async def ask(body: AskBody) -> Job:
    """Start the agent team on a request. One case runs at a time (cost control)."""
    if not os.environ.get("PORTKEY_API_KEY"):
        raise HTTPException(503, "PORTKEY_API_KEY is not set. Add it to .env (see README) and restart the server.")
    if (busy := State.running()):
        raise HTTPException(409, f"The team is already working on {busy.case_id}. Wait for it to finish.")
    case_id = State.team.new_case_id()
    State.jobs[case_id] = Job(case_id=case_id, request=body.request.strip(), mode=body.mode, status="running",
                              started_at=_now())
    State.tasks[case_id] = asyncio.create_task(_run(case_id, body.request.strip(), body.mode))
    return State.jobs[case_id]


def _run_file(case_id: str) -> dict | None:
    path = RUNS_DIR / f"{case_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


@app.get("/api/cases")
async def cases() -> dict:
    listed = (await tool("list_cases", limit=30))["cases"]
    for c in listed:
        job = State.jobs.get(c["case_id"])
        run = None if job and job.status == "running" else _run_file(c["case_id"])
        if job:
            c["run_status"] = job.status
        elif run:
            c["run_status"] = run["outcome"]
        else:  # no result yet: still running (e.g. from the CLI or eval suite) if it started recently
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["created_at"])).total_seconds()
            c["run_status"] = "running" if age < 20 * 60 and c["status"] != "briefed" else "interrupted"
        c["reply_status"] = ((run or {}).get("reply") or {}).get("status")
    return {"cases": listed}


@app.get("/api/cases/{case_id}")
async def case(case_id: str) -> dict:
    status = await tool("get_case_status", case_id=case_id)
    if not status.get("ok"):
        raise HTTPException(404, status.get("error", "Case not found"))
    brief = await tool("get_brief", case_id=case_id, include_quotes=True)
    shortlist = await tool("get_shortlist", case_id=case_id)
    job = State.jobs.get(case_id)
    run = _run_file(case_id)
    return {
        **status,
        "job": job.model_dump() if job else None,
        "run": run,
        "shortlist": shortlist.get("shortlist", []),
        "brief": brief.get("brief") if brief.get("ok") else None,
        "brief_html": brief.get("html_path") if brief.get("ok") else None,
    }


# --------------------------------------------------------------------------- #
# Live events (polled by the dashboard)
# --------------------------------------------------------------------------- #
@app.get("/api/events")
async def events(case_id: str, after: int = Query(0, ge=0)) -> dict:
    """Audit-trail events for one case with seq > after, each with a one-line summary."""
    out = []
    for seq, e in enumerate(read_audit_trail(AUDIT_TRAIL_PATH), start=1):
        if seq <= after or e.get("case_id") != case_id:
            continue
        summary, importance = summarize(e)
        usage = e.get("detail", {}).get("request_usage") if e["event"] == "model_step" else None
        out.append({"seq": seq, "ts": e["ts"], "event": e["event"], "agent": e.get("agent"),
                    "agent_label": AGENT_LABELS.get(e.get("agent") or "system"), "depth": e.get("depth"),
                    "summary": summary, "importance": importance, "usage": usage, "plain": plain(e),
                    "role": FRIENDLY.get(e.get("agent") or "system"),
                    "to_agent": e.get("detail", {}).get("to_agent"), **extras(e)})
    last = out[-1]["seq"] if out else after
    return {"last_seq": last, "events": out}


# --------------------------------------------------------------------------- #
# Files: evidence screenshots, briefs, and source PDFs
# --------------------------------------------------------------------------- #
def _safe(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, "Not found")
    return path


@app.get("/files/{kind}/{rest:path}")
async def files(kind: Literal["evidence", "briefs"], rest: str) -> FileResponse:
    root = OUTPUT_DIR / kind
    return FileResponse(_safe(root / rest, root))


@app.get("/api/reports/{report_id}/pdf")
async def report_pdf(report_id: str) -> FileResponse:
    """The cached source PDF, so 'open at page' links work for every cited report (append #page=N)."""
    found = await tool("report_pdf_path", report_id=report_id)
    if not found.get("ok"):
        raise HTTPException(404, found.get("error"))
    path = _safe(PROJECT_DIR / found["path"], PROJECT_DIR / "data")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": "inline"})


# The built dashboard (npm run build). In development use the Vite dev server instead.
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")
