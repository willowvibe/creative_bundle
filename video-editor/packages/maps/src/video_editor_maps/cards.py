"""Large photo cards that pop in on a map scene's cue words.

    cards:
      - {image: photos/laddu.jpg, title: తిరుపతి లడ్డూ, badge: GI, cue: తిరుపతి}
      - {image: photos/fish.jpg, title: నెల్లూరు చేపల పులుసు, cue: నెల్లూరు, hold: 2.0}

A card is a rounded, cover-cropped photo with a bold title underneath and an
optional badge on its top-right corner. Only one is on screen at a time: it
enters on its cue (default: the title), stays until the next card enters or
its ``hold`` runs out, and exits just before the cut for the last card. The
motion is plain CSS keyframes on the HTML card, so a HyperFrames seek lands on
exactly the frame it should.
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

from video_editor_core.cues import declared_cue, find_cue
from video_editor_core.safe_area import content_bottom, safe_box

#: Longest side of an embedded card photo.
CARD_IMAGE_MAX_PX = 1200
#: The image's cover-cropped aspect ratio.
CARD_ASPECT = 4.0 / 3.0
#: Fade + scale with a small overshoot, and the shorter fade-out.
CARD_ENTER_SECONDS = 0.35
CARD_EXIT_SECONDS = 0.25
#: A card is never cut away before it has been fully visible this long.
CARD_MIN_VISIBLE = 0.4
#: The last uncued card leaves this much room before the scene's cut.
CARD_END_MARGIN = 0.3
#: Gap below a card's title before the map's free band, as a share of height.
CARD_BAND_GAP = 0.02
#: Indic scripts need extra leading so descenders and vowel signs stay inside.
CARD_TITLE_LINE_HEIGHT = 1.35
#: Titles past this many characters are scaled down to fit their box.
CARD_TITLE_SHRINK_AFTER = 16
#: Never scale a long title below this fraction of the base size.
CARD_TITLE_MIN_SCALE = 0.72


class CardError(ValueError):
    """A photo card the scene cannot draw."""


@dataclass(frozen=True)
class Card:
    """One embedded photo card and its reveal metadata."""

    image: str
    title: str
    badge: str = ""
    cue: str = ""
    hold: float | None = None


@dataclass(frozen=True)
class CardWindow:
    """When a card starts entering and when its exit animation begins."""

    start: float
    exit: float


def _entries(props: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    raw = props.get("cards")
    if raw is None:
        return ()
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, str)
        or not all(isinstance(item, Mapping) for item in raw)
    ):
        raise CardError("map cards must be a list of {image, title, ...} maps")
    return raw


def _title(entry: Mapping[str, Any], position: int) -> str:
    title = str(entry.get("title") or "").strip()
    if not title:
        raise CardError(f"map card {position} needs `title`")
    return title


def _image_data_uri(path: str, position: int) -> str:
    from PIL import Image, UnidentifiedImageError

    if not path:
        raise CardError(f"map card {position} needs `image`")
    source = Path(path).expanduser()
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise CardError(f"map card {position}: cannot read image {path!r}: {exc}") from exc
    image.thumbnail((CARD_IMAGE_MAX_PX, CARD_IMAGE_MAX_PX))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _hold(entry: Mapping[str, Any], position: int) -> float | None:
    raw = entry.get("hold")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise CardError(f"map card {position}: `hold` must be a number of seconds") from None
    if not math.isfinite(value) or value < 0.0:
        raise CardError(f"map card {position}: `hold` must be a non-negative number")
    return value


def parse_cards(props: Mapping[str, Any]) -> list[Card]:
    """Validate and embed every ``cards`` entry."""
    cards: list[Card] = []
    for position, entry in enumerate(_entries(props)):
        title = _title(entry, position)
        image_path = str(entry.get("image") or "").strip()
        cards.append(
            Card(
                image=_image_data_uri(image_path, position),
                title=title,
                badge=str(entry.get("badge") or "").strip(),
                cue=declared_cue(entry.get("cue"), title),
                hold=_hold(entry, position),
            )
        )
    return cards


def card_cues(props: Mapping[str, Any]) -> dict[str, str]:
    """``{card_N: phrase}`` for the QA cue gate, without reading any image."""
    cues: dict[str, str] = {}
    for position, entry in enumerate(_entries(props)):
        cue = declared_cue(entry.get("cue"), _title(entry, position))
        if cue:
            cues[f"card_{position}"] = cue
    return cues


def silence_unspoken_cards(
    props: Mapping[str, Any], words: Sequence[Mapping[str, Any]]
) -> list[Any]:
    """Card entries whose default cue the narration never says, switched off."""
    silenced: list[Any] = []
    for position, entry in enumerate(_entries(props)):
        entry = dict(entry)
        cue = declared_cue(entry.get("cue"), _title(entry, position))
        if cue and find_cue(words, cue) is None:
            entry["cue"] = False
        silenced.append(entry)
    return silenced


def schedule_cards(
    cards: Sequence[Card], starts: Sequence[float], seconds: float
) -> list[CardWindow]:
    """Turn cue starts into enter/exit times, with one card on screen at a time.

    A non-last card's exit starts :data:`CARD_EXIT_SECONDS` before the next
    card enters, so the outgoing title has faded before the new one appears. A
    ``hold`` can take it out sooner, and a card stays fully visible for at
    least :data:`CARD_MIN_VISIBLE` seconds. The final card stays until
    ``CARD_END_MARGIN`` before the cut (or its own ``hold``).
    """
    length = max(0.0, float(seconds))
    begins = [max(0.0, min(length, float(start))) for start in starts]
    windows: list[CardWindow] = []
    for position, card in enumerate(cards):
        start = begins[position]
        if position + 1 < len(cards):
            next_start = begins[position + 1]
            exit_at = next_start - CARD_EXIT_SECONDS
            if card.hold is not None:
                exit_at = min(exit_at, start + card.hold)
            exit_at = max(exit_at, start + CARD_MIN_VISIBLE)
        elif card.hold is not None:
            exit_at = start + card.hold
        else:
            exit_at = length - CARD_END_MARGIN
        exit_at = max(start, min(length, exit_at))
        windows.append(CardWindow(start=start, exit=exit_at))
    return windows


@dataclass(frozen=True)
class _Layout:
    left: float
    top: float
    width: float
    image_height: float
    title_font: float
    gap: float
    radius: float

    @property
    def total_height(self) -> float:
        """The image, title gap and a two-line title, as the card box lays out."""
        return self.image_height + self.gap + self.title_font * 1.15 * 2.0

    @property
    def bottom(self) -> float:
        """The card's bottom edge, including its title."""
        return self.top + self.total_height


