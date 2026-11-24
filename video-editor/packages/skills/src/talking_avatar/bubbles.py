"""Speech-bubble cue parsing, scheduling and drawing for the avatar overlay.

A ``bubbles.yaml`` file is a list of entries.  Each entry names either a word
from the Whisper transcript (with an optional ``nth`` occurrence) or an
absolute ``time``, plus the text to show.  Bubbles never overlap: a later cue
waits until the previous bubble has finished fading.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from talking_avatar.timeline import WordTiming

DEFAULT_HOLD_SECONDS = 2.5
DEFAULT_GLANCE_HOLD_SECONDS = 1.2
DEFAULT_BROWS_HOLD_SECONDS = 0.9
ACTION_TYPES = ("glance", "brows")
POP_IN_SECONDS = 0.25
FADE_OUT_SECONDS = 0.3
OUTLINE_PIXELS = 3
TARGET_FONT_RATIO = 0.085
MAX_BUBBLE_WIDTH_RATIO = 1.25
MIN_FONT_SIZE = 8
POP_SCALE = 1.1
OUTLINE_COLOUR = (30, 44, 69, 255)
TEXT_COLOUR = (30, 44, 69, 255)
EMOJI_FONT_PATH = Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf")

FIRA_BOLD_PATHS = (
    Path("/usr/share/fonts/truetype/fira-sans/FiraSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/fira/FiraSans-Bold.ttf"),
    Path("/usr/share/fonts/opentype/fira/FiraSans-Bold.otf"),
    Path("/usr/share/fonts/truetype/firasans/FiraSans-Bold.ttf"),
)
BOLD_SANS_PATHS = (
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/open-sans/OpenSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)

_PUNCTUATION = re.compile(r"[^\w]+", re.UNICODE)


class BubbleError(RuntimeError):
    """Raised when a bubble cue cannot be parsed or resolved."""


@dataclass(frozen=True)
class BubbleSpec:
    """One entry from ``bubbles.yaml`` before its word is resolved.

    An entry with an ``action`` and no ``text`` is an avatar-only expression
    action; it is never scheduled or drawn as a bubble.
    """

    text: str = ""
    word: str | None = None
    nth: int = 1
    time: float | None = None
    emoji: str | None = None
    hold: float | None = None
    action: str | None = None
    brows: bool = True

    @property
    def is_action(self) -> bool:
        return self.action is not None and not self.text


@dataclass(frozen=True)
class Bubble:
    """A resolved bubble with its visible start and end times."""

    text: str
    start: float
    end: float
    emoji: str | None = None
    brows: bool = True

    @property
    def hold(self) -> float:
        return max(0.0, self.end - self.start - POP_IN_SECONDS - FADE_OUT_SECONDS)


@dataclass(frozen=True)
class AvatarAction:
    """A resolved expression action: ``glance`` or ``brows``."""

    type: str
    start: float
    end: float


@dataclass(frozen=True)
class BubbleArt:
    """A pre-rendered bubble image and the tail point used for the pop-in."""

    image: Image.Image
    tail: tuple[int, int]
    bubble_box: tuple[int, int, int, int] = (0, 0, 0, 0)
    text_box: tuple[int, int, int, int] = (0, 0, 0, 0)
    font_size: int = 0
    lines: tuple[str, ...] = ()
    mask: Image.Image | None = None

    @property
    def interior(self) -> Image.Image | None:
        return self.mask


def _normalise_token(text: str) -> str:
    return _PUNCTUATION.sub("", str(text)).lower()


def _coerce_spec(item: Any) -> BubbleSpec:
    if isinstance(item, BubbleSpec):
        return item
    if not isinstance(item, Mapping):
        raise BubbleError(f"Bubble entry must be a mapping, got {type(item).__name__}")
    action_value = item.get("action")
    action = str(action_value).strip().lower() if action_value is not None else None
    if action == "":
        action = None
    if action is not None and action not in ACTION_TYPES:
        raise BubbleError(f"Unknown avatar action {action!r}; choose one of {list(ACTION_TYPES)}")
    text = str(item.get("text", "")).strip()
    if not text and action is None:
        raise BubbleError("Bubble entry is missing text")
    word = item.get("word")
    word = str(word).strip() if word is not None else None
    if word == "":
        word = None
    time_value = item.get("time")
    time = float(time_value) if time_value is not None else None
    if word is None and time is None:
        label = text or action
        raise BubbleError(f"Bubble {label!r} needs either a word or a time")
    nth = int(item.get("nth", 1))
    if nth < 1:
        raise BubbleError(f"Bubble {text!r} has nth={nth}; it must be 1 or greater")
    hold_value = item.get("hold")
    if hold_value is not None:
        hold: float = float(hold_value)
    elif not text and action == "glance":
        hold = DEFAULT_GLANCE_HOLD_SECONDS
    elif not text and action == "brows":
        hold = DEFAULT_BROWS_HOLD_SECONDS
    else:
        hold = DEFAULT_HOLD_SECONDS
    if hold < 0.0:
        raise BubbleError(f"Bubble {text or action!r} has a negative hold")
    emoji = item.get("emoji")
    emoji = str(emoji) if emoji else None
    brows_value = item.get("brows", True)
    brows = bool(brows_value) if brows_value is not None else True
    return BubbleSpec(
        text=text,
        word=word,
        nth=nth,
        time=time,
        emoji=emoji,
        hold=hold,
        action=action,
        brows=brows,
    )


def parse_bubbles(payload: Any) -> list[BubbleSpec]:
    """Parse an already-loaded YAML payload (a list or ``{"bubbles": [...]}``)."""
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        payload = payload.get("bubbles", [])
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise BubbleError("bubbles.yaml must be a list of bubble entries")
    return [_coerce_spec(item) for item in payload]


def load_bubbles(path: Path | str) -> list[BubbleSpec]:
    """Read and parse a ``bubbles.yaml`` file."""
    import yaml

    file = Path(path)
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    return parse_bubbles(data)


def _word_start(word: str, words: Sequence[WordTiming], nth: int) -> float:
    target = _normalise_token(word)
    if not target:
        raise BubbleError(f"Bubble word {word!r} contains no letters or digits")
    matches = [timing for timing in words if _normalise_token(timing.text) == target]
    if len(matches) < nth:
        raise BubbleError(f"Bubble word {word!r} was not found {nth} time(s) in the transcript")
    return float(matches[nth - 1].start)


def schedule_bubbles(bubbles: Sequence[Bubble]) -> list[Bubble]:
    """Push overlapping bubbles later so only one is ever visible at a time."""
    ordered = sorted(bubbles, key=lambda bubble: (bubble.start, bubble.end))
    scheduled: list[Bubble] = []
    previous_end: float | None = None
    for bubble in ordered:
        start = max(0.0, float(bubble.start))
        end = max(start, float(bubble.end))
        if previous_end is not None and start < previous_end:
            shift = previous_end - start
            start += shift
            end += shift
        scheduled.append(replace(bubble, start=start, end=end))
        previous_end = end
    return scheduled


def resolve_bubbles(specs: Sequence[BubbleSpec], words: Sequence[WordTiming]) -> list[Bubble]:
    """Resolve word cues against Whisper timings and schedule the bubbles.

    Action-only entries are skipped: they never take part in bubble
    scheduling, so they cannot delay or block a text bubble.
    """
    bubbles: list[Bubble] = []
    for spec in specs:
        if spec.is_action:
            continue
        if spec.time is not None:
            start = float(spec.time)
        else:
            assert spec.word is not None
            start = _word_start(spec.word, words, spec.nth)
        hold = spec.hold if spec.hold is not None else DEFAULT_HOLD_SECONDS
        end = start + max(0.0, hold) + POP_IN_SECONDS + FADE_OUT_SECONDS
        bubbles.append(
            Bubble(
                text=spec.text,
                start=max(0.0, start),
                end=max(0.0, end),
                emoji=spec.emoji,
                brows=spec.brows,
            )
        )
    return schedule_bubbles(bubbles)


def resolve_actions(
    specs: Sequence[BubbleSpec],
    words: Sequence[WordTiming],
    bubbles: Sequence[Bubble] = (),
) -> list[AvatarAction]:
    """Resolve explicit expression entries and bubble pop-in brow raises.

    Explicit actions resolve independently of the bubble schedule, so they
    never push a bubble later.  Every text bubble whose entry did not set
    ``brows: false`` also raises the brows for 0.9 s at its pop-in.
    """
    actions: list[AvatarAction] = []
    for spec in specs:
        if spec.action is None:
            continue
        if spec.time is not None:
            start = float(spec.time)
        elif spec.word is not None:
            start = _word_start(spec.word, words, spec.nth)
        else:  # pragma: no cover - parsing already rejects this
            continue
        if spec.hold is not None:
            hold = spec.hold
        elif spec.action == "glance":
            hold = DEFAULT_GLANCE_HOLD_SECONDS
        else:
            hold = DEFAULT_BROWS_HOLD_SECONDS
        start = max(0.0, start)
        end = max(start, start + max(0.0, hold))
        actions.append(AvatarAction(type=spec.action, start=start, end=end))
    for bubble in bubbles:
        if not bubble.brows:
            continue
        start = max(0.0, float(bubble.start))
        actions.append(
            AvatarAction(type="brows", start=start, end=start + DEFAULT_BROWS_HOLD_SECONDS)
        )
    actions.sort(key=lambda action: action.start)
    return actions


def bubble_start_times(bubbles: Sequence[Bubble]) -> list[float]:
    return [bubble.start for bubble in bubbles]


def bubble_timing(bubble: Bubble, time: float) -> tuple[float, float]:
    """Return ``(opacity, scale)`` for ``bubble`` at ``time``."""
    if time < bubble.start or time >= bubble.end:
        return 0.0, 0.0
    elapsed = time - bubble.start
    duration = max(1e-6, bubble.end - bubble.start)
    if elapsed < POP_IN_SECONDS:
        unit = max(0.0, min(1.0, elapsed / POP_IN_SECONDS))
        scale = 1.1 * (unit / 0.6) if unit < 0.6 else 1.1 - 0.1 * ((unit - 0.6) / 0.4)
    else:
        scale = 1.0
    fade_start = duration - FADE_OUT_SECONDS
    if elapsed > fade_start:
        opacity = max(0.0, min(1.0, (bubble.end - time) / FADE_OUT_SECONDS))
    else:
        opacity = 1.0
    return opacity, max(0.0, scale)


# -- font helpers ------------------------------------------------------


def _font_path() -> Path | None:
    for candidate in (*FIRA_BOLD_PATHS, *BOLD_SANS_PATHS):
        if candidate.exists():
            return candidate
    return None


def text_font_path() -> Path | None:
    """Return the bold sans font used for bubble text, if one is installed."""
    return _font_path()


def _load_text_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    if path is not None:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def _line_height(
    draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont | ImageFont.ImageFont
) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, int(bbox[3] - bbox[1]))


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: float,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def target_font_size(avatar_size: int) -> int:
    """Font target for bubbles: about 8.5% of the rendered avatar height."""
    return max(MIN_FONT_SIZE, int(round(max(1, avatar_size) * TARGET_FONT_RATIO)))


def bubble_art_limits(avatar_size: int) -> tuple[int, int]:
    """Wrap/art hints for a bubble beside an avatar of ``avatar_size`` pixels."""
    width = max(24, int(round(max(1, avatar_size) * MAX_BUBBLE_WIDTH_RATIO)))
    height = max(24, int(round(max(1, avatar_size) * 1.7)))
    return width, height


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    target_size: int,
    max_width: float,
    emoji: str | None = None,
) -> tuple[
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    list[str],
    int,
    Image.Image | None,
    int,
    int,
]:
    min_size = min(MIN_FONT_SIZE, target_size)
    for size in range(target_size, min_size - 1, -1):
        font = _load_text_font(size)
        emoji_image = _render_emoji(emoji, max(1, int(round(size * 1.15))))
        gap = max(3, size // 4) if emoji_image is not None else 0
        padding = max(6, int(math.ceil(size * 0.6)))
        emoji_width = emoji_image.width if emoji_image is not None else 0
        available = max(1.0, max_width - emoji_width - gap - 2 * padding)
        lines = _wrap(draw, text, font, available)
        if len(lines) <= 2:
            return font, lines, size, emoji_image, gap, padding
    size = min_size
    font = _load_text_font(size)
    emoji_image = _render_emoji(emoji, max(1, int(round(size * 1.15))))
    gap = max(3, size // 4) if emoji_image is not None else 0
    padding = max(6, int(math.ceil(size * 0.6)))
    emoji_width = emoji_image.width if emoji_image is not None else 0
    available = max(1.0, max_width - emoji_width - gap - 2 * padding)
    lines = _wrap(draw, text, font, available)
    if len(lines) > 2:
        lines = lines[:2]
        while len(lines[-1]) > 1 and draw.textlength(f"{lines[-1]}…", font=font) > available:
            lines[-1] = lines[-1][:-1]
        lines[-1] = f"{lines[-1]}…"
    return font, lines, size, emoji_image, gap, padding


def _render_emoji(emoji: str | None, target_height: int) -> Image.Image | None:
    if not emoji or not EMOJI_FONT_PATH.exists():
        return None
    try:
        font = ImageFont.truetype(str(EMOJI_FONT_PATH), 109)
        scratch = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
        ImageDraw.Draw(scratch).text((0, 0), emoji, font=font, embedded_color=True)
        bbox = scratch.getbbox()
        if bbox is None:
            return None
        glyph = scratch.crop(bbox)
        ratio = max(1, target_height) / max(1, glyph.height)
        size = (max(1, int(glyph.width * ratio)), max(1, int(glyph.height * ratio)))
        return glyph.resize(size, Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


# -- drawing -----------------------------------------------------------


def _alpha_composite_clipped(
    base: Image.Image,
    image: Image.Image,
    xy: tuple[int, int],
) -> None:
    x, y = xy
    source_x = max(0, -x)
    source_y = max(0, -y)
    dest_x = max(0, x)
    dest_y = max(0, y)
    width = min(image.width - source_x, base.width - dest_x)
    height = min(image.height - source_y, base.height - dest_y)
    if width <= 0 or height <= 0:
        return
    patch = image.crop((source_x, source_y, source_x + width, source_y + height))
    base.alpha_composite(patch, (dest_x, dest_y))


def _bubble_mask(
    width: int,
    height: int,
    *,
    bubble_box: tuple[int, int, int, int],
    tail: list[tuple[float, float]],
) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    left, top, right, bottom = bubble_box
    radius = max(6, int((bottom - top) * 0.32))
    draw.rounded_rectangle((left, top, right, bottom), radius=radius, fill=255)
    bump = max(4, int((bottom - top) * 0.16))
    span = max(1, right - left)
    for fraction in (0.18, 0.4, 0.62, 0.84):
        centre = left + span * fraction
        draw.ellipse((centre - bump, top - bump, centre + bump, top + bump), fill=255)
    for fraction in (0.3, 0.55, 0.78):
        centre = left + span * fraction
        draw.ellipse((centre - bump, bottom - bump, centre + bump, bottom + bump), fill=255)
    draw.polygon(tail, fill=255)
    return mask


def render_bubble_art(
    bubble: Bubble,
    *,
    max_width: int,
    max_height: int,
    avatar_size: int | None = None,
    target_size: int | None = None,
) -> BubbleArt:
    """Render one bubble from its measured text, ready to be scaled per frame."""
    max_width = max(24, int(max_width))
    max_height = max(24, int(max_height))
    if avatar_size is None:
        avatar_size = max(1, int(round(max_width / MAX_BUBBLE_WIDTH_RATIO)))
    font_target = target_size if target_size is not None else target_font_size(avatar_size)
    bubble_limit = max(
        24,
        min(max_width, int(round(avatar_size * MAX_BUBBLE_WIDTH_RATIO))),
    )

    scratch = ImageDraw.Draw(Image.new("L", (1, 1), 0))
    font, lines, font_size, emoji, emoji_gap, padding = _fit_text(
        scratch,
        bubble.text,
        target_size=font_target,
        max_width=bubble_limit,
        emoji=bubble.emoji,
    )
    line_height = _line_height(scratch, font)
    metrics = [scratch.textbbox((0, 0), line, font=font) for line in lines]
    widest = max((bbox[2] - bbox[0] for bbox in metrics), default=0)
    ink_heights = [bbox[3] - bbox[1] for bbox in metrics]
    text_block_height = (
        (len(lines) - 1) * line_height + max(ink_heights, default=line_height)
        if lines
        else line_height
    )
    emoji_width = emoji.width if emoji is not None else 0
    emoji_height = emoji.height if emoji is not None else 0
    content_width = int(math.ceil(widest)) + emoji_width + emoji_gap
    content_height = int(max(text_block_height, emoji_height))

    bubble_width = max(padding * 3, content_width + 2 * padding)
    bubble_height = int(max(padding * 2 + line_height, content_height + 2 * padding))

    bump = max(4, int(bubble_height * 0.16))
    tail_width = max(10, int(round(font_size * 1.2)))
    tail_height = max(8, int(round(font_size * 0.9)))
    shadow_blur = max(1.0, font_size * 0.16)
    shadow_offset = max(2, int(round(font_size * 0.12)))
    shadow_margin = int(math.ceil(shadow_blur * 3)) + shadow_offset + 2
    overshoot = max(2, int(math.ceil(max(bubble_width, bubble_height) * (POP_SCALE - 1.0))))

    left_margin = overshoot + max(bump, OUTLINE_PIXELS + shadow_margin)
    top_margin = overshoot + bump + OUTLINE_PIXELS + shadow_margin
    right_margin = overshoot + max(
        bump,
        OUTLINE_PIXELS + shadow_margin,
        int(round(tail_width * 0.5)) + OUTLINE_PIXELS,
    )
    bottom_margin = overshoot + max(bump, tail_height) + OUTLINE_PIXELS + shadow_margin

    art_width = left_margin + bubble_width + right_margin
    art_height = top_margin + bubble_height + bottom_margin
    left = left_margin
    top = top_margin
    right = left + bubble_width
    bottom = top + bubble_height
    tail_tip = (
        right + int(round(tail_width * 0.5)),
        bottom + tail_height,
    )
    tail: list[tuple[float, float]] = [
        (left + (right - left) * 0.62, bottom - 2),
        (right - 2, bottom - 2),
        tail_tip,
    ]
    mask = _bubble_mask(
        art_width,
        art_height,
        bubble_box=(left, top, right, bottom),
        tail=tail,
    )

    dilated = mask.filter(ImageFilter.MaxFilter(OUTLINE_PIXELS * 2 + 1))
    ring = ImageChops.subtract(dilated, mask)

    art = Image.new("RGBA", (art_width, art_height), (0, 0, 0, 0))
    shadow_alpha = mask.filter(ImageFilter.GaussianBlur(shadow_blur)).point(
        lambda value: int(value * 0.32)
    )
    shadow_layer = Image.new("RGBA", (art_width, art_height), (10, 20, 40, 255))
    shadow_layer.putalpha(shadow_alpha)
    _alpha_composite_clipped(art, shadow_layer, (0, shadow_offset))

    outline_layer = Image.new("RGBA", (art_width, art_height), OUTLINE_COLOUR)
    outline_layer.putalpha(ring)
    art.alpha_composite(outline_layer)

    fill_layer = Image.new("RGBA", (art_width, art_height), (255, 255, 255, 255))
    fill_layer.putalpha(mask)
    art.alpha_composite(fill_layer)

    text_layer = Image.new("RGBA", (art_width, art_height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    content_x = left + max(padding, (bubble_width - content_width) // 2)
    content_y = int(top + max(padding, (bubble_height - content_height) // 2))
    if emoji is not None:
        emoji_y = content_y + max(0, (content_height - emoji.height) // 2)
        text_layer.alpha_composite(emoji, (content_x, emoji_y))
    text_x = content_x + emoji_width + emoji_gap
    text_y = content_y + max(0, (content_height - text_block_height) // 2)
    for index, (line, bbox) in enumerate(zip(lines, metrics, strict=True)):
        text_draw.text(
            (text_x - bbox[0], text_y + index * line_height - bbox[1]),
            line,
            font=font,
            fill=TEXT_COLOUR,
        )
    art.alpha_composite(text_layer)

    text_box = (
        content_x,
        content_y,
        content_x + content_width,
        content_y + content_height,
    )
    return BubbleArt(
        image=art,
        tail=(int(tail_tip[0]), int(tail_tip[1])),
        bubble_box=(left, top, right, bottom),
        text_box=text_box,
        font_size=font_size,
        lines=tuple(lines),
        mask=mask,
    )


def bubble_origin(art: BubbleArt, *, margin: int = 0) -> tuple[int, int]:
    """Stage position for ``art`` so its 110% pop-in stays inside the stage."""
    overshoot_x = int(math.ceil(art.tail[0] * (POP_SCALE - 1.0)))
    overshoot_y = int(math.ceil(art.tail[1] * (POP_SCALE - 1.0)))
    return max(int(margin), overshoot_x), max(int(margin), overshoot_y)


def bubble_extent(art: BubbleArt, origin: tuple[int, int]) -> tuple[int, int]:
    """Right/bottom extent of ``art`` at pop-in scale, from ``origin``."""
    left = origin[0] - (POP_SCALE - 1.0) * art.tail[0]
    top = origin[1] - (POP_SCALE - 1.0) * art.tail[1]
    return (
        int(math.ceil(left + art.image.width * POP_SCALE)),
        int(math.ceil(top + art.image.height * POP_SCALE)),
    )


def draw_bubble(
    stage: Image.Image,
    bubble: Bubble,
    art: BubbleArt,
    origin: tuple[int, int],
    time: float,
) -> None:
    """Draw ``bubble`` on ``stage`` when it is visible at ``time``."""
    opacity, scale = bubble_timing(bubble, time)
    if opacity <= 0.0 or scale <= 0.0:
        return
    image = art.image
    if abs(scale - 1.0) > 1e-6:
        size = (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        )
        image = image.resize(size, Image.Resampling.LANCZOS)
    if opacity < 1.0:
        alpha = image.getchannel("A").point(lambda value: int(value * opacity))
        image = image.copy()
        image.putalpha(alpha)
    tail_x, tail_y = art.tail
    anchor_x = origin[0] + tail_x
    anchor_y = origin[1] + tail_y
    left = int(round(anchor_x - tail_x * scale))
    top = int(round(anchor_y - tail_y * scale))
    _alpha_composite_clipped(stage, image, (left, top))


__all__ = [
    "ACTION_TYPES",
    "AvatarAction",
    "BOLD_SANS_PATHS",
    "Bubble",
    "BubbleArt",
    "BubbleError",
    "BubbleSpec",
    "DEFAULT_BROWS_HOLD_SECONDS",
    "DEFAULT_GLANCE_HOLD_SECONDS",
    "DEFAULT_HOLD_SECONDS",
    "EMOJI_FONT_PATH",
    "FADE_OUT_SECONDS",
    "FIRA_BOLD_PATHS",
    "OUTLINE_COLOUR",
    "POP_IN_SECONDS",
    "bubble_art_limits",
    "bubble_extent",
    "bubble_origin",
    "bubble_start_times",
    "bubble_timing",
    "draw_bubble",
    "load_bubbles",
    "parse_bubbles",
    "render_bubble_art",
    "resolve_actions",
    "resolve_bubbles",
    "schedule_bubbles",
    "target_font_size",
    "text_font_path",
]
