"""PDF handling: safe fetch + cache, per-page indexing, page ranking, passage
extraction, deterministic quote verification, and highlighted evidence screenshots.

The Reader never receives a whole report. It gets the outline, then the few
highest-ranked pages, then only the best ~700-character passages on those pages.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pymupdf

from .catalog import concept_query, recall_query, record_citations
from .store import CACHE_DIR, PROJECT_DIR, now_iso, rel

ALLOWED_PDF_HOSTS = {"www.everycrsreport.com", "everycrsreport.com", "www.govinfo.gov", "govinfo.gov"}
MAX_PDF_BYTES = 40 * 1024 * 1024
# freshness: pages citing the report's most recent years (latest data) beat older background pages
PAGE_WEIGHTS = {"relevance": 0.42, "coverage": 0.28, "data": 0.12, "freshness": 0.13, "section": 0.05}
YEAR = re.compile(r"\b(?:FY\s?)?((?:19|20)\d{2})\b")
PASSAGE_CHARS = 700
SUMMARY_HEADINGS = re.compile(r"\b(Summary|What GAO Found|Highlights|Key Findings|Conclusions?|Why GAO Did This Study)\b")
BOILERPLATE = re.compile(r"(\.\s?){8,}|Table of Contents|Author Information|Disclaimer|This document is a work of the U\.S\.", re.I)
NUMBER = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:%|percent)|\b\d{1,3}(?:,\d{3})+\b|\b\d+(?:\.\d+)?\b")
INJECTION = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|above) instructions|disregard (?:the |all )?(?:previous |prior )?instructions"
    r"|you are now|system prompt|developer message|<\s*/?\s*(?:system|assistant)\s*>", re.I)


class ReportUnavailable(Exception):
    pass


# --------------------------------------------------------------------------- #
# Fetch and index
# --------------------------------------------------------------------------- #
def ensure_pdf(conn: sqlite3.Connection, report: sqlite3.Row) -> Path:
    if report["local_pdf"]:
        path = PROJECT_DIR / report["local_pdf"]
        if path.exists():
            return path
    url = report["pdf_url"] or ""
    host = urlparse(url).hostname or ""
    if not url:
        hint = (" GAO blocks automated downloads: download the PDF from "
                f"{report['landing_url']} and save it in data/library/." if report["source"] == "GAO" else "")
        raise ReportUnavailable(f"No PDF is available for {report['report_id']}.{hint}")
    if host not in ALLOWED_PDF_HOSTS:
        raise ReportUnavailable(f"PDF host '{host}' is not on the download allowlist.")
    import httpx

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{report['report_id']}.pdf"
    with httpx.stream("GET", url, timeout=60, follow_redirects=False,
                      headers={"User-Agent": "gov-report-research-team/0.1 (MGT 409 class project)"}) as resp:
        if resp.status_code != 200:
            raise ReportUnavailable(f"Download failed for {report['report_id']}: HTTP {resp.status_code}.")
        data = bytearray()
        for chunk in resp.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_PDF_BYTES:
                raise ReportUnavailable("PDF exceeds the 40 MB size limit.")
    if not bytes(data[:5]) == b"%PDF-":
        raise ReportUnavailable("Downloaded file is not a PDF.")
    dest.write_bytes(bytes(data))
    conn.execute("UPDATE reports SET local_pdf=? WHERE report_id=?", (rel(dest), report["report_id"]))
    conn.commit()
    return dest


def _printed_label(page: pymupdf.Page) -> str | None:
    label = page.get_label() if hasattr(page, "get_label") else ""
    if label:
        return label
    h = page.rect.height
    for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if lines and (y1 > h * 0.88 or y0 < h * 0.07):
            m = re.fullmatch(r"(?:Page\s+)?(\d{1,4}|[ivxlc]{1,6})", lines[-1], re.I)
            if m:
                return m.group(1)
    return None


def index_pages(conn: sqlite3.Connection, report_id: str, path: Path) -> int:
    """Extract and full-text-index every page once; later opens are free."""
    have = conn.execute("SELECT COUNT(*) FROM pages WHERE report_id=?", (report_id,)).fetchone()[0]
    if have:
        return have
    texts = []
    with pymupdf.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            texts.append(text)
            conn.execute("INSERT INTO pages (report_id, pdf_page, printed_page, text) VALUES (?, ?, ?, ?)",
                         (report_id, i, _printed_label(page), text))
    record_citations(conn, report_id, "\n".join(texts))
    summary = _learned_summary(texts)
    conn.execute("UPDATE reports SET page_count=?, opened_at=?, summary=CASE WHEN summary='' THEN ? ELSE summary END "
                 "WHERE report_id=?", (len(texts), now_iso(), summary, report_id))
    conn.commit()
    return len(texts)


def _learned_summary(texts: list[str]) -> str:
    """Feed the report's own summary back into the catalog so future searches rank it better."""
    for t in texts[:5]:
        m = SUMMARY_HEADINGS.search(t)
        if m:
            return " ".join(t[m.start():].split())[:1200]
    return " ".join(" ".join(texts[:2]).split())[:800]