def _layout(canvas_w: float, canvas_h: float, safe_profile: str) -> _Layout:
    """The card's box: ~70% of a portrait frame wide, ~38% of a landscape tall."""
    portrait = canvas_h >= canvas_w
    if portrait:
        width = canvas_w * 0.70
        image_height = width / CARD_ASPECT
    else:
        image_height = canvas_h * 0.38
        width = image_height * CARD_ASPECT
    title_font = max(1.0, width * 0.075)
    gap = max(1.0, width * 0.035)
    total = image_height + gap + title_font * 1.15 * 2.0

    left_edge, top_edge, safe_width, safe_height = safe_box(
        round(canvas_w), round(canvas_h), profile=safe_profile or "auto"
    )
    factor = min(1.0, safe_width / width if width else 1.0)
    min_top = max(canvas_h * 0.10, float(top_edge))
    max_bottom = float(top_edge + safe_height)
    if portrait:
        max_bottom = min(canvas_h * 0.58, max_bottom)
    available = max_bottom - min_top
    if total > 0.0 and total * factor > available:
        factor = min(factor, max(0.0, available) / total)
    factor = max(factor, 0.05)
    width *= factor
    image_height *= factor
    title_font *= factor
    gap *= factor
    total *= factor

    left = (canvas_w - width) / 2.0
    left = min(max(left, float(left_edge)), left_edge + safe_width - width)
    top = min_top
    if total <= available:
        top = max(min_top, max_bottom - total) if min_top + total > max_bottom else min_top
    return _Layout(
        left=left,
        top=top,
        width=width,
        image_height=image_height,
        title_font=title_font,
        gap=gap,
        radius=max(2.0, width * 0.035),
    )


