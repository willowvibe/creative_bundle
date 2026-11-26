"""Render a composite editorial scene: type laid over or beside a still.

A composite scene is the workhorse plate of an essay explainer. Unlike a
``still`` scene (one image, full bleed) or a ``typographic`` scene (text on a
flat colour), it places the image and the type in one designed arrangement:

``split``      a mounted still on one side, a text column on the other
``over_media`` a full-bleed still with a scrim and a title block
``compare``    two stills side by side, each under its own label
``quote``      a pull quote on the paper plate
``section``    a chapter divider with a large number

The renderer is pure and CSS-only, like the map and typographic renderers: it
returns markup plus a scoped stylesheet, and the adapter turns the ``media``
entries it reports into root-level media clips. The plate is authored twice —
16:9 for a landscape frame and 9:16 for a portrait one — and fitted to the
canvas with one uniform scale, so a frame that matches neither aspect
letterboxes in paper rather than stretches. A portrait canvas reflows the
composition (a split stacks, a compare stacks) instead of shrinking the
landscape layout into unreadable type.
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from video_editor_core.composition import MotionSpec
from video_editor_core.cues import declared_cue, find_cue, resolve_cues
from video_editor_core.designs import DesignTokens
from video_editor_core.fonts import font_stack
from video_editor_core.motion.registry import MotionError, build_motion
from video_editor_core.paper import colors_for, paper_css, paper_layers
from video_editor_core.script_text import NON_LATIN_CSS, tag_non_latin_text

DESIGN_WIDTH = 1920
DESIGN_HEIGHT = 1080
#: The portrait plate. A phone frame is read at arm's length, so a 16:9 plate
#: letterboxed into it leaves the type too small and every column too narrow
#: to hold a line; a composite scene reflows onto this plate instead.
PORTRAIT_WIDTH = 1080
PORTRAIT_HEIGHT = 1920
ENTRY_SECONDS = 0.35

#: Design-unit rects: (x, y, width) and (x, y, width, height).
Rect3 = tuple[float, float, float]
Rect4 = tuple[float, float, float, float]

LAYOUTS = frozenset({"split", "over_media", "compare", "quote", "section"})

#: Type sizes in design units, shared by both plates. Running text keeps its
#: size across plates because it is read at the same physical distance; the
#: portrait overrides only step the display sizes up.
#:
#: No reading text sits below the craft check's floor: it treats 2.6% of a
#: frame's short side as the smallest a phone can read (28 px on a 1080-wide
#: plate) and warns on anything smaller that holds for half a second. Small
#: caps, credits and badge subtext are still reading copy, so they take the
#: floor too rather than being waved through.
_TYPE: dict[str, float] = {
    "eyebrow": 28.0,
    "headline": 84.0,
    "body": 30.0,
    "stat_value": 88.0,
    "stat_label": 28.0,
    "fact": 28.0,
    "fact_num": 28.0,
    "credit": 28.0,
    "subtitle": 28.0,
    "label": 28.0,
    "caption": 28.0,
    "quote": 64.0,
    "quote_mark": 240.0,
    "attribution": 28.0,
    "role": 28.0,
    "section_num": 360.0,
    "section_headline": 104.0,
    "badge_disc": 70.0,
    "badge_initial": 32.0,
    "badge_name": 28.0,
    "badge_sub": 28.0,
    "mark": 28.0,
}

_PORTRAIT_TYPE: dict[str, float] = {
    **_TYPE,
    "headline": 72.0,
    "stat_value": 80.0,
    "quote": 60.0,
    "quote_mark": 300.0,
    "section_num": 420.0,
    "section_headline": 96.0,
    "badge_disc": 76.0,
    "badge_initial": 34.0,
}

#: Props a scene may use to recolour the plate without a whole design. The
#: map plate's ``ghost``/``pin`` colours are not meaningful here. ``eyebrow`` is
#: absent: in a composite scene that prop is display copy, not a colour.
_OVERRIDABLE = (
    "paper",
    "paper_light",
    "paper_edge",
    "ink",
    "muted",
    "highlight",
    "rule",
)

#: Props that recolour a plate under a name the display copy does not use.
_PROP_COLOURS = {"eyebrow_color": "eyebrow"}


class CompositeSceneError(ValueError):
    """A composite scene whose props cannot be rendered."""


@dataclass(frozen=True)
class CompositeMedia:
    """One image the layout places, and where it goes on the canvas."""

    role: str
    asset_index: int
    #: Canvas pixels: (x, y, width, height).
    rect: tuple[int, int, int, int]


@dataclass(frozen=True)
class CompositeSceneRender:
    """Markup, CSS, media placements and an optional paper backdrop."""

    markup: str
    css: str
    media: tuple[CompositeMedia, ...] = ()
    #: Paper markup for its own layer when media does not cover the canvas.
    background: str = ""


@dataclass(frozen=True)
class _Geom:
    """One authored plate: its canvas, its layout slots and its type scale."""

    width: float
    height: float
    type: Mapping[str, float]
    #: ``split``: the still and the text column, for each ``side`` value.
    split_media: Rect4
    split_media_alt: Rect4
    split_text: Rect3
    split_text_alt: Rect3
    #: ``compare``: the two slots in order; each label sits under its still.
    compare: tuple[Rect4, Rect4]
    compare_label_gap: float
    compare_headline: Rect3
    compare_caption: tuple[float, float]
    #: ``over_media``: the title block, as (left, bottom, width).
    over_media_title: Rect3
    quote_mark: tuple[float, float]
    quote_block: Rect3
    section_num: tuple[float, float]
    section_block: Rect3
    badge: tuple[float, float]
    #: Watermark inset, as (right, bottom).
    mark: tuple[float, float]
    eyebrow_flow: tuple[float, float]


#: The landscape plate, authored for 16:9.
_LANDSCAPE = _Geom(
    width=float(DESIGN_WIDTH),
    height=float(DESIGN_HEIGHT),
    type=_TYPE,
    split_media=(90.0, 110.0, 760.0, 860.0),
    split_media_alt=(1070.0, 110.0, 760.0, 860.0),
    split_text=(930.0, 170.0, 870.0),
    split_text_alt=(120.0, 170.0, 870.0),
    compare=((120.0, 270.0, 760.0, 520.0), (1040.0, 270.0, 760.0, 520.0)),
    compare_label_gap=40.0,
    compare_headline=(120.0, 150.0, 1680.0),
    compare_caption=(120.0, 960.0),
    over_media_title=(120.0, 96.0, 1120.0),
    quote_mark=(196.0, 150.0),
    quote_block=(220.0, 280.0, 1480.0),
    section_num=(108.0, 80.0),
    section_block=(140.0, 440.0, 1500.0),
    badge=(120.0, 74.0),
    mark=(120.0, 74.0),
    eyebrow_flow=(120.0, 64.0),
)

#: The portrait plate, authored for 9:16. A split stacks the still above the
#: text column (below it for ``side: right``) and a compare stacks its two
#: stills; a side-by-side arrangement cannot hold a caption on a phone frame.
_PORTRAIT = _Geom(
    width=float(PORTRAIT_WIDTH),
    height=float(PORTRAIT_HEIGHT),
    type=_PORTRAIT_TYPE,
    split_media=(72.0, 200.0, 936.0, 760.0),
    split_media_alt=(72.0, 1000.0, 936.0, 760.0),
    split_text=(72.0, 1040.0, 936.0),
    split_text_alt=(72.0, 150.0, 936.0),
    compare=((72.0, 400.0, 936.0, 520.0), (72.0, 1060.0, 936.0, 520.0)),
    compare_label_gap=36.0,
    compare_headline=(72.0, 160.0, 936.0),
    compare_caption=(72.0, 1740.0),
    over_media_title=(72.0, 120.0, 936.0),
    quote_mark=(64.0, 540.0),
    quote_block=(80.0, 700.0, 920.0),
    section_num=(56.0, 110.0),
    section_block=(80.0, 780.0, 920.0),
    badge=(72.0, 100.0),
    mark=(72.0, 74.0),
    eyebrow_flow=(72.0, 72.0),
)


def _geom_for(canvas_w: float, canvas_h: float) -> _Geom:
    """The authored plate closest to the canvas.

    A frame at least as tall as it is wide is a phone frame: use the portrait
    plate. Everything else uses the landscape plate, including 16:9 itself.
    """
    return _PORTRAIT if canvas_h >= canvas_w else _LANDSCAPE


@dataclass(frozen=True)
class _Item:
    """One revealable block: markup, the words that cue it, and its class."""

    html: str
    cue: str = ""
    class_name: str = "comp-line"


@dataclass(frozen=True)
class _Fact:
    text: str
    cue: str


def _seconds(duration: float) -> float:
    try:
        value = float(duration)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0.0 else 0.0


def _entry_seconds(design: DesignTokens) -> float:
    try:
        tempo = float(design.motion.tempo)
    except (TypeError, ValueError):
        return ENTRY_SECONDS
    if not math.isfinite(tempo) or tempo <= 0.0:
        return ENTRY_SECONDS
    return ENTRY_SECONDS / tempo


def _text(props: Mapping[str, Any], key: str) -> str:
    return str(props.get(key) or "").strip()


def _esc(value: str) -> str:
    return html.escape(value)


def _layout(props: Mapping[str, Any]) -> str:
    layout = _text(props, "layout")
    if layout not in LAYOUTS:
        raise CompositeSceneError(
            f"unknown composite layout {layout!r}; choose one of {sorted(LAYOUTS)}"
        )
    return layout


def _facts(props: Mapping[str, Any]) -> list[_Fact]:
    raw = props.get("facts")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CompositeSceneError("composite `facts` must be a list of strings or {text, cue} maps")
    facts: list[_Fact] = []
    for entry in raw:
        if isinstance(entry, str):
            text = entry.strip()
            cue = text
        elif isinstance(entry, Mapping):
            text = str(entry.get("text") or "").strip()
            cue = declared_cue(entry.get("cue"), text)
        else:
            raise CompositeSceneError(f"a composite fact must be text or a map, got {entry!r}")
        if not text:
            raise CompositeSceneError("a composite fact cannot be empty")
        facts.append(_Fact(text=text, cue=cue))
    return facts


def _eyebrow(props: Mapping[str, Any]) -> list[_Item]:
    eyebrow = _text(props, "eyebrow")
    if not eyebrow:
        return []
    return [
        _Item(
            f'<div class="comp-rule"></div><div class="comp-eyebrow-text">{_esc(eyebrow)}</div>',
            class_name="comp-eyebrow",
        )
    ]


def _headline(
    props: Mapping[str, Any], *, cue: bool = True, class_name: str = "comp-line"
) -> list[_Item]:
    headline = _text(props, "headline")
    if not headline:
        return []
    # A headline is a title: it is revealed on its own words by default. An
    # authored plate whose title the narration never says opts out with
    # ``headline_cue: false`` instead of failing the cue gate.
    if props.get("headline_cue") is False:
        cue = False
    return [
        _Item(
            f'<div class="comp-headline">{_esc(headline)}</div>',
            cue=headline if cue else "",
            class_name=class_name,
        )
    ]


def _body(props: Mapping[str, Any]) -> list[_Item]:
    body = _text(props, "body")
    if not body:
        return []
    return [
        _Item(
            f'<div class="comp-body">{_esc(body)}</div>',
            cue=_text(props, "cue"),
            class_name="comp-body-row",
        )
    ]


def _stat(props: Mapping[str, Any]) -> list[_Item]:
    raw = props.get("stat")
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        raise CompositeSceneError("composite `stat` must be a map with `value`")
    value = str(raw.get("value") or "").strip()
    if not value:
        raise CompositeSceneError("composite `stat` needs a `value`")
    label = str(raw.get("label") or "").strip()
    cue = declared_cue(raw.get("cue"), value)
    label_markup = f'<span class="comp-stat-label">{_esc(label)}</span>' if label else ""
    return [
        _Item(
            f'<div class="comp-stat"><span class="comp-stat-value">{_esc(value)}</span>'
            f"{label_markup}</div>",
            cue=cue,
            class_name="comp-stat-row",
        )
    ]


def _credit(props: Mapping[str, Any]) -> list[_Item]:
    credit = _text(props, "credit")
    if not credit:
        return []
    return [_Item(f'<div class="comp-credit">{_esc(credit)}</div>', class_name="comp-credit-row")]


def _fact_items(props: Mapping[str, Any]) -> list[_Item]:
    return [
        _Item(
            f'<span class="comp-fact-num">{position:02d}</span>'
            f'<span class="comp-fact-text">{_esc(fact.text)}</span>',
            cue=fact.cue,
            class_name="comp-fact",
        )
        for position, fact in enumerate(_facts(props), start=1)
    ]


def _split_items(props: Mapping[str, Any]) -> list[_Item]:
    content = [
        *_headline(props),
        *_body(props),
        *_stat(props),
        *_fact_items(props),
    ]
    if not content:
        # An eyebrow and a credit are decoration; a split plate with nothing
        # but decoration leaves half the frame empty by mistake.
        raise CompositeSceneError("a split scene needs a headline, body, stat or facts")
    return [*_eyebrow(props), *content, *_credit(props)]


def _over_media_items(props: Mapping[str, Any]) -> list[_Item]:
    items = [
        *_eyebrow(props),
        *_headline(props),
        *_body(props),
    ]
    subtitle = _text(props, "subtitle")
    if subtitle:
        items.append(
            _Item(
                f'<div class="comp-subtitle">{_esc(subtitle)}</div>', class_name="comp-subtitle-row"
            )
        )
    if not items:
        raise CompositeSceneError("an over_media scene needs a headline, body or subtitle")
    return items


def _labels(props: Mapping[str, Any]) -> list[tuple[str, str]]:
    raw = props.get("labels")
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 2:
        raise CompositeSceneError("compare needs `labels` with exactly 2 entries")
    labels: list[tuple[str, str]] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            text = str(entry.get("text") or "").strip()
            cue = declared_cue(entry.get("cue"), text)
        else:
            text = str(entry or "").strip()
            cue = text
        if not text:
            raise CompositeSceneError("compare labels must be non-empty")
        labels.append((text, cue))
    return labels


def _compare_items(props: Mapping[str, Any]) -> list[_Item]:
    labels = _labels(props)
    items = [*_eyebrow(props), *_headline(props, class_name="comp-top-headline")]
    # ``first``/``second`` are logical slots: side by side on the landscape
    # plate, stacked on the portrait plate. The names never promise a side.
    for slot, (text, cue) in zip(("first", "second"), labels, strict=True):
        items.append(
            _Item(
                f'<div class="comp-label-rule"></div>'
                f'<div class="comp-label-text">{_esc(text)}</div>',
                cue=cue,
                class_name=f"comp-label is-{slot}",
            )
        )
    caption = _text(props, "caption")
    if caption:
        items.append(
            _Item(f'<div class="comp-caption">{_esc(caption)}</div>', class_name="comp-caption-row")
        )
    return items


def _quote_items(props: Mapping[str, Any]) -> list[_Item]:
    quote = _text(props, "quote")
    if not quote:
        raise CompositeSceneError("a quote scene needs a `quote`")
    items = [*_eyebrow(props)]
    items.append(
        _Item(
            f'<div class="comp-quote">{_esc(quote)}</div>',
            cue=_text(props, "cue"),
            class_name="comp-quote-row",
        )
    )
    attribution = _text(props, "attribution")
    if attribution:
        items.append(
            _Item(
                f'<div class="comp-attribution-rule"></div>'
                f'<div class="comp-attribution">{_esc(attribution)}</div>',
                cue=_text(props, "attribution_cue"),
                class_name="comp-attribution-row",
            )
        )
    role = _text(props, "role")
    if role:
        items.append(
            _Item(f'<div class="comp-role">{_esc(role)}</div>', class_name="comp-role-row")
        )
    return items


def _section_items(props: Mapping[str, Any]) -> list[_Item]:
    headline = _headline(props)
    if not headline:
        raise CompositeSceneError("a section scene needs a headline")
    items = [*_eyebrow(props), *headline]
    subtitle = _text(props, "subtitle")
    if subtitle:
        items.append(
            _Item(
                f'<div class="comp-subtitle">{_esc(subtitle)}</div>', class_name="comp-subtitle-row"
            )
        )
    return items


def _items_for(layout: str, props: Mapping[str, Any]) -> list[_Item]:
    if layout == "split":
        return _split_items(props)
    if layout == "over_media":
        return _over_media_items(props)
    if layout == "compare":
        return _compare_items(props)
    if layout == "quote":
        return _quote_items(props)
    return _section_items(props)


def composite_scene_cues(scene: Mapping[str, Any]) -> dict[str, str]:
    """``{item_N: phrase}`` for every item the narration can cue."""
    props = dict(scene.get("props") or {})
    layout = _layout(props)
    return {
        f"item_{position}": item.cue
        for position, item in enumerate(_items_for(layout, props))
        if item.cue
    }


def _silenced_entry(entry: Any, words: Sequence[Mapping[str, Any]]) -> Any:
    """A fact/label entry whose default cue the narration never says."""
    if isinstance(entry, str):
        if entry.strip() and find_cue(words, entry) is None:
            return {"text": entry, "cue": False}
        return entry
    if isinstance(entry, Mapping):
        entry = dict(entry)
        text = str(entry.get("text") or "")
        if text.strip() and find_cue(words, declared_cue(entry.get("cue"), text)) is None:
            entry["cue"] = False
        return entry
    return entry


def silence_unspoken_composite_cues(
    props: Mapping[str, Any], words: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``props`` with every default cue the narration never says switched off.

    The renderer derives a cue from display text when the author names none: a
    headline's words, a stat's value, a fact's text, a compare label. That is
    right when the voice says the text and impossible to resolve when it does
    not, so drafting can ask which defaults will never match and turn only
    those off. Authored cue strings are left alone; the draft layer drops the
    ones that cannot resolve.
    """
    out = dict(props)
    headline = _text(out, "headline")
    if headline and find_cue(words, headline) is None:
        out["headline_cue"] = False

    layout = _text(out, "layout")
    if layout == "split":
        stat = out.get("stat")
        if isinstance(stat, Mapping):
            stat = dict(stat)
            phrase = declared_cue(stat.get("cue"), str(stat.get("value") or ""))
            if find_cue(words, phrase) is None:
                stat["cue"] = False
            out["stat"] = stat
        facts = out.get("facts")
        if isinstance(facts, Sequence) and not isinstance(facts, str):
            out["facts"] = [_silenced_entry(entry, words) for entry in facts]
    elif layout == "compare":
        labels = out.get("labels")
        if isinstance(labels, Sequence) and not isinstance(labels, str):
            out["labels"] = [_silenced_entry(entry, words) for entry in labels]
    return out


