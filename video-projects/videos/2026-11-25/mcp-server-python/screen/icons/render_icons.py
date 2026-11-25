"""Rasterize the Lucide SVGs in svg/ to white 256 px transparent PNGs (png/). usage: uv run --with playwright python render_icons.py"""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
(HERE / "png").mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    page = b.new_page(viewport={"width": 256, "height": 256})
    for svg in sorted((HERE / "svg").glob("*.svg")):
        body = svg.read_text().replace('width="24"', 'width="256"').replace('height="24"', 'height="256"')
        body = body.replace('stroke="currentColor"', 'stroke="#ffffff"').replace('stroke-width="2"', 'stroke-width="1.6"')
        page.set_content(f"<html><body style='margin:0;background:transparent'>{body}</body></html>")
        page.locator("svg").screenshot(path=str(HERE / "png" / f"{svg.stem}.png"), omit_background=True)
    b.close()