def outline(path: Path, max_entries: int = 40) -> list[dict[str, Any]]:
    """The PDF's bookmarks, or (when it has none) bold headings larger than the body text."""
    with pymupdf.open(path) as doc:
        toc = [e for e in doc.get_toc(simple=True) if e[0] <= 2]
        if toc:
            return [{"level": lvl, "title": t.strip()[:120], "pdf_page": pg} for lvl, t, pg in toc[:max_entries]]
        spans = [(i + 1, sp) for i, page in enumerate(doc) for b in page.get_text("dict")["blocks"]
                 for ln in b.get("lines", []) for sp in ln["spans"] if sp["text"].strip()]
    sizes: dict[float, int] = {}
    for _, sp in spans:
        sizes[round(sp["size"])] = sizes.get(round(sp["size"]), 0) + len(sp["text"])
    body = max(sizes, key=sizes.get) if sizes else 10
    heads, seen = [], set()
    for pg, sp in spans:
        t = " ".join(sp["text"].split())
        bold = sp["flags"] & 16 or "Bold" in sp["font"]
        if bold and round(sp["size"]) > body and 4 <= len(t) <= 100 and t.lower() not in seen:
            seen.add(t.lower())
            heads.append({"level": 1 if round(sp["size"]) >= body + 2 else 2, "title": t, "pdf_page": pg})
    if len(heads) > max_entries:  # keep the top level first, then fill with subheadings in page order
        top = [h for h in heads if h["level"] == 1][:max_entries]
        rest = [h for h in heads if h["level"] == 2][: max_entries - len(top)]
        heads = sorted(top + rest, key=lambda h: h["pdf_page"])
    return heads