def _media_for(layout: str, props: Mapping[str, Any], geom: _Geom) -> list[tuple[str, int, Rect4]]:
    """The images a layout places, in design units of ``geom``."""
    if layout == "split":
        side = _text(props, "side").lower()
        rect = geom.split_media_alt if side == "right" else geom.split_media
        return [("hero", 0, rect)]
    if layout == "over_media":
        return [("hero", 0, (0.0, 0.0, geom.width, geom.height))]
    if layout == "compare":
        return [("first", 0, geom.compare[0]), ("second", 1, geom.compare[1])]
    return []


def _split_side(props: Mapping[str, Any]) -> str:
    return "right" if _text(props, "side").lower() == "right" else "left"


def _frames(media: Sequence[tuple[str, int, Rect4]], *, scale: float) -> str:
    """Paper mats drawn over the media edges; split and compare only."""
    parts: list[str] = []
    for _role, _asset, (x, y, w, h) in media:
        parts.append(
            f'<div class="comp-frame" style="left:{x * scale:.1f}px;top:{y * scale:.1f}px;'
            f'width:{w * scale:.1f}px;height:{h * scale:.1f}px"></div>'
        )
    return "".join(parts)


def _badge(props: Mapping[str, Any]) -> str:
    name = _text(props, "badge")
    if not name:
        return ""
    sub = _text(props, "badge_sub")
    monogram = _esc(_text(props, "badge_mark") or name[:1].upper())
    sub_markup = f'<div class="comp-badge-sub">{_esc(sub)}</div>' if sub else ""
    return (
        '<div class="comp-badge">'
        f'<div class="comp-badge-disc"><span>{monogram}</span></div>'
        f'<div class="comp-badge-text"><div class="comp-badge-name">{_esc(name)}</div>'
        f"{sub_markup}</div>"
        "</div>"
    )


