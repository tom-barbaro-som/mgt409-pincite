"""Offline crawl of EveryCRSReport per-report metadata: abstracts + revision counts.

  python scripts/enrich_catalog.py                 # reports in data/sample/catalog.csv
  python scripts/enrich_catalog.py --all           # the entire CRS index (~23.5k, ~15 min)
  python scripts/enrich_catalog.py --html          # 2nd pass: summaries for reports whose metadata has none
                                                   # (mostly 2018+), read from the top of the HTML version

Writes data/full/crs_meta.jsonl (resumable: already-crawled ids are skipped). Run
`python scripts/build_index.py --make-sample --rebuild` (or the full build) afterwards
to fold the metadata into the catalog. This is indexing, done once, and costs no model tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html as htmllib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from mcp_server import catalog  # noqa: E402
from mcp_server.store import FULL_DIR, SAMPLE_DIR  # noqa: E402

CONCURRENCY = 8  # polite to a volunteer-run static site
HTML_BYTES = 160_000  # stop reading each HTML file after this much (summaries are at the top)


def html_summary(raw: str) -> str:
    """The report's Summary section, or (In Focus/Insight products) its opening text."""
    raw = re.sub(r"<img[^>]*>|<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    text = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
    m = re.search(r"\bSummary\b\s*(.{80,})", text)
    body = m.group(1) if m else re.sub(r"^.{0,200}?\((?:[A-Z]{1,3}\d{4,5})\)\s*", "", text)
    return body[:catalog.SUMMARY_CHARS]


async def fetch_html(client: httpx.AsyncClient, rid: str, path: str, versions: int, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        try:
            buf = bytearray()
            async with client.stream("GET", catalog.EVERYCRS_BASE + path) as r:
                if r.status_code != 200:
                    return None
                async for chunk in r.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= HTML_BYTES:
                        break
            summary = html_summary(buf.decode("utf-8", errors="ignore"))
            return {"report_id": rid, "summary": summary, "versions": versions, "summary_source": "html"} if summary else None
        except httpx.HTTPError:
            return None


async def fetch(client: httpx.AsyncClient, rid: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        for attempt in range(3):
            try:
                r = await client.get(f"{catalog.EVERYCRS_BASE}reports/{rid}.json")
                if r.status_code == 404:
                    return {"report_id": rid, "summary": "", "versions": 1}
                r.raise_for_status()
                d = r.json()
                versions = d.get("versions") or []
                summary = next((v.get("summary") for v in versions if v.get("summary")), "") or ""
                return {"report_id": rid, "summary": " ".join(summary.split()), "versions": len(versions) or 1}
            except (httpx.HTTPError, ValueError):
                await asyncio.sleep(1 + attempt)
        return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="crawl the whole CRS index, not just the sample")
    ap.add_argument("--html", action="store_true", help="fill missing summaries from each report's HTML version")
    args = ap.parse_args()
    if args.html:
        await html_pass(args.all)
        return
    source = FULL_DIR / "crs_reports.csv"
    ids = ([r["report_id"] for r in catalog.crs_rows(source)] if args.all
           else [r["report_id"] for r in catalog.read_catalog_csv(SAMPLE_DIR / "catalog.csv") if r["source"] == "CRS"])
    done = set(catalog.load_crs_meta())
    todo = [i for i in dict.fromkeys(ids) if i not in done]
    print(f"{len(ids):,} reports, {len(done):,} already crawled, {len(todo):,} to fetch")
    sem = asyncio.Semaphore(CONCURRENCY)
    FULL_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "gov-report-research-team/0.1 (MGT 409 class project)"}) as client:
        with catalog.CRS_META_FILE.open("a", encoding="utf-8") as out:
            for i in range(0, len(todo), 200):
                batch = await asyncio.gather(*(fetch(client, rid, sem) for rid in todo[i:i + 200]))
                for m in batch:
                    if m:
                        out.write(json.dumps(m) + "\n")
                out.flush()
                print(f"  {min(i + 200, len(todo)):,}/{len(todo):,}")
    meta = catalog.load_crs_meta()
    with_summary = sum(1 for i in ids if meta.get(i, {}).get("summary"))
    print(f"done: {with_summary:,}/{len(ids):,} have abstracts")


async def html_pass(everything: bool) -> None:
    source = FULL_DIR / "crs_reports.csv"
    with source.open(encoding="utf-8", newline="") as f:
        html_paths = {r["number"]: r["latestHTML"] for r in csv.DictReader(f) if r.get("latestHTML")}
    wanted = (set(html_paths) if everything
              else {r["report_id"] for r in catalog.read_catalog_csv(SAMPLE_DIR / "catalog.csv") if r["source"] == "CRS"})
    meta = catalog.load_crs_meta()
    todo = [rid for rid in wanted if rid in html_paths and not meta.get(rid, {}).get("summary")
            and meta.get(rid, {}).get("summary_source") != "html"]
    print(f"{len(todo):,} reports without a summary; reading their HTML heads")
    sem = asyncio.Semaphore(CONCURRENCY)
    got = 0
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "gov-report-research-team/0.1 (MGT 409 class project)"}) as client:
        with catalog.CRS_META_FILE.open("a", encoding="utf-8") as out:
            for i in range(0, len(todo), 200):
                batch = await asyncio.gather(*(fetch_html(client, rid, html_paths[rid], meta.get(rid, {}).get("versions", 1), sem)
                                               for rid in todo[i:i + 200]))
                for m in batch:
                    if m:
                        out.write(json.dumps(m) + "\n")
                        got += 1
                out.flush()
                print(f"  {min(i + 200, len(todo)):,}/{len(todo):,} ({got:,} summaries)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
