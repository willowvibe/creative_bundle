"""Brand capture from a product page (P4.5)."""

from __future__ import annotations

import httpx
import pytest
from video_editor_core.brand_capture import BrandCaptureError, capture_brand

PAGE = """
<html><head>
<title>Acme Tools</title>
<meta property="og:site_name" content="Acme Co">
<meta property="og:image" content="/assets/og.png">
<link rel="icon" href="/favicon.ico">
<style>
  body { font-family: "Inter", sans-serif; color: #112233; }
  .accent { background: rgb(200, 10, 20); font-family: Inter, Georgia; }
</style>
</head><body>Hello</body></html>
"""


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://93.184.216.34")


def test_capture_reads_name_colours_fonts_and_images() -> None:
    capture = capture_brand("https://93.184.216.34/", client=_client())
    assert capture.name == "Acme Co"
    assert "#112233" in capture.colors
    assert any(colour.startswith("rgb(200") for colour in capture.colors)
    assert capture.fonts[0] == "Inter"
    assert capture.fonts[1] == "Georgia"
    assert capture.logo_url == "https://93.184.216.34/favicon.ico"
    assert capture.og_image_url == "https://93.184.216.34/assets/og.png"


def test_a_page_without_brand_data_is_refused() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<p>hi</p>")),
        base_url="https://93.184.216.34",
    )
    with pytest.raises(BrandCaptureError, match="no colours"):
        capture_brand("https://93.184.216.34/", client=client)


def test_a_private_host_is_refused() -> None:
    with pytest.raises(BrandCaptureError, match="non-public"):
        capture_brand("http://127.0.0.1/", client=_client())


def test_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(BrandCaptureError, match="must be http"):
        capture_brand("file:///etc/passwd")
