"""Translate a design's ``layers`` block into placed rects and styles.

Every styling decision lives here or in the design JSON. The adapter decides
*when* a preset is on screen; this module decides *where* it sits and what it
looks like. Each function re-checks the safe area, so a design edit that
pushes a box over the platform edge fails at build time rather than in review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from video_editor_core.composition import Canvas
from video_editor_core.designs import DesignTokens
from video_editor_core.fonts import BUNDLED_FONTS
from video_editor_core.render_graph import Rect
from video_editor_core.safe_area import safe_box

#: The caption band never grows past this: 180 px of two-line Inter at 64 px.
CAPTION_MAX_HEIGHT = 180


class LayerPresetError(ValueError):
    """A design's ``layers`` block cannot produce a valid placement."""


@dataclass(frozen=True)
class Placed:
    """A rect plus the style dict ``compile_composition`` understands."""

    rect: Rect
    style: dict[str, Any]
    #: Preset-owned content (a scrim gradient). Empty for text presets, where
    #: the words come from the pack, never from the design.
    content: str = ""


def _safe(canvas: Canvas, tokens: DesignTokens) -> tuple[int, int, int, int]:
    profile = tokens.layers.safe_area or "auto"
    try:
        return safe_box(canvas.width, canvas.height, profile=profile)
    except (KeyError, ValueError) as exc:
        raise LayerPresetError(
            f"design {tokens.name!r} names unknown safe-area profile {profile!r}"
        ) from exc


def _require_safe(rect: Rect, canvas: Canvas, tokens: DesignTokens, name: str) -> None:
    x, y, width, height = _safe(canvas, tokens)
    inside = (
        rect.x >= x
        and rect.y >= y
        and rect.x + rect.width <= x + width
        and rect.y + rect.height <= y + height
    )
    if not inside:
        raise LayerPresetError(
            f"{name} rect ({rect.x},{rect.y},{rect.width},{rect.height}) leaves the "
            f"safe area ({x},{y},{width},{height}) of design {tokens.name!r}"
        )


def _text_style(
    block: dict[str, Any],
    tokens: DesignTokens,
    *,
    size: int,
    weight: int,
    color: str,
) -> dict[str, Any]:
    """The style keys ``compile_composition._text_css`` reads, and no others."""
    font = str(block.get("font") or tokens.type.body)
    if font not in BUNDLED_FONTS:
        raise LayerPresetError(
            f"design {tokens.name!r} names font {font!r}, which is not bundled; "
            f"choose from {sorted(BUNDLED_FONTS)}"
        )
    style: dict[str, Any] = {
        "font": font,
        "font_size": int(block.get("font_size") or size),
        "font_weight": int(block.get("font_weight") or weight),
        "font_color": str(block.get("font_color") or color),
        "text_effect": str(block.get("text_effect") or "shadow"),
    }
    if "border_width" in block:
        style["border_width"] = int(block["border_width"])
    if "border_color" in block:
        style["border_color"] = str(block["border_color"])
    return style


def scrims(tokens: DesignTokens, canvas: Canvas) -> list[Placed]:
    """Top and bottom gradient scrims, full-bleed by design, not safe-boxed."""
    block = tokens.layers.scrim
    top_height = int(block.get("top_height") or 0)
    bottom_height = int(block.get("bottom_height") or 0)
    if top_height <= 0 and bottom_height <= 0:
        return []
    if top_height + bottom_height > canvas.height:
        raise LayerPresetError(
            f"scrims total {top_height + bottom_height}px on a {canvas.height}px canvas"
        )
    placed: list[Placed] = []
    if top_height > 0:
        placed.append(
            Placed(
                Rect(x=0, y=0, width=canvas.width, height=top_height),
                {"radius": 0},
                str(block.get("top") or ""),
            )
        )
    if bottom_height > 0:
        placed.append(
            Placed(
                Rect(
                    x=0,
                    y=canvas.height - bottom_height,
                    width=canvas.width,
                    height=bottom_height,
                ),
                {"radius": 0},
                str(block.get("bottom") or ""),
            )
        )
    for item in placed:
        if not item.content:
            raise LayerPresetError(
                f"design {tokens.name!r} has a scrim with no gradient; the shape "
                "layer would render as an opaque block"
            )
    return placed


