"""Catalog ingest and the report ranking engine (no LLM calls, no tokens).

Ranking works like a small web search engine, tuned for government reports:

  1. Recall:   FTS5 BM25 over title (weight 8) and summary (weight 2), porter-stemmed,
               using every phrase of every concept the agent supplies -> top 150 candidates.
  2. Rerank:   score = 0.30 relevance      (BM25, normalized within the candidate set)
                     + 0.35 coverage       (IDF-weighted share of the request's concepts matched:
                                            a rare concept like "first-of-class" counts more than "Navy")
                     + 0.10 depth          (full CRS/GAO reports > In Focus > Insight)
                     + 0.20 recency        (exponential decay, 4-year scale: newest information first)
                     + 0.05 authority      (revision count = canonical living report, citation in-links
                                            from opened reports, past verified findings = "click-through")
  3. Diversity: maximal-marginal-relevance pick so the shortlist isn't five versions of one series.
  4. Newest to oldest: candidates are recalled per date tier (current <=3 yrs, recent 3-8, archive 8+),
     so older title-only reports are never crowded out. Results come from the newest tier with strong
     topical matches first; older tiers fill in only once newer strong matches are used up. An older
     report is also penalized when a newer one in the same series covers the same concepts ("superseded").
     prefer_recent=False ranks all eras together, for explicitly historical questions.

Every result carries its score breakdown, so the Search agent (and the audit trail)
can see *why* a report ranked where it did, and a `score_cliff` hint that says
whether more good results are likely below the cut.
"""

from __future__ import annotations

import csv
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from .store import CACHE_DIR, FULL_DIR, LIBRARY_DIR, SAMPLE_DIR, rel

CATALOG_FIELDS = ["report_id", "source", "doc_type", "title", "pub_date", "summary", "pdf_url", "landing_url", "versions"]
EVERYCRS_BASE = "https://www.everycrsreport.com/"
EVERYCRS_INDEX_URL = EVERYCRS_BASE + "reports.csv"
GAO_RSS_URL = "https://www.gao.gov/rss/reports.xml"

CRS_TYPES = {"R": "CRS Report", "RL": "CRS Report", "RS": "CRS Report", "IF": "CRS In Focus",
             "IN": "CRS Insight", "IG": "CRS Infographic", "LSB": "CRS Legal Sidebar"}
DEPTH_WEIGHT = {"CRS Report": 1.0, "GAO Report": 1.0, "Library PDF": 0.9, "CRS In Focus": 0.7,
                "CRS Legal Sidebar": 0.6, "CRS Insight": 0.55, "CRS Infographic": 0.4}
# Newest information first: recency is the second-largest signal after topical match.
WEIGHTS = {"relevance": 0.30, "coverage": 0.35, "depth": 0.10, "recency": 0.20, "authority": 0.05}
# Only for questions that explicitly ask about a past period (prefer_recent=False).
HISTORICAL_WEIGHTS = {"relevance": 0.40, "coverage": 0.40, "depth": 0.10, "recency": 0.05, "authority": 0.05}
CANDIDATES_PER_TIER = 120
# Newest to oldest: (name, min age, max age) in years
TIERS = [("current", 0, 3), ("recent", 3, 8), ("archive", 8, 1000)]
STRONG_FRACTION = 0.8   # a 'strong' match fits the request within 80% of the best fit in any era
MMR_PENALTY = 0.25
SUMMARY_MATCH_WEIGHT = 0.65
RECENCY_YEARS = 4.0        # exp(-age/4): 1 yr 0.78, 3 yr 0.47, 5 yr 0.29, 10 yr 0.08
SUPERSEDED_PENALTY = 0.08  # older report when a newer one covers the same concepts in the same series

GAO_ID = re.compile(r"\bGAO-\d{2}-\d{2,6}[A-Z]{0,2}\b", re.I)
CRS_ID = re.compile(r"\b(?:RL|RS|IF|IN|IG|LSB|R)\d{4,5}\b")
_WORD = re.compile(r"[A-Za-z0-9]+")


