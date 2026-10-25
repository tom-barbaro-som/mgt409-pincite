"""Build (or rebuild) the report index for a dataset.

  python scripts/build_index.py                          # sample dataset (offline, committed files)
  python scripts/build_index.py --dataset full --download  # full CRS index + GAO RSS (downloads ~7 MB)
  python scripts/build_index.py --make-sample            # maintainer: regenerate data/sample from the full CRS index

The index lives in data/index_<dataset>.db. --rebuild deletes it first (case history is lost;
the audit trail is not). PDFs in data/library/ are always included.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE_PDFS = ["R44972", "RL32109", "RL32665", "RL32418"]  # cached for offline demos (Navy shipbuilding)
# Multi-domain sample: the policy areas the app is tested on (see docs/evals.md).
SAMPLE_DOMAINS = {
    "defense": r"navy|naval|ship|defense|military|army|air force|marine|coast guard|weapon|pentagon|\bdod\b",
    "health": r"health|medicare|medicaid|hospital|drug|insurance|affordable care|\baca\b|physician|opioid|pandemic|"
              r"covid|public health|\bnih\b|\bfda\b",
    "budget_tax": r"budget|appropriation|deficit|debt|tax|revenue|spending|fiscal|\bcbo\b|sequest",
    "energy_env": r"energy|oil|gas|electric|climate|emission|environment|\bepa\b|water|nuclear|renewable",
    "education_labor": r"education|student|school|college|workforce|labor|employment|wage|pension|social security",
    "immigration": r"immigra|border|visa|asylum|refugee|citizenship",
    "infrastructure": r"highway|transit|transportation|infrastructure|aviation|rail|broadband|housing",
}
SAMPLE_TOPICS = re.compile("|".join(f"(?:{p})" for p in SAMPLE_DOMAINS.values()), re.I)
SAMPLE_RECENT_OTHERS = 800  # unrelated recent reports kept as distractors so ranking is a real test


def download(url: str, dest: Path) -> None:
    import httpx
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, headers={"User-Agent": "gov-report-research-team/0.1 (MGT 409 class project)"}) as c:
        r = c.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    print(f"downloaded {url} -> {dest.relative_to(ROOT)} ({dest.stat().st_size:,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["sample", "full"], default=os.environ.get("DATASET", "sample"))
    ap.add_argument("--download", action="store_true", help="(full) fetch the CRS index and GAO RSS feed")
    ap.add_argument("--rebuild", action="store_true", help="delete the existing index first")
    ap.add_argument("--make-sample", action="store_true", help="regenerate data/sample/ from the full CRS index")
    args = ap.parse_args()
    os.environ["DATASET"] = args.dataset

    from mcp_server import catalog
    from mcp_server.server import index_local_pdfs
    from mcp_server.store import DB_PATH, FULL_DIR, SAMPLE_DIR, connect

    if args.download or args.make_sample:
        download(catalog.EVERYCRS_INDEX_URL, FULL_DIR / "crs_reports.csv")
        download(catalog.GAO_RSS_URL, FULL_DIR / "gao_reports_rss.xml")

    if args.make_sample:
        rows = list(catalog.crs_rows(FULL_DIR / "crs_reports.csv"))
        topical = [r for r in rows if SAMPLE_TOPICS.search(r["title"])]
        others = sorted((r for r in rows if not SAMPLE_TOPICS.search(r["title"])),
                        key=lambda r: r["pub_date"], reverse=True)[:SAMPLE_RECENT_OTHERS]
        gao = list(catalog.gao_rows(FULL_DIR / "gao_reports_rss.xml"))
        meta = catalog.load_crs_meta()
        n = catalog.write_catalog_csv(SAMPLE_DIR / "catalog.csv", catalog.with_meta(topical + others, meta))
        n += catalog.write_catalog_csv(SAMPLE_DIR / "catalog.csv", gao, append=True)
        print(f"wrote data/sample/catalog.csv ({n:,} reports: {len(topical)} topical, {len(others)} distractors, "
              f"{len(gao)} GAO RSS; crawled metadata for {sum(r['report_id'] in meta for r in topical + others):,})")
        by_id = {r["report_id"]: r for r in rows}
        for rid in SAMPLE_PDFS:
            dest = SAMPLE_DIR / "pdfs" / f"{rid}.pdf"
            if not dest.exists():
                download(by_id[rid]["pdf_url"], dest)

    if args.rebuild:
        for suffix in ("", "-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
    conn = connect(DB_PATH)
    counts = catalog.build_index(conn, args.dataset)
    counts["pdfs_indexed"] = index_local_pdfs(conn)
    print(f"index {DB_PATH.relative_to(ROOT)}: {counts}")


if __name__ == "__main__":
    main()
