"""Vendored Lucide icons (ISC licence, see ``lucide/LICENSE``).

The SVG files keep their licence comment on disk; :func:`icon_svg` strips it and
returns an inline ``<svg>`` that takes its colour from CSS ``color`` (every
Lucide stroke is ``currentColor``), so a scene can tint an icon with the
design's accent without editing the file.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent / "lucide"

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SVG_OPEN = re.compile(r"<svg\b[^>]*>", re.DOTALL)
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


class IconError(ValueError):
    """An icon name with no vendored file."""


def icon_names() -> list[str]:
    """Names of the vendored icons, sorted."""
    return sorted(path.stem for path in ICON_DIR.glob("*.svg"))


@cache
def _load(name: str) -> str:
    raw = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    raw = _COMMENT.sub("", raw).strip()
    opening = _SVG_OPEN.search(raw)
    if opening is None:
        raise IconError(f"icon {name!r} is not an SVG file")
    viewbox = re.search(r'viewBox="([^"]+)"', opening.group(0))
    box = viewbox.group(1) if viewbox else "0 0 24 24"
    head = (
        f'<svg class="typo-icon-svg" xmlns="http://www.w3.org/2000/svg" viewBox="{box}" '
        'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round" aria-hidden="true" focusable="false">'
    )
    return head + re.sub(r"\s+", " ", raw[opening.end() :]).strip()


def icon_svg(name: str) -> str:
    """The inline SVG for ``name``; an unknown name lists the available ones."""
    key = str(name or "").strip().lower()
    if not _NAME.fullmatch(key) or not (ICON_DIR / f"{key}.svg").is_file():
        raise IconError(f"unknown icon {name!r}; available icons: {', '.join(icon_names())}")
    return _load(key)