# --------------------------------------------------------------------------- #
# Ingest: every source becomes the same catalog row shape
# --------------------------------------------------------------------------- #
def crs_rows(everycrs_csv: Path) -> Iterable[dict[str, str]]:
    """EveryCRSReport.com bulk index (public, keyless): number,url,sha1,latestPubDate,title,latestPDF,..."""
    with everycrs_csv.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            number = (r.get("number") or "").strip()
            if not number or not r.get("title"):
                continue
            prefix = re.match(r"[A-Z]+", number)
            yield {
                "report_id": number,
                "source": "CRS",
                "doc_type": CRS_TYPES.get(prefix.group() if prefix else "", "CRS Product"),
                "title": r["title"].strip(),
                "pub_date": (r.get("latestPubDate") or "")[:10],
                "summary": "",
                "pdf_url": EVERYCRS_BASE + r["latestPDF"] if r.get("latestPDF") else "",
                "landing_url": f"https://www.congress.gov/crs-product/{number}",
            }


CRS_META_FILE = FULL_DIR / "crs_meta.jsonl"  # offline crawl of per-report metadata (scripts/enrich_catalog.py)
SUMMARY_CHARS = 700


def load_crs_meta(path: Path = CRS_META_FILE) -> dict[str, dict[str, Any]]:
    import json
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {m["report_id"]: m for m in (json.loads(line) for line in f if line.strip())}


def with_meta(rows: Iterable[dict[str, str]], meta: dict[str, dict[str, Any]]) -> Iterable[dict[str, str]]:
    """Add crawled abstracts and revision counts to catalog rows (title-only otherwise)."""
    for r in rows:
        m = meta.get(r["report_id"])
        if m:
            r = {**r, "summary": r.get("summary") or (m.get("summary") or "")[:SUMMARY_CHARS],
                 "versions": str(m.get("versions") or 1)}
        yield r


def gao_rows(rss_xml: Path) -> Iterable[dict[str, str]]:
    """GAO's public reports RSS feed (recent products, with summaries). PDFs are not fetched
    automatically: GAO blocks automated downloads, so a GAO PDF must be placed in data/library/."""
    root = ET.parse(rss_xml).getroot()
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        m = re.search(r"/products/(gao-[\w-]+)", link, re.I)
        if not m:
            continue
        try:
            pub = parsedate_to_datetime(item.findtext("pubDate") or "").date().isoformat()
        except (TypeError, ValueError):
            pub = ""
        yield {
            "report_id": m.group(1).upper(),
            "source": "GAO",
            "doc_type": "GAO Report",
            "title": (item.findtext("title") or "").strip(),
            "pub_date": pub,
            "summary": " ".join((item.findtext("description") or "").split())[:1500],
            "pdf_url": "",
            "landing_url": link,
        }


def library_rows() -> Iterable[dict[str, str]]:
    """PDFs a person dropped in data/library/ (optional library.csv adds titles, dates, links)."""
    meta: dict[str, dict[str, str]] = {}
    manifest = LIBRARY_DIR / "library.csv"
    if manifest.exists():
        with manifest.open(encoding="utf-8", newline="") as f:
            meta = {r["file"]: r for r in csv.DictReader(f) if r.get("file")}
    for pdf in sorted(LIBRARY_DIR.glob("*.pdf")):
        m = meta.get(pdf.name, {})
        gao = GAO_ID.search(pdf.stem.replace("_", "-"))
        report_id = m.get("report_id") or (gao.group().upper() if gao else pdf.stem)
        source = m.get("source") or ("GAO" if gao else "library")
        landing = m.get("landing_url") or (f"https://www.gao.gov/products/{report_id.lower()}" if gao else "")
        yield {
            "report_id": report_id,
            "source": source,
            "doc_type": m.get("doc_type") or ("GAO Report" if gao else "Library PDF"),
            "title": m.get("title") or _pdf_title(pdf) or pdf.stem,
            "pub_date": m.get("pub_date", ""),
            "summary": m.get("summary", ""),
            "pdf_url": "",
            "landing_url": landing,
            "local_pdf": rel(pdf),
        }


def _pdf_title(pdf: Path) -> str:
    try:
        import pymupdf
        with pymupdf.open(pdf) as doc:
            title = (doc.metadata or {}).get("title") or ""
            if len(title) < 8 and doc.page_count:
                lines = [ln.strip() for ln in doc[0].get_text().splitlines() if len(ln.strip()) > 12]
                title = lines[0] if lines else ""
            return title[:300]
    except Exception:
        return ""


