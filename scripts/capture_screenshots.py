"""Capture dashboard screenshots for the report while a real question runs end to end.

  pip install playwright                     # optional; drives your installed Google Chrome
  uvicorn backend.main:app --port 8010       # in another terminal
  python scripts/capture_screenshots.py "How much have first-of-class Navy ships overrun their cost estimates?"

Saves JPEGs to docs/screenshots/: the home page (with the workflow), one frame per workflow step
(plan, quick pass, full team), the answer with "How this answer was made", an evidence card, the
highlighted-page lightbox, the research plan, Behind the scenes, and How it works.
--prefix ships_ names the files ships_01_home.jpg, ... so a second walkthrough can sit beside the first.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"
URL = "http://127.0.0.1:8010"


PREFIX = ""
PHASES = ["plan", "quick", "team"]


def shot(page: Page, name: str, locator: str | None = None) -> None:
    target = page.locator(locator).first if locator else page
    target.screenshot(path=str(OUT / f"{PREFIX}{name}.jpg"), type="jpeg", quality=88)
    print("saved", PREFIX + name)


def capture_answer(page: Page) -> None:
    page.wait_for_selector(".card.answer, .card.s-none", timeout=600_000)
    if not page.locator(".card.answer").count():
        raise SystemExit("The run did not finish with an answer; nothing captured.")
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo(0, 0)")
    shot(page, "05_answer")
    page.locator(".evidence").first.scroll_into_view_if_needed()
    page.wait_for_timeout(800)
    shot(page, "06_evidence_card", ".evidence")
    page.locator(".thumb").first.click()
    page.wait_for_timeout(800)
    shot(page, "07_lightbox", ".lightbox")
    page.keyboard.press("Escape")
    page.mouse.click(5, 5)
    page.wait_for_timeout(400)
    page.locator(".tabs button", has_text="Behind the scenes").click()
    page.wait_for_timeout(800)
    page.evaluate("window.scrollTo(0, 0)")
    shot(page, "08_behind_the_scenes")
    if page.locator(".card", has_text="Research plan").count():
        page.locator(".card", has_text="Research plan").first.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        shot(page, "08b_research_plan", ".card:has-text('Research plan')")
    page.locator(".card", has_text="Full activity log").first.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    shot(page, "09_activity_log", ".card:has-text('Full activity log')")
    page.locator(".top-actions .link-button").click()
    page.wait_for_timeout(600)
    shot(page, "10_how_it_works", ".modal")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="How much have first-of-class Navy ships overrun their cost estimates?")
    ap.add_argument("--existing", action="store_true",
                    help="don't ask: open the newest past question with this text and capture its answer screens")
    ap.add_argument("--prefix", default="", help="file-name prefix, e.g. ships_")
    ap.add_argument("--mode", choices=["auto", "lean", "team"], default="auto", help="mode to pick before asking")
    args = ap.parse_args()
    global PREFIX
    PREFIX = args.prefix
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1360, "height": 860}, device_scale_factor=1.5)
        page.goto(URL)
        page.wait_for_selector(".hero")
        if args.existing:
            page.locator(".history-row", has_text=args.question[:40]).first.click()
            capture_answer(page)
            browser.close()
            return
        page.fill(".ask-box textarea", args.question)
        page.locator(f".mode:has(input[value='{args.mode}'])").click()
        page.wait_for_timeout(300)
        shot(page, "01_home")
        page.click(".ask-foot .primary")
        page.wait_for_selector(".progress", timeout=30_000)
        seen: set[int] = set()
        opened = False
        start = time.time()
        while time.time() - start < 600:
            if page.locator(".card.answer, .card.s-none").count():
                break
            phases = page.locator(".progress .phase:not(.answer)")
            active = next((i for i in range(phases.count()) if "active" in (phases.nth(i).get_attribute("class") or "")), None)
            if active is not None and active not in seen:
                page.wait_for_timeout(1200 if active == 0 else 2500)  # let the "Now:" line and feed catch up
                if active >= 1 and not opened:
                    page.locator(".activity summary").click()
                    opened = True
                    page.wait_for_timeout(400)
                page.evaluate("window.scrollTo(0, 0)")
                shot(page, f"02_progress_{active + 1}_{PHASES[active]}")
                seen.add(active)
            page.wait_for_timeout(500)
        capture_answer(page)
        browser.close()


if __name__ == "__main__":
    main()