def card_bottom(canvas_w: float, canvas_h: float, safe_profile: str = "auto") -> float:
    """The bottom edge of the card box, including its title, in canvas pixels."""
    return _layout(canvas_w, canvas_h, safe_profile).bottom


def has_cards(props: Mapping[str, Any]) -> bool:
    """True when the scene declares at least one photo card."""
    return bool(_entries(props))


def cards_fit_frame(props: Mapping[str, Any]) -> bool:
    """True unless the scene opts out of the band fit with ``cards_fit: frame``."""
    return str(props.get("cards_fit") or "").strip().lower() != "frame"


#: Allowed range for a scene's ``cards_band: {bottom: <fraction>}`` override.
MIN_BAND_BOTTOM = 0.5
MAX_BAND_BOTTOM = 0.95


def cards_band_bottom(props: Mapping[str, Any]) -> float | None:
    """The scene's ``cards_band.bottom`` fraction, or ``None`` when not set."""
    band = props.get("cards_band")
    if not isinstance(band, Mapping) or band.get("bottom") in (None, ""):
        return None
    try:
        return float(band["bottom"])
    except (TypeError, ValueError) as exc:
        raise CardError(f"cards_band.bottom must be a number, got {band['bottom']!r}") from exc


def card_fit_band(
    canvas_w: float,
    canvas_h: float,
    safe_profile: str = "auto",
    *,
    captions_burned: bool = True,
    bottom_fraction: float | None = None,
) -> tuple[float, float] | None:
    """The free band under the card and above the burned captions.

    Returned as ``(top, bottom)`` in canvas pixels, or ``None`` when the card
    leaves no usable room. The bottom is the same caption-band top the
    explanatory pipeline burns captions into (see :func:`caption_top`). When the
    video does not burn captions, a portrait band reaches the Shorts UI safe
    bottom instead (see :func:`content_bottom`). ``bottom_fraction`` (a scene's
    ``cards_band.bottom``, clamped to 0.5-0.95) overrides either.
    """
    top = card_bottom(canvas_w, canvas_h, safe_profile) + canvas_h * CARD_BAND_GAP
    if bottom_fraction is not None:
        bottom = canvas_h * min(MAX_BAND_BOTTOM, max(MIN_BAND_BOTTOM, bottom_fraction))
    else:
        bottom = float(
            content_bottom(round(canvas_w), round(canvas_h), captions_burned=captions_burned)
        )
    if bottom <= top:
        return None
    return top, bottom


def _title_font_size(title: str, base: float) -> float:
    """Scale long titles down so they fit without overflowing their line box."""
    length = len(title.strip())
    if length <= CARD_TITLE_SHRINK_AFTER:
        return base
    scale = max(CARD_TITLE_MIN_SCALE, CARD_TITLE_SHRINK_AFTER / length)
    return base * scale


