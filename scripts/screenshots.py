"""Capture README screenshots from the running app with Playwright.

Start the server first (any provider), then:

    python scripts/screenshots.py --upload data/samples/inv-002-banded.pdf --mode text
    python scripts/screenshots.py --upload data/samples/po-017-banded-scan.png --mode vision \
        --name vision-scan

Each upload goes through the real UI (file input + Extract button), so the
screenshot shows a real extraction from whichever provider the server uses.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "docs" / "screenshots"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4102")
    parser.add_argument("--upload", type=Path, help="Sample to extract through the UI")
    parser.add_argument("--mode", default="", choices=["", "auto", "text", "vision"])
    parser.add_argument("--open-id", type=int, help="Show a stored document instead of uploading")
    parser.add_argument("--name", default="extraction-result")
    parser.add_argument("--timeout-min", type=float, default=30)
    parser.add_argument("--channel", default="chrome",
                        help="Browser channel; 'chrome' uses the installed Google Chrome, "
                             "'' uses Playwright's own Chromium")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.goto(args.base_url)
        page.wait_for_selector("#doc-rows tr")
        if args.upload:
            page.set_input_files("#file", str(args.upload))
            if args.mode:
                page.select_option("#mode", args.mode)
            page.click("#submit")
            page.wait_for_selector("#result:not(.hidden)", timeout=args.timeout_min * 60_000)
        elif args.open_id:
            page.click(f'#doc-rows tr[data-id="{args.open_id}"]')
            page.wait_for_selector("#result:not(.hidden)")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)  # let the page image decode
        target = OUT / f"{args.name}.png"
        page.screenshot(path=str(target), full_page=True)
        print(f"wrote {target.relative_to(REPO_ROOT)}")
        browser.close()


if __name__ == "__main__":
    main()