# --------------------------------------------------------------------------- #
# Page ranking and passages
# --------------------------------------------------------------------------- #
def rank_pages(conn: sqlite3.Connection, report_id: str, concepts: list[list[str]], limit: int = 5,
               exclude_pages: list[int] | None = None) -> list[dict[str, Any]]:
    query = recall_query(concepts)
    if not query:
        return []
    rows = conn.execute(
        """SELECT p.pdf_page, p.printed_page, p.text, bm25(pages_fts) AS bm,
                  snippet(pages_fts, 0, '[', ']', ' ... ', 24) AS snip
           FROM pages_fts JOIN pages p ON p.rowid = pages_fts.rowid
           WHERE pages_fts MATCH ? AND p.report_id = ? ORDER BY bm LIMIT 60""",
        (query, report_id),
    ).fetchall()
    skip = set(exclude_pages or [])
    rows = [r for r in rows if r["pdf_page"] not in skip]
    if not rows:
        return []
    page_set = {r["pdf_page"] for r in rows}
    hits = []
    for c in concepts:
        sub = concept_query(c)
        hits.append({r[0] for r in conn.execute(
            "SELECT p.pdf_page FROM pages_fts JOIN pages p ON p.rowid = pages_fts.rowid "
            "WHERE pages_fts MATCH ? AND p.report_id = ?", (sub, report_id))} & page_set if sub else set())
    best = max(-r["bm"] for r in rows) or 1.0
    latest = {r["pdf_page"]: _latest_year(r["text"]) for r in rows}
    years = [y for y in latest.values() if y]
    lo, hi = (min(years), max(years)) if years else (0, 0)
    scored = []
    for r in rows:
        text = r["text"]
        boiler = bool(BOILERPLATE.search(text[:600]))
        density = min(len(NUMBER.findall(text)) / max(len(text) / 1000, 0.5) / 12, 1.0)
        parts = {
            "relevance": -r["bm"] / best,
            "coverage": sum(1 for h in hits if r["pdf_page"] in h) / len(hits),
            "data": 0.0 if boiler else density,
            "freshness": ((latest[r["pdf_page"]] - lo) / (hi - lo) if hi > lo and latest[r["pdf_page"]] else
                          (1.0 if hi and latest[r["pdf_page"]] == hi else 0.0)),
            "section": 1.0 if SUMMARY_HEADINGS.search(text[:400]) else 0.0,
        }
        score = sum(PAGE_WEIGHTS[k] * v for k, v in parts.items()) * (0.4 if boiler else 1.0)
        scored.append({
            "pdf_page": r["pdf_page"], "printed_page": r["printed_page"], "score": round(score, 3),
            "why_ranked": ", ".join(f"{k} {v:.2f}" for k, v in parts.items()) + (" (boilerplate penalty)" if boiler else ""),
            "snippet": " ".join(r["snip"].split())[:300],
        })
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[: max(1, min(limit, 5))]


def _latest_year(text: str) -> int:
    """Most recent year the page talks about (ignores far-future projection years)."""
    from datetime import date
    cap = date.today().year + 1
    found = [int(y) for y in YEAR.findall(text) if int(y) <= cap]
    return max(found, default=0)