def read_catalog_csv(path: Path) -> Iterable[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def write_catalog_csv(path: Path, rows: Iterable[dict[str, str]], append: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a" if append else "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CATALOG_FIELDS, extrasaction="ignore")
        if not append:
            w.writeheader()
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def upsert_reports(conn: sqlite3.Connection, rows: Iterable[dict[str, str]]) -> int:
    n = 0
    for r in rows:
        conn.execute(
            """INSERT INTO reports (report_id, source, doc_type, title, pub_date, summary, pdf_url, landing_url, local_pdf,
                                    versions)
               VALUES (:report_id, :source, :doc_type, :title, :pub_date, :summary, :pdf_url, :landing_url, :local_pdf,
                       :versions)
               ON CONFLICT(report_id) DO UPDATE SET
                 title=excluded.title, pub_date=excluded.pub_date, pdf_url=excluded.pdf_url,
                 landing_url=excluded.landing_url, doc_type=excluded.doc_type,
                 local_pdf=COALESCE(excluded.local_pdf, reports.local_pdf), versions=excluded.versions,
                 summary=CASE WHEN excluded.summary != '' THEN excluded.summary ELSE reports.summary END""",
            {**{k: r.get(k, "") or "" for k in CATALOG_FIELDS}, "local_pdf": r.get("local_pdf") or None,
             "versions": int(r.get("versions") or 1)},
        )
        n += 1
    return n


def attach_cached_pdfs(conn: sqlite3.Connection) -> None:
    """Point catalog rows at PDFs already on disk (data/sample/pdfs or data/cache/pdfs)."""
    for folder in (SAMPLE_DIR / "pdfs", CACHE_DIR):
        for pdf in folder.glob("*.pdf"):
            conn.execute("UPDATE reports SET local_pdf=? WHERE report_id=? AND local_pdf IS NULL", (rel(pdf), pdf.stem))


def build_index(conn: sqlite3.Connection, dataset: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if dataset == "sample":
        counts["sample_catalog"] = upsert_reports(conn, read_catalog_csv(SAMPLE_DIR / "catalog.csv"))
    else:
        crs_csv, gao_xml = FULL_DIR / "crs_reports.csv", FULL_DIR / "gao_reports_rss.xml"
        if not crs_csv.exists() or not gao_xml.exists():
            raise FileNotFoundError("Full dataset files are missing. Run: python scripts/build_index.py --dataset full --download")
        counts["crs"] = upsert_reports(conn, with_meta(crs_rows(crs_csv), load_crs_meta()))
        counts["gao_rss"] = upsert_reports(conn, gao_rows(gao_xml))
    counts["library"] = upsert_reports(conn, library_rows())
    attach_cached_pdfs(conn)
    conn.commit()
    counts["total"] = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    return counts


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
FTS_STOP = {"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "by", "about", "regarding",
            "data", "information", "report", "reports", "find", "me", "please", "us"}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if w.lower() not in FTS_STOP]


def fts_phrase(text: str) -> str | None:
    words = _WORD.findall(text)
    return '"' + " ".join(words) + '"' if words else None


def concept_query(synonyms: Iterable[str]) -> str:
    """A concept matches if all content words of any one synonym appear (order-free, stemmed)."""
    groups = ["(" + " AND ".join(f'"{w}"' for w in ws) + ")" for ws in (_words(t) for t in synonyms) if ws]
    return " OR ".join(dict.fromkeys(groups))


def recall_query(concepts: Iterable[Iterable[str]]) -> str:
    """Broad OR over every content word, plus exact phrases (which earn extra BM25 weight)."""
    terms: list[str] = []
    for c in concepts:
        for t in c:
            words = _words(t)
            if len(words) > 1 and (ph := fts_phrase(t)):
                terms.append(ph)
            terms += [f'"{w}"' for w in words]
    return " OR ".join(dict.fromkeys(terms))


# Series boilerplate ("...: Background and Issues for Congress") should not make two reports look alike.
TITLE_STOPWORDS = {"background", "issues", "congress", "for", "and", "the", "program", "programs", "overview",
                   "primer", "options", "policy", "update", "with", "from"}


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) > 2 and w.lower() not in TITLE_STOPWORDS}


