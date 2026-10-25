"""Build slides.html (reveal.js from a CDN; screenshots and diagram embedded) from docs/slides_template.html.

  python scripts/build_slides.py

Numbers come from the same metrics() as report.html, so the two files always agree.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_report import fill, metrics  # noqa: E402


def main() -> None:
    M = metrics()
    report = (ROOT / "docs" / "report_template.html").read_text(encoding="utf-8")
    start = report.index('<svg class="diagram"')
    M["diagram"] = report[start:report.index("</svg>", start) + len("</svg>")]  # the same diagram as the report
    fill(ROOT / "docs" / "slides_template.html", ROOT / "slides.html", M)


if __name__ == "__main__":
    main()
