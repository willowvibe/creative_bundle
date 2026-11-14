"""Capture a brand's public surface from a URL (P4.5).

This is the measured half of brand capture: fetch the page, read the icon,
OG image, palette and font-family declarations, and return them as a profile
the operator confirms. Computed CSS and full-page screenshots would need a
browser; the HTML declarations are enough to seed a `BrandProfile`.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

__all__ = ["BrandCapture", "BrandCaptureError", "capture_brand"]

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB = re.compile(r"rgba?\([^)]+\)")
_FONT = re.compile(r"font-family\s*:\s*([^;}\"']+)", re.IGNORECASE)
_ICON_LINK = re.compile(r"<link\b[^>]*\brel=[\"'][^\"']*icon[^\"']*[\"'][^>]*>", re.IGNORECASE)
_OG_IMAGE = re.compile(
    r"<meta\b[^>]*\bproperty=[\"']og:image[\"'][^>]*\bcontent=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_OG_SITE = re.compile(
    r"<meta\b[^>]*\bproperty=[\"']og:site_name[\"'][^>]*\bcontent=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HREF = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
_GENERIC_FONTS = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "inherit",
    "initial",
}


class BrandCaptureError(RuntimeError):
    """The page could not be fetched or produced no usable brand data."""


@dataclass(frozen=True)
class BrandCapture:
    url: str
    name: str
    colors: tuple[str, ...]
    fonts: tuple[str, ...]
    logo_url: str = ""
    og_image_url: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "name": self.name,
            "colors": list(self.colors),
            "fonts": list(self.fonts),
            "logo_url": self.logo_url,
            "og_image_url": self.og_image_url,
        }


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise BrandCaptureError(f"brand URL must be http(s), got {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise BrandCaptureError("brand URL has no host")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as exc:
        raise BrandCaptureError(f"brand host {host!r} cannot be resolved: {exc}") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise BrandCaptureError(f"brand host {host!r} resolves to a non-public address")


def _normalise_colour(value: str) -> str:
    colour = value.lower()
    if colour.startswith("#") and len(colour) == 4:
        return "#" + "".join(char * 2 for char in colour[1:])
    return colour


def _first_attr(tag: str, name: str) -> str:
    match = re.search(rf"\b{name}=[\"']([^\"']+)[\"']", tag, re.IGNORECASE)
    return match.group(1) if match else ""


def capture_brand(
    url: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 15.0,
) -> BrandCapture:
    """Fetch ``url`` and extract the logo, palette and font families."""
    _assert_public_url(url)
    owns_client = client is None
    http = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "video-editor brand-capture/1.0"},
    )
    try:
        response = http.get(url)
        response.raise_for_status()
        html = response.text
    except httpx.HTTPError as exc:
        raise BrandCaptureError(f"could not fetch {url}: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    base = str(response.url)
    site_name = _OG_SITE.search(html)
    title = _TITLE.search(html)
    name = (
        (site_name.group(1) if site_name else "")
        or (re.sub(r"\s+", " ", title.group(1)).strip() if title else "")
        or urlparse(base).hostname
        or "brand"
    )

    colours = Counter(_normalise_colour(value) for value in _HEX.findall(html))
    colours.update(_normalise_colour(value) for value in _RGB.findall(html))
    palette = tuple(colour for colour, _count in colours.most_common(6) if colour)

    fonts: Counter[str] = Counter()
    for declaration in _FONT.findall(html):
        for family in declaration.split(","):
            cleaned = family.strip().strip("\"'").strip()
            if cleaned and cleaned.lower() not in _GENERIC_FONTS:
                fonts[cleaned] += 1
    font_list = tuple(family for family, _count in fonts.most_common(4))

    logo_url = ""
    for tag in _ICON_LINK.findall(html):
        href = _first_attr(tag, "href")
        if href:
            logo_url = urljoin(base, href)
            break
    og = _OG_IMAGE.search(html)
    og_image_url = urljoin(base, og.group(1)) if og else ""

    if not (palette or fonts or logo_url or og_image_url):
        raise BrandCaptureError(f"{url} exposed no colours, fonts or images")
    return BrandCapture(
        url=base,
        name=name,
        colors=palette,
        fonts=font_list,
        logo_url=logo_url,
        og_image_url=og_image_url,
    )