def _watermark(props: Mapping[str, Any]) -> str:
    watermark = _text(props, "watermark")
    return f'<div class="comp-mark">{_esc(watermark)}</div>' if watermark else ""


def _static_markup(
    layout: str,
    props: Mapping[str, Any],
    media: Sequence[tuple[str, int, Rect4]],
    *,
    scale: float,
) -> str:
    parts: list[str] = []
    if layout in {"split", "compare"}:
        parts.append(_frames(media, scale=scale))
    if layout == "over_media" and media:
        parts.append('<div class="comp-scrim"></div>')
    if layout == "quote":
        parts.append('<div class="comp-quote-mark">“</div>')
    if layout == "section":
        number = _text(props, "number") or "01"
        parts.append(f'<div class="comp-section-num">{_esc(number)}</div>')
    return "".join(parts)


def _container_class(layout: str, props: Mapping[str, Any]) -> str:
    if layout == "split":
        return f"comp-text is-{_split_side(props)}"
    if layout == "over_media":
        return "comp-title-block"
    if layout == "quote":
        return "comp-quote-block"
    if layout == "section":
        return "comp-section-block"
    return "comp-flow"


def _delays(
    items: Sequence[_Item],
    *,
    seconds: float,
    entry: float,
    words: Sequence[Mapping[str, Any]] | None,
) -> list[float]:
    cues = {f"item_{position}": item.cue for position, item in enumerate(items) if item.cue}
    times: Mapping[str, float] = (
        resolve_cues(words or [], cues, lead=0.0) if (words and cues) else {}
    )
    stagger = min(0.38, max(0.10, (seconds - entry) / max(len(items), 1) / 2.4))
    latest = max(0.0, seconds - entry)
    delays: list[float] = []
    for position in range(len(items)):
        fallback = entry * 0.6 + position * stagger
        cued = times.get(f"item_{position}")
        resolved = fallback if cued is None or not math.isfinite(cued) else cued
        delays.append(min(latest, max(0.0, resolved)))
    return delays


