"""SQLite store for the report index and the case board.

One database file per dataset (data/index_<dataset>.db) holds:
  reports / reports_fts   - the catalog (metadata only) with a BM25 full-text index
  pages / pages_fts       - per-page text of every report that has been opened
  citations               - report -> report links found in opened reports (authority signal)
  cases, shortlist,       - the shared "case board": agents hand work to each other
  findings, briefs          by id instead of pasting report text into each other's context

The dataset is chosen with DATASET=sample|full (default sample). Delete the
index file to rebuild it; nothing in data/sample or data/library is modified.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_DIR = PROJECT_DIR / "output"
DATASET = os.environ.get("DATASET", "sample").strip().lower() or "sample"
if DATASET not in {"sample", "full"}:
    raise SystemExit(f"DATASET must be 'sample' or 'full', not '{DATASET}'.")

SAMPLE_DIR = DATA_DIR / "sample"          # committed: small catalog + a few cached PDFs (works offline)
FULL_DIR = DATA_DIR / "full"              # git-ignored: full CRS index + GAO RSS, downloaded on build
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", DATA_DIR / "library"))  # user drop-in PDFs (e.g. GAO reports)
CACHE_DIR = DATA_DIR / "cache" / "pdfs"   # git-ignored: PDFs fetched on demand
EVIDENCE_DIR = OUTPUT_DIR / "evidence"
BRIEFS_DIR = OUTPUT_DIR / "briefs"
DB_PATH = Path(os.environ.get("INDEX_DB_PATH", DATA_DIR / f"index_{DATASET}.db")).resolve()

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    rowid INTEGER PRIMARY KEY,
    report_id TEXT UNIQUE NOT NULL,      -- e.g. R44972, GAO-24-106546
    source TEXT NOT NULL,                -- CRS | GAO | library
    doc_type TEXT,                       -- CRS Report, CRS In Focus, GAO Report, ...
    title TEXT NOT NULL,
    pub_date TEXT,                       -- YYYY-MM-DD
    summary TEXT DEFAULT '',             -- catalog abstract; filled in from page 1 once opened
    pdf_url TEXT,                        -- direct PDF link (allowlisted hosts only are fetched)
    landing_url TEXT,                    -- human-facing official page
    local_pdf TEXT,                      -- path relative to the project, once cached
    page_count INTEGER,
    versions INTEGER DEFAULT 1,          -- times the report was revised (CRS): a 'living canonical report' signal
    opened_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
    title, summary, report_id UNINDEXED, content='reports', content_rowid='rowid',
    tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS reports_ai AFTER INSERT ON reports BEGIN
    INSERT INTO reports_fts(rowid, title, summary, report_id) VALUES (new.rowid, new.title, new.summary, new.report_id);
END;
CREATE TRIGGER IF NOT EXISTS reports_au AFTER UPDATE OF title, summary ON reports BEGIN
    INSERT INTO reports_fts(reports_fts, rowid, title, summary, report_id) VALUES ('delete', old.rowid, old.title, old.summary, old.report_id);
    INSERT INTO reports_fts(rowid, title, summary, report_id) VALUES (new.rowid, new.title, new.summary, new.report_id);
END;

CREATE TABLE IF NOT EXISTS pages (
    rowid INTEGER PRIMARY KEY,
    report_id TEXT NOT NULL,
    pdf_page INTEGER NOT NULL,           -- 1-based physical page in the PDF
    printed_page TEXT,                   -- page label printed on the page, when detectable
    text TEXT NOT NULL,
    UNIQUE (report_id, pdf_page)
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    text, report_id UNINDEXED, content='pages', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, text, report_id) VALUES (new.rowid, new.text, new.report_id);
END;

CREATE TABLE IF NOT EXISTS citations (
    from_report TEXT NOT NULL,
    to_report TEXT NOT NULL,
    PRIMARY KEY (from_report, to_report)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    search_rounds INTEGER NOT NULL DEFAULT 0,
    reports_opened INTEGER NOT NULL DEFAULT 0,
    pages_read INTEGER NOT NULL DEFAULT 0,
    chars_served INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shortlist (
    case_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    round INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    score REAL,
    why TEXT,
    status TEXT NOT NULL DEFAULT 'proposed',   -- proposed | opened | not_useful | unavailable
    reader_note TEXT,
    PRIMARY KEY (case_id, report_id)
);
CREATE TABLE IF NOT EXISTS findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    pdf_page INTEGER NOT NULL,
    printed_page TEXT,
    quote TEXT NOT NULL,                 -- verbatim text from the page
    claim TEXT NOT NULL,                 -- the reader's one-sentence takeaway
    relevance INTEGER NOT NULL,          -- 1 (weak) .. 5 (direct answer)
    match_score REAL NOT NULL,           -- how closely the quote matches the page text (0..1)
    recorded_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'recorded',   -- recorded | verified | rejected
    check_note TEXT,
    evidence_path TEXT,                  -- screenshot PNG, relative to the project
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    case_id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    body_json TEXT NOT NULL,
    html_path TEXT,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after an index was first built (keeps case history)."""
    added = [("reports", "versions", "INTEGER DEFAULT 1"),
             ("cases", "plan", "TEXT"),          # research plan: priorities the planning agent named (JSON)
             ("shortlist", "lane", "TEXT")]      # which search lane proposed the report: newest | priority: ...
    for table, col, decl in added:
        if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError as exc:  # another thread's connection added it first
                if "duplicate column" not in str(exc):
                    raise
    conn.commit()


def rel(path: Path) -> str:
    """Project-relative path string (keeps the DB portable across clones)."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_DIR))
    except ValueError:
        return str(path)