def passages(text: str, focus_terms: list[str], per_page: int = 2) -> list[str]:
    """Best ~700-char windows on a page for the focus terms (sentence-aligned)."""
    flat = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?;])\s+(?=[A-Z\u201c\"(\d])", flat)
    windows, buf = [], ""
    for sent in sentences:
        if buf and len(buf) + len(sent) > PASSAGE_CHARS:
            windows.append(buf)
            buf = ""
        while len(sent) > PASSAGE_CHARS:  # tables and run-on text: hard split
            windows.append(sent[:PASSAGE_CHARS])
            sent = sent[PASSAGE_CHARS:]
        buf = (buf + " " + sent).strip()
    if buf:
        windows.append(buf)
    newest = _latest_year(flat)
    terms = [t.lower() for t in focus_terms if t.strip()]
    stems = {w[:6] for t in terms for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2}

    def score(w: str) -> float:
        low = w.lower()
        phrase = sum(2 for t in terms if t in low)
        words = sum(1 for s in stems if s in low)
        fresh = 0.75 if newest and str(newest) in w else 0.0  # passages with the page's latest year first
        return phrase + words + 0.5 * min(len(NUMBER.findall(w)), 6) / 6 + fresh

    best = sorted(range(len(windows)), key=lambda i: score(windows[i]), reverse=True)[:per_page]
    return [windows[i] for i in sorted(best)]


def flag_injection(text: str) -> bool:
    return bool(INJECTION.search(text))


# --------------------------------------------------------------------------- #
# Quote verification (deterministic) and evidence screenshots
# --------------------------------------------------------------------------- #
# A footnote marker glued to a sentence ("...below the level of the MFP.78 The CBO..."): 1-3 digits right after a
# word's closing punctuation. A faithful quote leaves it out, so matching ignores it on both sides.
FOOTNOTE = re.compile(r"(?<=[A-Za-z)\]\u201d\"\u2019][.,;:])\d{1,3}(?=\s|$)")


def _alnum(s: str) -> str:
    s = FOOTNOTE.sub("", unicodedata.normalize("NFKC", s)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def match_quote(page_text: str, quote: str) -> float:
    """1.0 = the quote appears verbatim (ignoring case, spacing, punctuation, line-break hyphens)."""
    q, p = _alnum(quote), _alnum(page_text)
    if not q:
        return 0.0
    if q in p:
        return 1.0
    step = max(len(q) // 10, 1)
    best = 0.0
    for start in range(0, max(len(p) - len(q), 0) + 1, step):
        best = max(best, difflib.SequenceMatcher(None, p[start:start + len(q)], q, autojunk=False).ratio())
        if best > 0.99:
            break
    return round(min(best, 0.99), 3)


def _split_prefixed(text: str) -> str:
    """'FY2027' / 'CY2025' / 'P.L.117' -> 'FY 2027' so the digits are seen as a number."""
    return re.sub(r"(?<=[A-Za-z.])(?=\d)", " ", text)


def _number_values(text: str) -> set[float]:
    """Every number in the text as a value ('$4,879.8' -> 4879.8, '29 percent' -> 29.0)."""
    out = set()
    for raw in NUMBER.findall(_split_prefixed(text)):
        try:
            out.add(float(re.sub(r"[,\s$%]|percent", "", raw.lower())))
        except ValueError:
            continue
    return out


def numbers_missing(claim: str, quote: str) -> list[str]:
    """Numbers stated in the claim whose value does not appear in the quoted text (likely invented).

    Compares numeric values, not substrings, so "$500 billion" is not 'found' inside "$51.9 billion".
    """
    have = _number_values(quote)
    missing = []
    for raw in NUMBER.findall(_split_prefixed(claim)):
        try:
            value = float(re.sub(r"[,\s$%]|percent", "", raw.lower()))
        except ValueError:
            continue
        if value not in have:
            missing.append(raw.strip())
    return missing


def _quote_rects(page: pymupdf.Page, quote: str) -> list[pymupdf.Rect]:
    words = page.get_text("words")
    norm = [_alnum(w[4]) for w in words]
    joined, owner = "", []
    for i, w in enumerate(norm):
        joined += w
        owner.extend([i] * len(w))
    q = _alnum(quote)
    if not q or not joined:
        return []
    start = joined.find(q)
    if start < 0:
        m = difflib.SequenceMatcher(None, joined, q, autojunk=False).find_longest_match(0, len(joined), 0, len(q))
        if m.size < min(40, len(q) * 0.5):
            return []
        start, length = m.a - m.b, len(q)
        start = max(start, 0)
    else:
        length = len(q)
    first, last = owner[start], owner[min(start + length, len(owner)) - 1]
    return [pymupdf.Rect(words[i][:4]) for i in range(first, last + 1)]


def render_evidence(path: Path, pdf_page: int, quote: str, out_png: Path, dpi: int = 150) -> dict[str, Any]:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(path) as doc:
        page = doc[pdf_page - 1]
        rects = _quote_rects(page, quote)
        if rects:
            for r in rects:
                page.add_highlight_annot(r)  # in-memory only; the PDF on disk is never modified
            box = rects[0]
            for r in rects[1:]:
                box |= r
            clip = pymupdf.Rect(page.rect.x0 + 24, max(box.y0 - 90, 0), page.rect.x1 - 24,
                                min(box.y1 + 90, page.rect.y1))
        else:
            clip = page.rect
        page.get_pixmap(clip=clip, dpi=dpi, annots=True).save(out_png)
    return {"highlighted": bool(rects), "png": rel(out_png)}
