"""Full-page screenshots of the real web pages used in the video, plus y offsets of the sections we scroll to.

usage: uv run --with playwright python capture_pages.py OUT_DIR
Viewport 1600 CSS px wide at 1.2x -> 1920 px images. Writes OUT_DIR/<name>.png, OUT_DIR/<name>__<anchor>.png and OUT_DIR/anchors.json.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = {
    "mcp": ("https://modelcontextprotocol.io/docs/getting-started/intro", ["page-title", "what-can-mcp-enable"]),
    "fastmcp": ("https://gofastmcp.com/getting-started/welcome", ["page-title"]),
    "tools": ("https://gofastmcp.com/servers/tools", ["the-@tool-decorator"]),
}


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    anchors = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.2)
        for name, (url, ids) in PAGES.items():
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            ys = {}
            for i in ids:
                y = page.evaluate("id => { const e = document.getElementById(id); return e ? e.getBoundingClientRect().top + window.scrollY : null }", i)
                ys[i] = None if y is None else round(y * 1.2)
            anchors[name] = {"url": url, "ys": ys}
            if ids:  # long pages: one 2400 CSS px slice per section instead of the whole page
                page.screenshot(path=str(out / f"{name}.png"), timeout=120000)
                page.set_viewport_size({"width": 1600, "height": 2400})
                for i in ids:
                    top = max(0, ys[i] / 1.2 - 120) if ys[i] is not None else 0
                    page.evaluate("y => window.scrollTo(0, y)", top)
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(out / f"{name}__{i}.png"), timeout=120000)
            else:
                page.screenshot(path=str(out / f"{name}.png"), full_page=True, timeout=120000)
            print(name, ys)
            page.close()
        browser.close()
    (out / "anchors.json").write_text(json.dumps(anchors, indent=1))


if __name__ == "__main__":
    main()