def _recency(pub_date: str | None) -> float:
    try:
        d = date.fromisoformat((pub_date or "")[:10])
    except ValueError:
        return 0.3
    return math.exp(-max((date.today() - d).days, 0) / 365.25 / RECENCY_YEARS)


def _authority(conn: sqlite3.Connection, ids: list[str]) -> dict[str, float]:
    """PageRank-style signals, all free at query time:
    revision count (heavily maintained = canonical), citation in-links from opened reports,
    and verified findings from past cases (a click-through analog)."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    versions = dict(conn.execute(f"SELECT report_id, versions FROM reports WHERE report_id IN ({marks})", ids).fetchall())
    inlinks = dict(conn.execute(
        f"SELECT to_report, COUNT(*) FROM citations WHERE to_report IN ({marks}) GROUP BY to_report", ids).fetchall())
    verified = dict(conn.execute(
        f"SELECT report_id, COUNT(*) FROM findings WHERE status='verified' AND report_id IN ({marks}) GROUP BY report_id",
        ids).fetchall())

    def norm(d: dict[str, int], rid: str) -> float:
        top = max(d.values(), default=0)
        return math.log1p(d.get(rid, 0) or 0) / math.log1p(top) if top else 0.0

    return {rid: 0.4 * norm(versions, rid) + 0.35 * norm(inlinks, rid) + 0.25 * norm(verified, rid) for rid in ids}


def _tier_of(age_years: float) -> str:
    return next(name for name, lo, hi in TIERS if lo <= age_years < hi)


def _tier_dates(lo: float, hi: float) -> tuple[str, str]:
    """[oldest, newest) publication-date bounds for a tier, as ISO strings."""
    today = date.today()
    newest = today.replace(year=today.year - int(lo)).isoformat() if lo else "9999-12-31"
    oldest = today.replace(year=today.year - int(hi)).isoformat() if hi < 1000 else "0000-01-01"
    return oldest, newest


def rank_reports(
    conn: sqlite3.Connection,
    concepts: list[list[str]],
    limit: int = 5,
    exclude_ids: Iterable[str] = (),
    sources: Iterable[str] = (),
    published_after: str | None = None,
    prefer_recent: bool = True,
    tier: str = "auto",
) -> dict[str, Any]:
    """Rank catalog reports for a request expressed as concepts (each a list of synonyms).

    Newest to oldest: candidates are recalled separately inside each date tier (so older,
    title-only reports are never crowded out), then results are filled from the newest tier
    that has strong topical matches, moving to older tiers only once newer strong matches run out.
    """
    weights = WEIGHTS if prefer_recent else HISTORICAL_WEIGHTS
    concepts = [[t for t in c if _words(t)] for c in concepts]
    concepts = [c for c in concepts if c]
    if not concepts:
        return {"ok": False, "error": "Give at least one concept with a searchable term."}
    exclude = {e.upper() for e in exclude_ids}
    srcs = {s.upper() for s in sources}
    query = recall_query(concepts)

    # 1. Recall per tier (title-only older reports compete only with reports of their own era).
    cands: list[dict[str, Any]] = []
    for name, lo, hi in TIERS:
        oldest, newest = _tier_dates(lo, hi)
        rows = conn.execute(
            """SELECT r.report_id, r.source, r.doc_type, r.title, r.pub_date, r.summary, r.local_pdf, r.pdf_url,
                      bm25(reports_fts, 8.0, 2.0) AS bm
               FROM reports_fts JOIN reports r ON r.rowid = reports_fts.rowid
               WHERE reports_fts MATCH ? AND COALESCE(r.pub_date, '') >= ? AND COALESCE(r.pub_date, '') < ?
               ORDER BY bm LIMIT ?""",
            (query, oldest, newest, CANDIDATES_PER_TIER * 2),
        ).fetchall()
        cands += [dict(r) for r in rows
                  if r["report_id"].upper() not in exclude
                  and (not srcs or r["source"].upper() in srcs)
                  and (not published_after or (r["pub_date"] or "") >= published_after)][:CANDIDATES_PER_TIER]
    if not cands:
        return {"ok": True, "query": query, "results": [], "candidates_considered": 0,
                "hint": "No catalog match. Try broader synonyms or fewer, more general concepts."}

    # 2. Features. IDF-weighted concept coverage: which concepts does each candidate match, and where?
    total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] or 1
    ids = {c["report_id"] for c in cands}
    concept_hits, title_hits, concept_w = [], [], []
    for c in concepts:
        q = concept_query(c)
        hit = {r[0] for r in conn.execute("SELECT report_id FROM reports_fts WHERE reports_fts MATCH ?", (q,))}
        in_title = {r[0] for r in conn.execute("SELECT report_id FROM reports_fts WHERE reports_fts MATCH ?",
                                               (f"title : ({q})",))}
        concept_w.append(math.log(1 + total / (1 + len(hit))))
        concept_hits.append(hit & ids)
        title_hits.append(in_title & ids)
    w_sum = sum(concept_w) or 1.0

    best = {}  # BM25 is normalized within each tier, so eras with longer abstracts don't dominate
    for c in cands:
        c["age_years"] = _age_years(c["pub_date"])
        c["tier"] = _tier_of(c["age_years"])
        best[c["tier"]] = max(best.get(c["tier"], 0.0), -c["bm"])
    auth = _authority(conn, list(ids))
    topic_w = {k: v for k, v in weights.items() if k != "recency"}
    for c in cands:
        matched = [i for i, h in enumerate(concept_hits) if c["report_id"] in h]
        parts = {
            "relevance": -c["bm"] / (best[c["tier"]] or 1.0),
            # A title match is the strong signal; an abstract-only match counts 0.65 (and many newer
            # reports have no abstract, so this also keeps abstracts from favoring older reports).
            "coverage": sum(concept_w[i] * (1.0 if c["report_id"] in title_hits[i] else SUMMARY_MATCH_WEIGHT)
                            for i in matched) / w_sum,
            "depth": DEPTH_WEIGHT.get(c["doc_type"] or "", 0.6),
            "recency": _recency(c["pub_date"]),
            "authority": auth.get(c["report_id"], 0.0),
        }
        c["parts"] = parts
        c["matched_concepts"] = ["/".join(concepts[i][:2]) + ("/..." if len(concepts[i]) > 2 else "") for i in matched]
        c["topic"] = sum(topic_w[k] * parts[k] for k in topic_w) / sum(topic_w.values())  # how well it fits, any era
        c["score"] = sum(weights[k] * v for k, v in parts.items())
    if prefer_recent:
        _penalize_superseded(cands)

    # 3. Newest to oldest. "Strong" = topical fit within STRONG_FRACTION of the best fit in any era.
    top_topic = max(c["topic"] for c in cands)
    for c in cands:
        c["strong"] = c["topic"] >= STRONG_FRACTION * top_topic
    order = [name for name, _, _ in TIERS]
    if tier != "auto":
        groups = [[c for c in cands if c["tier"] == tier]]
    elif not prefer_recent:
        groups = [cands]
    else:  # strong matches tier by tier (newest first), then everything else by score
        groups = [[c for c in cands if c["strong"] and c["tier"] == name] for name in order]
        groups.append([c for c in cands if not c["strong"]])
    picked: list[dict[str, Any]] = []
    limit = max(1, min(limit, 5))
    for group in groups:
        pool = sorted(group, key=lambda c: c["score"], reverse=True)
        while pool and len(picked) < limit:
            def mmr(c: dict[str, Any]) -> float:  # maximal marginal relevance: skip near-duplicate series
                sim = max((_jaccard(c["title"], p["title"]) for p in picked), default=0.0)
                return c["score"] - MMR_PENALTY * sim
            nxt = max(pool, key=mmr)
            picked.append(nxt)
            pool.remove(nxt)

    chosen = {c["report_id"] for c in picked}
    tiers_info = []
    for name, lo, hi in TIERS:
        in_tier = [c for c in cands if c["tier"] == name]
        tiers_info.append({
            "tier": name, "ages": f"{lo:g}-{hi:g} yrs" if hi < 1000 else f"{lo:g}+ yrs",
            "candidates": len(in_tier), "strong_matches": sum(c["strong"] for c in in_tier),
            "strong_left_after_these_results": sum(c["strong"] and c["report_id"] not in chosen for c in in_tier),
        })
    newer_left = next((t for t in tiers_info if t["strong_left_after_these_results"]), None)
    return {
        "ok": True,
        "query": query,
        "candidates_considered": len(cands),
        "results": [_result(c) for c in picked],
        "newest_to_oldest": {
            "tiers": tiers_info,
            "advice": (f"Strong matches remain in the '{newer_left['tier']}' tier; searching again continues there "
                       "(reports already shortlisted are skipped)." if newer_left else
                       "All strong matches across every era are in these results."),
        },
    }


# --------------------------------------------------------------------------- #
# Priority lane: what matters most, alongside what is newest
# --------------------------------------------------------------------------- #
MAX_PRIORITIES = 4
PRIORITY_MIN_FIT = 0.5     # IDF-weighted share of a priority's words that the report must match
PRIORITY_FIT_BAND = 0.1    # only reports within this of the best fit compete (a full title match beats a partial one)
PRIORITY_WEIGHTS = {"fit": 0.20, "authority": 0.30, "topic": 0.20, "recency": 0.30}


def rank_priorities(
    conn: sqlite3.Connection,
    concepts: list[list[str]],
    priorities: list[str],
    exclude_ids: Iterable[str] = (),
    sources: Iterable[str] = (),
    taken: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """For each priority the planning agent named (the largest or most consequential programs, laws, or
    updates behind the question, most important first), find the single best report about it.

    Fit = IDF-weighted share of the priority's words in the report title (an abstract-only match counts
    0.65), so 'Virginia-class submarine' finds the Navy submarine report, not a West Virginia one. Among
    reports that fit best, the most-maintained and most recent one wins: CRS revises its reports on major
    programs far more often (in the sample, 137-204 revisions for the carrier, submarine, destroyer, and
    shipbuilding-plan reports vs 31-71 for small programs). Topic coverage also counts. Zero model tokens.
    Each priority gets a different report when one fits; `taken` (e.g. the newest-lane results) is only
    reused, as `covered_by`, when nothing else fits. Returns one entry per priority:
    {"priority", "result" (or None), "covered_by" (or None), "fit"}.
    """
    taken = {t.upper() for t in taken}
    exclude = {e.upper() for e in exclude_ids}
    srcs = {s.upper() for s in sources}
    total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] or 1
    concepts = [[t for t in c if _words(t)] for c in concepts]
    concept_sets = []
    for c in (c for c in concepts if c):
        q = concept_query(c)
        hit = {r[0] for r in conn.execute("SELECT report_id FROM reports_fts WHERE reports_fts MATCH ?", (q,))}
        concept_sets.append((math.log(1 + total / (1 + len(hit))), hit))
    c_sum = sum(w for w, _ in concept_sets) or 1.0

    out: list[dict[str, Any]] = []
    for p in list(dict.fromkeys(priorities))[:MAX_PRIORITIES]:
        words = list(dict.fromkeys(w.lower() for w in _words(p)))
        if not words:
            continue
        idf, in_title, anywhere = {}, {}, {}
        for w in words:
            ids = {r[0] for r in conn.execute("SELECT report_id FROM reports_fts WHERE reports_fts MATCH ?", (f'"{w}"',))}
            idf[w] = math.log(1 + total / (1 + len(ids)))
            anywhere[w] = ids
            in_title[w] = {r[0] for r in conn.execute("SELECT report_id FROM reports_fts WHERE reports_fts MATCH ?",
                                                      (f'title : "{w}"',))}
        w_sum = sum(idf.values()) or 1.0
        cand_ids = set().union(*in_title.values()) - exclude
        fits = {}
        for rid in cand_ids:
            fit = sum(idf[w] * (1.0 if rid in in_title[w] else SUMMARY_MATCH_WEIGHT if rid in anywhere[w] else 0.0)
                      for w in words) / w_sum
            if fit >= PRIORITY_MIN_FIT:
                fits[rid] = fit
        if not fits:
            out.append({"priority": p, "result": None, "covered_by": None, "fit": 0.0})
            continue
        # A hand-added library PDF is unvetted: it can't take a priority slot from a catalog report that also fits
        # (catalog reports carry their official PDF or landing-page link). It can still surface in the newest lane.
        # Found by the red team: a planted "CRS report" titled to match a likely priority took the slot.
        marks = ",".join("?" * len(fits))
        official = {r[0] for r in conn.execute(
            f"SELECT report_id FROM reports WHERE report_id IN ({marks}) AND (COALESCE(pdf_url, '') != '' "
            f"OR COALESCE(landing_url, '') != '')", list(fits))}
        fits = {rid: f for rid, f in fits.items() if rid in official} or fits
        top_fit = max(fits.values())
        fits = {rid: f for rid, f in fits.items() if f >= top_fit - PRIORITY_FIT_BAND}
        marks = ",".join("?" * len(fits))
        rows = [dict(r) for r in conn.execute(
            f"""SELECT report_id, source, doc_type, title, pub_date, summary, local_pdf, pdf_url
                FROM reports WHERE report_id IN ({marks})""", list(fits)).fetchall()]
        rows = [r for r in rows if not srcs or r["source"].upper() in srcs]
        if not rows:
            out.append({"priority": p, "result": None, "covered_by": None, "fit": 0.0})
            continue
        auth = _authority(conn, [r["report_id"] for r in rows])
        for r in rows:
            topic = sum(w for w, hit in concept_sets if r["report_id"] in hit) / c_sum
            r["age_years"], r["tier"] = _age_years(r["pub_date"]), _tier_of(_age_years(r["pub_date"]))
            r["parts"] = {"fit": fits[r["report_id"]], "authority": auth.get(r["report_id"], 0.0), "topic": topic,
                          "recency": _recency(r["pub_date"])}
            r["score"] = sum(PRIORITY_WEIGHTS[k] * v for k, v in r["parts"].items())
            r["matched_concepts"] = [f"priority: {p}"]
        rows.sort(key=lambda r: r["score"], reverse=True)
        fresh = [r for r in rows if r["report_id"].upper() not in taken]
        if fresh:
            best = fresh[0]
            taken.add(best["report_id"].upper())
            out.append({"priority": p, "result": _result(best), "covered_by": None, "fit": round(best["parts"]["fit"], 2)})
        else:
            out.append({"priority": p, "result": None, "covered_by": rows[0]["report_id"],
                        "fit": round(rows[0]["parts"]["fit"], 2)})
    return out


def _age_years(pub_date: str | None) -> float:
    try:
        return round(max((date.today() - date.fromisoformat((pub_date or "")[:10])).days, 0) / 365.25, 1)
    except ValueError:
        return 99.0


def _penalize_superseded(cands: list[dict[str, Any]]) -> None:
    """An older report loses ground to a newer one in the same series that covers the same concepts."""
    by_date = sorted(cands, key=lambda c: c["pub_date"] or "", reverse=True)
    for i, old in enumerate(by_date):
        for new in by_date[:i]:
            if (new["pub_date"] or "") > (old["pub_date"] or "") and new["parts"]["coverage"] >= old["parts"]["coverage"] \
                    and _jaccard(new["title"], old["title"]) >= 0.35:
                old["score"] -= SUPERSEDED_PENALTY
                old["superseded_by"] = new["report_id"]
                break


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def _result(c: dict[str, Any]) -> dict[str, Any]:
    p = c["parts"]
    return {
        "report_id": c["report_id"],
        "title": c["title"],
        "source": c["source"],
        "doc_type": c["doc_type"],
        "pub_date": c["pub_date"],
        "age_years": c["age_years"],
        **({"superseded_by": c["superseded_by"]} if c.get("superseded_by") else {}),
        "tier": c["tier"],
        "summary": (c["summary"] or "")[:240],
        "pdf_available": bool(c["local_pdf"] or c["pdf_url"]),
        "score": round(c["score"], 3),
        "why_ranked": (f"concepts matched: {', '.join(c['matched_concepts']) or 'none'}; "
                       + ", ".join(f"{k} {v:.2f}" for k, v in p.items())),
    }


# --------------------------------------------------------------------------- #
# Learning from opened reports (makes later searches better, at zero token cost)
# --------------------------------------------------------------------------- #
def record_citations(conn: sqlite3.Connection, report_id: str, full_text: str) -> int:
    found = {m.upper() for m in GAO_ID.findall(full_text)} | set(CRS_ID.findall(full_text))
    found.discard(report_id.upper())
    if not found:
        return 0
    marks = ",".join("?" * len(found))
    known = [r[0] for r in conn.execute(f"SELECT report_id FROM reports WHERE report_id IN ({marks})", list(found))]
    conn.executemany("INSERT OR IGNORE INTO citations VALUES (?, ?)", [(report_id, k) for k in known])
    return len(known)

