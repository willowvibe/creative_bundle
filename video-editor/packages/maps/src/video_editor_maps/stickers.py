"""Speech bubbles and stickers that pop in at places on a map scene.

    bubbles:
      - {text: నమస్కారం, at: Eluru}              # any script; at = a marker/stop
      - {text: Hello, lon: 80.27, lat: 13.08, cue: Chennai}
    stickers:
      - {icon: laptop, at: Chennai}               # built-in flat icon
      - {image: photos/car.png, lon: 79.5, lat: 12.0, size: 1.4}

Each one is anchored to a place (``at`` a scene marker or journey stop, or
``lon``/``lat``), pops in on its ``cue`` (else a stagger), and rides the camera
on an overlay track, so it follows zooms without being scaled, like the place
labels. Text is HTML-escaped; Indic scripts shape through the browser's font
fallback. An ``image`` sticker is resized and embedded as a data URI.
"""

from __future__ import annotations

import base64
import html
import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_editor_core.cues import declared_cue
from video_editor_core.fonts import font_stack

#: Longest side of an embedded image sticker.
STICKER_IMAGE_MAX_PX = 400
#: Sticker edge on the 1080-wide design canvas, before ``size``.
STICKER_SIZE = 96.0


class StickerError(ValueError):
    """A bubble or sticker the scene cannot place."""


@dataclass(frozen=True)
class Bubble:
    text: str
    lon: float
    lat: float
    cue: str = ""


@dataclass(frozen=True)
class Sticker:
    lon: float
    lat: float
    icon: str = ""
    image: str = ""
    cue: str = ""
    size: float = 1.0


# Flat sticker icons on a 48x48 box: a white outline keeps them readable on any map.
_OUTLINE = 'stroke="#FFFFFF" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"'
_ICON_SVG: dict[str, str] = {
    "laptop": (
        f'<rect x="9" y="11" width="30" height="20" rx="2.5" fill="#2B3A55" {_OUTLINE}/>'
        '<rect x="12" y="14" width="24" height="14" rx="1" fill="#7FE0C8"/>'
        f'<path d="M5 34 H43 L40 38 H8 Z" fill="#C9D3E0" {_OUTLINE}/>'
    ),
    "tea": (
        f'<path d="M11 20 H33 V31 A8 8 0 0 1 25 39 H19 A8 8 0 0 1 11 31 Z" fill="#E8A45A" {_OUTLINE}/>'
        f'<path d="M33 23 A5 5 0 0 1 33 33" fill="none" {_OUTLINE}/>'
        '<path d="M17 9 Q15 12 17 15 M23 8 Q21 12 23 16 M29 9 Q27 12 29 15" fill="none" '
        'stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>'
    ),
    "family": (
        f'<circle cx="16" cy="15" r="6" fill="#F2C38B" {_OUTLINE}/>'
        f'<circle cx="32" cy="15" r="6" fill="#F2C38B" {_OUTLINE}/>'
        f'<path d="M6 40 Q6 25 16 25 Q26 25 26 40 Z" fill="#E07A5F" {_OUTLINE}/>'
        f'<path d="M22 40 Q22 25 32 25 Q42 25 42 40 Z" fill="#3D8B7D" {_OUTLINE}/>'
    ),
    "heart": (
        f'<path d="M24 40 C10 30 6 22 10 15 C14 9 21 10 24 16 C27 10 34 9 38 15 C42 22 38 30 24 40 Z" '
        f'fill="#E63946" {_OUTLINE}/>'
    ),
    "temple": (
        f'<path d="M14 40 V30 H34 V40 Z M16 30 V23 H32 V30 M18 23 V17 H30 V23 M20 17 V12 H28 V17 '
        f'M22 12 L24 6 L26 12" fill="#D9A441" {_OUTLINE}/>'
        '<rect x="21" y="33" width="6" height="7" fill="#7A4B12"/>'
    ),
    "phone": (
        f'<rect x="15" y="6" width="18" height="36" rx="3.5" fill="#2B3A55" {_OUTLINE}/>'
        '<rect x="18" y="10" width="12" height="24" rx="1" fill="#7FE0C8"/>'
        '<circle cx="24" cy="38" r="1.6" fill="#FFFFFF"/>'
    ),
    "chat": (
        '<path d="M7 11 H41 V31 H20 L12 39 V31 H7 Z" fill="#FFFFFF" stroke="#2B3A55" stroke-width="2.4" '
        'stroke-linejoin="round"/>'
        '<circle cx="17" cy="21" r="2.4" fill="#2B3A55"/><circle cx="24" cy="21" r="2.4" fill="#2B3A55"/>'
        '<circle cx="31" cy="21" r="2.4" fill="#2B3A55"/>'
    ),
}
ICONS = frozenset(_ICON_SVG)