def cards_markup(
    cards: Sequence[Card],
    windows: Sequence[CardWindow],
    *,
    index: int,
    canvas_w: float,
    canvas_h: float,
    scale: float,
    body: str,
    colors: Mapping[str, str],
    safe_profile: str = "auto",
) -> tuple[str, str]:
    """The card layer's HTML and its scoped CSS/keyframes."""
    if not cards:
        return "", ""
    layout = _layout(canvas_w, canvas_h, safe_profile)
    border = max(1, round(2.0 * scale))
    badge_pad = max(2.0, layout.width * 0.018)
    badge_font = max(1.0, layout.width * 0.040)
    edge = colors.get("card_border") or colors.get("rule") or "#FFFFFF"
    fill = colors.get("card_fill") or "#FFFFFF"
    ink = colors.get("ink") or "#14211F"
    shadow = f"0 {max(2, round(8 * scale))}px {max(4, round(24 * scale))}px rgba(0,0,0,0.45)"
    blocks = [
        f"      .map-{index} .map-photo-cards {{ position:absolute; inset:0;\n"
        "        pointer-events:none; }\n"
        f"      .map-{index} .map-photo-card {{ position:absolute; opacity:0;\n"
        "        transform-origin:50% 40%; }\n"
        f"      .map-{index} .map-photo-card-media {{ position:relative; width:100%;\n"
        f"        height:{layout.image_height:.1f}px; overflow:hidden;\n"
        f"        border:{border}px solid #FFFFFF; box-sizing:border-box;\n"
        f"        border-radius:{layout.radius:.1f}px; background:{fill};\n"
        f"        box-shadow:{shadow}; }}\n"
        f"      .map-{index} .map-photo-card-image {{ display:block; width:100%;\n"
        "        height:100%; object-fit:cover; }\n"
        f"      .map-{index} .map-photo-card-title {{ margin-top:{layout.gap:.1f}px;\n"
        f"        font:700 {layout.title_font:.1f}px/{CARD_TITLE_LINE_HEIGHT:.2f} {body};\n"
        f"        line-height:{CARD_TITLE_LINE_HEIGHT:.2f}; color:{ink};\n"
        "        text-align:center; text-shadow:0 2px 8px rgba(0,0,0,0.35);\n"
        "        height:auto; min-height:1.35em; padding:0.04em 0 0.08em;\n"
        "        display:-webkit-box; -webkit-box-orient:vertical;\n"
        "        -webkit-line-clamp:2; overflow:hidden; }\n"
        f"      .map-{index} .map-photo-card-badge {{ position:absolute;\n"
        f"        top:{badge_pad:.1f}px; right:{badge_pad:.1f}px;\n"
        f"        padding:{badge_pad * 0.55:.1f}px {badge_pad * 1.1:.1f}px;\n"
        "        border-radius:999px; border:1px solid " + edge + ";\n"
        f"        background:{fill}; color:{ink}; font:700 {badge_font:.1f}px/1 {body};\n"
        "        letter-spacing:0.06em; text-transform:uppercase; }\n"
    ]
    parts: list[str] = []
    for position, (card, window) in enumerate(zip(cards, windows, strict=True)):
        badge = (
            f'<span class="map-photo-card-badge">{html.escape(card.badge)}</span>'
            if card.badge
            else ""
        )
        style = (
            f"left:{layout.left:.1f}px;top:{layout.top:.1f}px;width:{layout.width:.1f}px;"
            f"animation:map_photo_card_in_{index}_{position} "
            f"{CARD_ENTER_SECONDS:.3f}s cubic-bezier(.2,.8,.2,1) {window.start:.3f}s both,"
            f" map_photo_card_out_{index}_{position} {CARD_EXIT_SECONDS:.3f}s ease-in "
            f"{window.exit:.3f}s forwards"
        )
        title_font = _title_font_size(card.title, layout.title_font)
        title_style = (
            f' style="font-size:{title_font:.1f}px"' if title_font < layout.title_font else ""
        )
        parts.append(
            f'<div class="map-photo-card" data-card="{position}" '
            "data-layout-allow-overlap "
            f'data-card-start="{window.start:.3f}" data-card-exit="{window.exit:.3f}" '
            f'style="{style}">'
            '<div class="map-photo-card-media">'
            f'<img class="map-photo-card-image" src="{card.image}" alt="">'
            f"{badge}</div>"
            f'<div class="map-photo-card-title"{title_style}>'
            f"{html.escape(card.title)}</div>"
            "</div>"
        )
        blocks.append(
            f"      @keyframes map_photo_card_in_{index}_{position} {{\n"
            "        0% { opacity:0; transform:scale(.85); }\n"
            "        70% { opacity:1; transform:scale(1.04); }\n"
            "        100% { opacity:1; transform:scale(1); }\n"
            "      }\n"
            f"      @keyframes map_photo_card_out_{index}_{position} {{\n"
            "        0% { opacity:1; transform:scale(1); }\n"
            "        100% { opacity:0; transform:scale(.92); }\n"
            "      }\n"
        )
    return (
        f'<div class="map-photo-cards">{"".join(parts)}</div>',
        "".join(blocks),
    )