def _media_rects(
    media: Sequence[tuple[str, int, tuple[float, float, float, float]]],
    *,
    scale: float,
    plate_x: float,
    plate_y: float,
) -> tuple[CompositeMedia, ...]:
    return tuple(
        CompositeMedia(
            role=role,
            asset_index=asset,
            rect=(
                int(round(plate_x + x * scale)),
                int(round(plate_y + y * scale)),
                int(round(w * scale)),
                int(round(h * scale)),
            ),
        )
        for role, asset, (x, y, w, h) in media
    )


def render_composite(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    index: int,
    duration: float,
    width: int = DESIGN_WIDTH,
    height: int = DESIGN_HEIGHT,
    image_count: int = 0,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> CompositeSceneRender:
    """Render ``scene`` for the compiler; ``index`` scopes every selector."""
    index = int(index)
    props = dict(scene.get("props") or {})
    layout = _layout(props)
    seconds = _seconds(duration)
    canvas_w = float(width) if width > 0 else float(DESIGN_WIDTH)
    canvas_h = float(height) if height > 0 else canvas_w * 9.0 / 16.0
    geom = _geom_for(canvas_w, canvas_h)
    scale = min(canvas_w / geom.width, canvas_h / geom.height)
    plate_w = geom.width * scale
    plate_h = geom.height * scale
    plate_x = (canvas_w - plate_w) / 2.0
    plate_y = (canvas_h - plate_h) / 2.0
    entry = _entry_seconds(design)
    colors = colors_for(
        design,
        props,
        section="composite",
        overridable=_OVERRIDABLE,
        aliases=_PROP_COLOURS,
    )
    display = font_stack(design.type.display)
    body = font_stack(design.type.body)

    media = _media_for(layout, props, geom)
    needed = max((asset for _role, asset, _rect in media), default=-1) + 1
    if image_count < needed:
        raise CompositeSceneError(
            f"composite layout {layout!r} needs {needed} image asset(s), got {image_count}"
        )

    items = _items_for(layout, props)
    delays = _delays(items, seconds=seconds, entry=entry, words=words)
    items_markup = "".join(
        f'<div class="comp-item {item.class_name}" style="animation-delay:{delay:.3f}s">'
        f"{item.html}</div>"
        for item, delay in zip(items, delays, strict=True)
    )

    # Only a full-bleed still gets the slow drift. Every other plate holds
    # still: scaling the stage moves the paper mats and the type away from the
    # still they were composed against, which reads as a misprint.
    stage_motion = ""
    stage_animation = "none"
    if layout == "over_media":
        try:
            drift = build_motion(
                MotionSpec(
                    name="drift",
                    params={"from": "scale(1)", "to": f"scale({design.motion.drift:g})"},
                    ease="ease-in-out",
                ),
                clip_duration=seconds,
            )
            stage_motion = drift.keyframes
            stage_animation = drift.declaration
        except MotionError:
            pass

    media_rects = _media_rects(media, scale=scale, plate_x=plate_x, plate_y=plate_y)
    # Any layout the plate does not fully cover (split, compare, and over_media
    # on a canvas that is not 16:9) needs the paper on its own layer *under* the
    # media clips; the media layer is a root-level clip and cannot carry it. A
    # text-only layout (quote, section) draws the paper inline instead.
    background = ""
    paper_markup = ""
    if media_rects:
        background = (
            f'<div class="comp-{index}">'
            + paper_layers(f"comp-{index}", "comp", colors, canvas_w, canvas_h, scale)
            + "</div>"
        )
    else:
        paper_markup = paper_layers(f"comp-{index}", "comp", colors, canvas_w, canvas_h, scale)

    static = _static_markup(layout, props, media, scale=scale)
    if layout == "over_media":
        static += _badge(props)
    static += _watermark(props)

    markup = (
        f'<div class="comp-{index}">'
        f"{paper_markup}"
        f'<div class="comp-plate" style="left:{plate_x:.1f}px;top:{plate_y:.1f}px;'
        f'width:{plate_w:.1f}px;height:{plate_h:.1f}px">'
        f'<div class="comp-stage" style="animation:{stage_animation}">'
        f"{static}"
        f'<div class="{_container_class(layout, props)}">{items_markup}</div>'
        "</div>"
        "</div>"
        "</div>"
    )

    css = _composite_css(
        index,
        layout,
        props,
        colors,
        geom=geom,
        display=display,
        body=body,
        scale=scale,
        entry=entry,
        stage_motion=stage_motion,
    )
    return CompositeSceneRender(
        markup=tag_non_latin_text(markup),
        css=css + NON_LATIN_CSS,
        media=media_rects,
        background=background,
    )


def _composite_css(
    index: int,
    layout: str,
    props: Mapping[str, Any],
    colors: Mapping[str, str],
    *,
    geom: _Geom,
    display: str,
    body: str,
    scale: float,
    entry: float,
    stage_motion: str,
) -> str:
    """Every selector scoped to ``.comp-{index}``; no global CSS."""

    def px(value: float) -> str:
        return f"{value * scale:.1f}px"

    def px_int(value: float) -> str:
        return f"{max(1, round(value * scale))}px"

    type_size = geom.type
    text = geom.split_text_alt if _split_side(props) == "right" else geom.split_text
    text_block = f"left:{px(text[0])}; top:{px(text[1])}; width:{px(text[2])};"

    first, second = geom.compare
    first_label_top = first[1] + first[3] + geom.compare_label_gap
    second_label_top = second[1] + second[3] + geom.compare_label_gap

    overlay_ink = colors["paper_light"] if layout == "over_media" else colors["ink"]
    overlay_muted = colors["paper_light"] if layout == "over_media" else colors["muted"]
    # The badge sits on the still, not on paper, so on over_media its text must
    # take the overlay colour: the design's slate highlight vanishes on a dark
    # photograph.
    badge_ink = colors["paper_light"] if layout == "over_media" else colors["highlight"]
    badge_muted = colors["paper_light"] if layout == "over_media" else colors["muted"]
    # The watermark is a texture, but the craft check measures its declared
    # colour against the scrim, not its opacity: the design's muted ink reads
    # 3.5:1 there. Over media it takes the overlay colour at half opacity,
    # which lands near 4.7:1 once composited; on paper the muted ink already
    # clears 4.5:1.
    mark_ink = colors["paper_light"] if layout == "over_media" else colors["muted"]
    mark_opacity = ".5" if layout == "over_media" else ".6"

    title = geom.over_media_title
    quote = geom.quote_block
    section = geom.section_block
    containers: dict[str, str] = {
        "split": f".comp-{index} .comp-text {{ position:absolute; {text_block} }}",
        "over_media": f".comp-{index} .comp-title-block {{ position:absolute; "
        f"left:{px(title[0])}; bottom:{px(title[1])}; width:{px(title[2])}; }}",
        "compare": f".comp-{index} .comp-flow {{ position:absolute; inset:0; }}",
        "quote": f".comp-{index} .comp-quote-block {{ position:absolute; "
        f"left:{px(quote[0])}; top:{px(quote[1])}; width:{px(quote[2])}; }}",
        "section": f".comp-{index} .comp-section-block {{ position:absolute; "
        f"left:{px(section[0])}; top:{px(section[1])}; width:{px(section[2])}; }}",
    }

    return f"""      {stage_motion}
{paper_css(f"comp-{index}", "comp", colors, scale)}      .comp-{index} {{ position:absolute; inset:0; overflow:hidden; background:transparent; }}
      .comp-{index} .comp-plate {{ position:absolute; overflow:visible; }}
      .comp-{index} .comp-stage {{ position:absolute; inset:0; transform-origin:center; will-change:transform; }}
      @keyframes comp_entry_{index} {{
        from {{ opacity:0; transform:translate3d(0,{px(18)},0); }}
        to {{ opacity:1; transform:translate3d(0,0,0); }}
      }}
      .comp-{index} .comp-item {{ opacity:0; animation: comp_entry_{index} {entry:.3f}s ease-out both; }}
      {containers[layout]}
      .comp-{index} .comp-eyebrow {{ margin-bottom:{px(20)}; }}
      .comp-{index} .comp-rule {{ width:{px(72)}; height:{px_int(3)}; background:{colors["rule"]}; }}
      .comp-{index} .comp-eyebrow-text {{ margin-top:{px(18)}; font-family:{body};
        font-size:{px(type_size["eyebrow"])}; font-weight:600; letter-spacing:.34em;
        text-transform:uppercase; color:{colors["eyebrow"]}; }}
      .comp-{index} .comp-headline {{ font-family:{display};
        font-weight:{600}; font-size:{px(type_size["headline"])}; line-height:1.02;
        letter-spacing:-0.005em; color:{overlay_ink}; }}
      .comp-{index} .comp-body {{ margin-top:{px(26)}; font-family:{body};
        font-size:{px(type_size["body"])}; line-height:1.5; color:{overlay_muted};
        max-width:40ch; }}
      .comp-{index} .comp-stat {{ margin-top:{px(38)}; }}
      .comp-{index} .comp-stat-value {{ font-family:{display}; font-weight:600;
        font-size:{px(type_size["stat_value"])}; line-height:1; color:{colors["highlight"]}; }}
      .comp-{index} .comp-stat-label {{ display:block; margin-top:{px(10)};
        font-family:{body}; font-size:{px(type_size["stat_label"])}; font-weight:600;
        letter-spacing:.30em; text-transform:uppercase; color:{colors["muted"]}; }}
      .comp-{index} .comp-fact {{ margin-top:{px(18)}; display:flex; align-items:center;
        gap:{px(22)}; padding-left:{px(22)}; box-sizing:border-box;
        box-shadow:inset {px_int(3)} 0 0 {colors["rule"]}; }}
      .comp-{index} .comp-fact-num {{ font-family:{display}; font-size:{px(type_size["fact_num"])};
        font-weight:600; color:{colors["gold_ink"]}; }}
      .comp-{index} .comp-fact-text {{ font-family:{body}; font-size:{px(type_size["fact"])};
        font-weight:600; letter-spacing:.10em; text-transform:uppercase; color:{overlay_ink}; }}
      .comp-{index} .comp-credit {{ margin-top:{px(40)}; font-family:{body};
        font-size:{px(type_size["credit"])}; font-weight:600; letter-spacing:.32em;
        text-transform:uppercase; color:{overlay_muted}; opacity:.7; }}
      .comp-{index} .comp-subtitle {{ margin-top:{px(18)}; font-family:{body};
        font-size:{px(type_size["subtitle"])}; font-weight:500; letter-spacing:.30em;
        text-transform:uppercase; color:{overlay_muted}; }}
      .comp-{index} .comp-scrim {{ position:absolute; inset:0;
        background:linear-gradient(to top, rgba(20,18,14,0.78) 0%,
          rgba(20,18,14,0.34) 42%, rgba(20,18,14,0) 72%); }}
      .comp-{index} .comp-frame {{ position:absolute;
        box-shadow: 0 0 0 {px(10)} {colors["paper_light"]},
          0 0 0 {px(11)} {colors["paper_edge"]},
          0 {px(18)} {px(40)} rgba(40,30,10,0.14),
          inset 0 0 0 {px_int(1)} rgba(255,255,255,0.35); }}
      .comp-{index} .comp-label {{ position:absolute; }}
      .comp-{index} .comp-label.is-first {{ left:{px(first[0])}; top:{px(first_label_top)};
        width:{px(first[2])}; }}
      .comp-{index} .comp-label.is-second {{ left:{px(second[0])}; top:{px(second_label_top)};
        width:{px(second[2])}; }}
      .comp-{index} .comp-label-rule {{ width:{px(56)}; height:{px_int(3)};
        background:{colors["rule"]}; }}
      .comp-{index} .comp-label-text {{ margin-top:{px(14)}; font-family:{body};
        font-size:{px(type_size["label"])}; font-weight:700; letter-spacing:.20em;
        text-transform:uppercase; color:{colors["highlight"]}; }}
      .comp-{index} .comp-top-headline {{ position:absolute;
        top:{px(geom.compare_headline[1])}; left:{px(geom.compare_headline[0])};
        width:{px(geom.compare_headline[2])}; }}
      .comp-{index} .comp-flow > .comp-item.comp-eyebrow {{ position:absolute;
        left:{px(geom.eyebrow_flow[0])}; top:{px(geom.eyebrow_flow[1])}; }}
      .comp-{index} .comp-caption {{ position:absolute; left:{px(geom.compare_caption[0])};
        top:{px(geom.compare_caption[1])}; font-family:{body};
        font-size:{px(type_size["caption"])}; font-weight:600;
        letter-spacing:.28em; text-transform:uppercase; color:{colors["muted"]}; }}
      .comp-{index} .comp-quote-mark {{ position:absolute; left:{px(geom.quote_mark[0])};
        top:{px(geom.quote_mark[1])}; font-family:{display};
        font-size:{px(type_size["quote_mark"])}; line-height:1;
        color:{colors["rule"]}; opacity:.45; }}
      .comp-{index} .comp-quote {{ font-family:{display}; font-size:{px(type_size["quote"])};
        font-weight:500; line-height:1.25; color:{colors["ink"]}; }}
      .comp-{index} .comp-attribution-rule {{ margin-top:{px(40)}; width:{px(56)};
        height:{px_int(3)}; background:{colors["rule"]}; }}
      .comp-{index} .comp-attribution {{ margin-top:{px(16)}; font-family:{body};
        font-size:{px(type_size["attribution"])}; font-weight:700; letter-spacing:.18em;
        text-transform:uppercase; color:{colors["ink"]}; }}
      .comp-{index} .comp-role {{ margin-top:{px(8)}; font-family:{body};
        font-size:{px(type_size["role"])}; font-weight:500; letter-spacing:.24em;
        text-transform:uppercase; color:{colors["muted"]}; }}
      .comp-{index} .comp-section-num {{ position:absolute; left:{px(geom.section_num[0])};
        top:{px(geom.section_num[1])}; font-family:{display};
        font-size:{px(type_size["section_num"])}; line-height:1;
        font-weight:600; color:{colors["highlight"]}; opacity:.14; }}
      .comp-{index} .comp-section-block .comp-headline {{
        font-size:{px(type_size["section_headline"])}; }}
      .comp-{index} .comp-badge {{ position:absolute; left:{px(geom.badge[0])};
        top:{px(geom.badge[1])}; display:flex; align-items:center; gap:{px(18)}; }}
      .comp-{index} .comp-badge-disc {{ width:{px(type_size["badge_disc"])};
        height:{px(type_size["badge_disc"])};
        border-radius:50%; border:{px_int(1.6)} solid {colors["highlight"]};
        background:{colors["paper_light"]}; display:flex; align-items:center;
        justify-content:center; font-family:{display}; font-weight:600;
        font-size:{px(type_size["badge_initial"])}; color:{colors["highlight"]}; }}
      .comp-{index} .comp-badge-text {{ line-height:1.2; }}
      .comp-{index} .comp-badge-name {{ font-family:{body};
        font-size:{px(type_size["badge_name"])};
        font-weight:700; letter-spacing:.20em; text-transform:uppercase;
        color:{badge_ink}; }}
      .comp-{index} .comp-badge-sub {{ font-family:{body};
        font-size:{px(type_size["badge_sub"])};
        letter-spacing:.30em; text-transform:uppercase; color:{badge_muted};
        margin-top:{px(4)}; }}
      .comp-{index} .comp-mark {{ position:absolute; right:{px(geom.mark[0])};
        bottom:{px(geom.mark[1])}; font-family:{body};
        font-size:{px(type_size["mark"])}; font-weight:600;
        letter-spacing:.40em; text-transform:uppercase; color:{mark_ink};
        opacity:{mark_opacity}; }}
"""