def _place(
    entry: Mapping[str, Any], places: Mapping[str, tuple[float, float]], what: str
) -> tuple[float, float]:
    at = str(entry.get("at") or "").strip()
    if at:
        found = places.get(at.casefold())
        if found is None:
            known = ", ".join(sorted(places)) or "none"
            raise StickerError(f"{what} at {at!r}: no marker or stop by that name (known: {known})")
        return found
    try:
        return float(entry["lon"]), float(entry["lat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StickerError(f"{what} needs `at` (a marker or stop) or `lon` and `lat`") from exc


def _image_uri(path: str) -> str:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(Path(path).expanduser()) as opened:
            image = opened.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise StickerError(f"sticker image {path!r} cannot be read: {exc}") from exc
    image.thumbnail((STICKER_IMAGE_MAX_PX, STICKER_IMAGE_MAX_PX))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _entries(props: Mapping[str, Any], key: str) -> Sequence[Mapping[str, Any]]:
    raw = props.get(key)
    if raw is None:
        return ()
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, str)
        or not all(isinstance(item, Mapping) for item in raw)
    ):
        raise StickerError(f"map {key} must be a list of mappings")
    return raw


def parse_overlays(
    props: Mapping[str, Any], places: Mapping[str, tuple[float, float]]
) -> tuple[list[Bubble], list[Sticker]]:
    """The scene's bubbles and stickers; ``places`` maps casefolded names to lon/lat."""
    bubbles: list[Bubble] = []
    for entry in _entries(props, "bubbles"):
        text = str(entry.get("text") or "").strip()
        if not text:
            raise StickerError("a map bubble needs `text`")
        lon, lat = _place(entry, places, f"bubble {text!r}")
        bubbles.append(Bubble(text=text, lon=lon, lat=lat, cue=declared_cue(entry.get("cue"), "")))
    stickers: list[Sticker] = []
    for entry in _entries(props, "stickers"):
        icon = str(entry.get("icon") or "").strip().lower()
        image_path = str(entry.get("image") or "").strip()
        if bool(icon) == bool(image_path):
            raise StickerError("a map sticker needs exactly one of `icon` or `image`")
        if icon and icon not in ICONS:
            raise StickerError(f"sticker icon {icon!r} is not one of {sorted(ICONS)}")
        what = f"sticker {icon or image_path!r}"
        lon, lat = _place(entry, places, what)
        try:
            size = float(entry.get("size", 1.0))
        except (TypeError, ValueError) as exc:
            raise StickerError(f"{what}: `size` must be a number") from exc
        stickers.append(
            Sticker(
                lon=lon,
                lat=lat,
                icon=icon,
                image=_image_uri(image_path) if image_path else "",
                cue=declared_cue(entry.get("cue"), ""),
                size=max(0.3, min(4.0, size)),
            )
        )
    return bubbles, stickers