def chip(tokens: DesignTokens, canvas: Canvas) -> Placed | None:
    """The CJK scene glyph, centred at the design's y."""
    block = tokens.layers.chip
    if not block:
        return None
    size = int(block.get("font_size") or 96)
    x, top, width, _ = _safe(canvas, tokens)
    rect = Rect(
        x=x,
        y=int(block.get("y") or top),
        width=width,
        height=int(round(size * 1.5)),
    )
    _require_safe(rect, canvas, tokens, "chip")
    style = _text_style(block, tokens, size=size, weight=600, color=tokens.palette.accent)
    return Placed(rect, style)


def pill(tokens: DesignTokens, canvas: Canvas, *, index: int = 0) -> Placed:
    """The ``show`` line as a filled band; ``index`` stacks down the frame."""
    block = tokens.layers.pill
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no pill block")
    size = int(block.get("font_size") or 52)
    x, top, width, _ = _safe(canvas, tokens)
    rect = Rect(
        x=int(block.get("x", x)),
        y=int(block.get("first_y", top)) + index * int(block.get("gap") or 0),
        width=int(block.get("width", width)),
        height=int(block.get("height") or round(size * 1.6)),
    )
    _require_safe(rect, canvas, tokens, f"pill {index}")
    style = _text_style(block, tokens, size=size, weight=600, color=tokens.palette.ink)
    background = str(block.get("background") or "")
    if not background:
        raise LayerPresetError(
            f"design {tokens.name!r} has no pill background; the band would be "
            "unreadable over the still"
        )
    style["background"] = background
    return Placed(rect, style)


def caption(tokens: DesignTokens, canvas: Canvas) -> Placed:
    """The karaoke band, clamped inside the platform's bottom edge."""
    block = tokens.layers.caption_line
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no caption_line block")
    box = block.get("box") or {}
    x, top, width, height = _safe(canvas, tokens)
    cap_height = int(box.get("height", CAPTION_MAX_HEIGHT))
    if cap_height > CAPTION_MAX_HEIGHT:
        raise LayerPresetError(
            f"caption box is {cap_height}px tall; the band is capped at {CAPTION_MAX_HEIGHT}px"
        )
    rect = Rect(
        x=int(box.get("x", x)),
        y=int(box.get("y", top + height - cap_height)),
        width=int(box.get("width", width)),
        height=cap_height,
    )
    _require_safe(rect, canvas, tokens, "caption")
    style = _text_style(block, tokens, size=64, weight=700, color=tokens.palette.ink)
    style["highlight_color"] = str(block.get("highlight_color") or tokens.palette.accent)
    style["upcoming_opacity"] = float(block.get("upcoming_opacity", 0.6))
    return Placed(rect, style)


def end_card(tokens: DesignTokens, canvas: Canvas) -> tuple[Placed, Placed]:
    """Two stacked text layers: the card's words over its URL."""
    block = tokens.layers.end_card
    if not block:
        raise LayerPresetError(f"design {tokens.name!r} has no end_card block")
    box = block.get("box") or {}
    x, top, width, _ = _safe(canvas, tokens)
    card_x = int(box.get("x", x))
    card_y = int(box.get("y", top))
    card_width = int(box.get("width", width))
    card_height = int(box.get("height") or 320)
    text_height = int(round(card_height * 0.6))
    text = Placed(
        Rect(x=card_x, y=card_y, width=card_width, height=text_height),
        _text_style(block, tokens, size=68, weight=600, color=tokens.palette.ink),
    )
    url_style = _text_style(block, tokens, size=36, weight=500, color=tokens.palette.accent)
    url_style["font_size"] = int(block.get("url_font_size") or 36)
    url_style["font_color"] = str(block.get("url_color") or tokens.palette.accent)
    url = Placed(
        Rect(
            x=card_x,
            y=card_y + text_height,
            width=card_width,
            height=card_height - text_height,
        ),
        url_style,
    )
    _require_safe(text.rect, canvas, tokens, "end card text")
    _require_safe(url.rect, canvas, tokens, "end card url")
    return text, url