def bubble_markup(
    position: int,
    text: str,
    x: float,
    y: float,
    *,
    offset: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
) -> str:
    """A speech bubble at its anchor; a nudged one gets a leader line back to it."""
    dx, dy = offset
    body_style = ""
    leader = ""
    if dx or dy:
        lift = 14.0 * scale
        # The bubble body's bottom-centre sits at (dx, dy - lift) from the anchor.
        end_x, end_y = dx, dy - lift
        length = math.hypot(end_x, end_y)
        angle = math.atan2(end_y, end_x)
        body_style = f' style="left:{dx:.1f}px;bottom:{lift - dy:.1f}px"'
        leader = (
            f'<span class="map-bubble-leader" style="width:{length:.1f}px;'
            f'transform:rotate({angle:.4f}rad)"></span>'
        )
    return (
        f'<div class="map-bubble" data-camera-track="bubble_{position}" '
        f'style="left:{x:.1f}px;top:{y:.1f}px">{leader}'
        f'<span class="map-bubble-body"{body_style}>{html.escape(text)}</span></div>'
    )


def sticker_markup(position: int, sticker: Sticker, x: float, y: float, scale: float) -> str:
    edge = STICKER_SIZE * scale * sticker.size
    if sticker.image:
        art = (
            f'<img class="map-sticker-art" src="{sticker.image}" alt="" '
            f'style="width:{edge:.1f}px;height:auto"/>'
        )
    else:
        art = (
            f'<svg class="map-sticker-art" viewBox="0 0 48 48" width="{edge:.1f}" '
            f'height="{edge:.1f}">{_ICON_SVG[sticker.icon]}</svg>'
        )
    return (
        f'<div class="map-sticker" data-camera-track="sticker_{position}" '
        f'style="left:{x:.1f}px;top:{y:.1f}px">{art}</div>'
    )


def overlay_css(index: int, scale: float, *, ink: str = "#14211F") -> str:
    """Bubble and sticker styles plus the pop the tracks use as their reveal."""
    return (
        f"      .map-{index} .map-bubble, .map-{index} .map-sticker {{ position:absolute;\n"
        "        width:0; height:0; opacity:0; transform-origin:0 0; }\n"
        f"      .map-{index} .map-bubble-body {{ position:absolute; left:0; bottom:{14 * scale:.1f}px;\n"
        "        transform:translateX(-50%); white-space:nowrap; background:#FFFFFF;\n"
        f"        color:{ink}; font: 700 {30 * scale:.1f}px/1.25 {font_stack(None)};\n"
        f"        padding:{8 * scale:.1f}px {16 * scale:.1f}px; border-radius:{14 * scale:.1f}px;\n"
        f"        box-shadow:0 {3 * scale:.1f}px {10 * scale:.1f}px rgba(0,0,0,0.35); }}\n"
        f"      .map-{index} .map-bubble-body::after {{ content:''; position:absolute; left:50%;\n"
        f"        bottom:{-9 * scale:.1f}px; transform:translateX(-50%);\n"
        f"        border:{10 * scale:.1f}px solid transparent; border-bottom:0;\n"
        "        border-top-color:#FFFFFF; }\n"
        f"      .map-{index} .map-bubble-leader {{ position:absolute; left:0; top:0;\n"
        f"        height:{2.5 * scale:.1f}px; margin-top:{-1.25 * scale:.1f}px; background:#FFFFFF;\n"
        f"        transform-origin:0 50%; border-radius:{2 * scale:.1f}px;\n"
        f"        box-shadow:0 {2 * scale:.1f}px {6 * scale:.1f}px rgba(0,0,0,0.35); }}\n"
        f"      .map-{index} .map-sticker-art {{ position:absolute; left:0; top:0;\n"
        "        transform:translate(-50%, -50%);\n"
        f"        filter: drop-shadow(0 {3 * scale:.1f}px {6 * scale:.1f}px rgba(0,0,0,0.4)); }}\n"
        f"      @keyframes map_pop_{index} {{ 0% {{ opacity:0; transform:scale(0.3); }}\n"
        "        70% { opacity:1; transform:scale(1.12); } 100% { opacity:1; transform:scale(1); } }\n"
    )
