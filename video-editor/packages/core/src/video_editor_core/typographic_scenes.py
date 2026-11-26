"""Render a typographic scene as HTML for the HyperFrames compiler.

Every layout is a pure function of the scene's ``props`` and the design
tokens, so it can be asserted on with plain strings. The compiler wraps the
returned markup in the root-level ``<div class="clip">`` that HyperFrames needs
for seeking; everything here nests inside it.

Each layout returns its items in reveal order. An item's reveal time is the
moment its own words are spoken when the narration contains them
(``resolve_cues``); otherwise items fall in on a short stagger after the entry
motion. Both paths are bounded by the scene duration, so a late word can never
schedule a reveal after the scene has ended.

A label usually reads differently from the sentence that introduces it — a
chart bar says "Wiz" where the voice says "Google's biggest deal". A
``bar_compare`` bar or a ``sequence`` step may therefore carry an explicit
``cue`` naming the spoken phrase; without one the label text is the cue, and
:func:`~video_editor_pipelines.composition_qa.cue_problems` fails the build when the
cue matches no spoken word.

Every typographic scene carries three motions, none of them optional:

1. an entry rise-and-fade whose length is ``ENTRY_SECONDS / motion.tempo``,
2. per-item reveals cued to the narration (or the bounded stagger fallback),
3. a slow camera drift from ``1`` to ``motion.drift`` across the whole scene.

A held shot therefore never reads as a frozen frame.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from video_editor_core.cues import declared_cue, find_cue, resolve_cues
from video_editor_core.designs import DesignTokens
from video_editor_core.fonts import font_stack
from video_editor_core.icons import IconError, icon_svg
from video_editor_core.script_text import NON_LATIN_CSS, tag_non_latin_text

#: Base entry motion. Divided by the design's ``motion.tempo`` so a bolder
#: design moves faster without every layout knowing about it.
ENTRY_SECONDS = 0.35

#: A big stat's number climbs to its value over this long, in pre-computed
#: steps. Each step is an HTML span in one grid cell, so a seek can show any
#: frame without JavaScript.
COUNT_SECONDS = 1.4
COUNT_STEPS = 14

#: A definition's gloss words arrive across this window after the gloss item
#: itself appears.
WORD_SPREAD_SECONDS = 1.4


class SceneRenderError(ValueError):
    """A typographic scene whose props cannot be rendered."""


@dataclass(frozen=True)
class SceneRender:
    """The scene's inner markup plus the CSS/keyframes it needs."""

    markup: str
    css: str


@dataclass(frozen=True)
class _Item:
    """One revealable block: markup, the words that cue it, and its class.

    ``opening`` marks the piece that gives the layout its meaning (a headline,
    the first step, the first donut slice). The no-empty-opening rule watches
    these pieces instead of an uncued scaffold that happens to reveal first.
    """

    html: str
    cue: str = ""
    class_name: str = "typo-line"
    opening: bool = False
    #: Donut centre only: the running sums (0, after part 1, ...) the centre
    #: steps through, formatted with ``count_unit`` / ``count_places``.
    count_sums: tuple[float, ...] = ()
    count_unit: str = ""
    count_places: int = 0


#: A word run for the readability floor, matching the craft check's tokeniser
#: closely enough: digits and letters count, punctuation does not.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: The first number in a headline, with optional sign, thousands separators
#: and decimals. ``2,400 km`` and ``3.3 million km²`` both match their value.
_NUMBER = re.compile(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)")

#: Earlier count-up steps are hidden in the same grid cell as the final value.
#: Drop all but the last one when measuring how long the headline needs to be
#: read; the final value is what stays on screen.
_REDUNDANT_COUNT_STEP = re.compile(
    r'<span class="typo-count-step"[^>]*>.*?</span>(?=\s*<span class="typo-count-step")',
    re.DOTALL,
)


def _read_floor_seconds(markup: str) -> float:
    """Seconds a fully visible reveal needs to be read.

    Mirrors the craft check's floor: 0.4 s for a two-word line, otherwise 75%
    of ``0.5 s + 0.3 s a word``. A reveal timed to a spoken word near the end
    of the scene is pulled back so its entry and readable hold still fit
    before the cut, instead of flashing for a second.
    """
    markup = _REDUNDANT_COUNT_STEP.sub("", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    words = len(_WORD.findall(text))
    if words == 0:
        return 0.0
    if words <= 2:
        return 0.4
    return 0.75 * (0.5 + 0.3 * words)


def _unit(width: int) -> float:
    """Scale factor from the 1080-wide design canvas to ``width``."""
    try:
        scale = float(width) / 1080.0
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(scale) or scale <= 0.0:
        return 1.0
    return max(0.5, scale)


def _seconds(duration: float) -> float:
    """A finite, non-negative scene length; degenerate input becomes ``0.0``."""
    try:
        value = float(duration)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0.0 else 0.0


def _require(props: Mapping[str, Any], key: str) -> str:
    value = str(props.get(key) or "").strip()
    if not value:
        raise SceneRenderError(f"layout {props.get('layout')!r} needs a non-empty {key!r}")
    return value


def _entry_seconds(design: DesignTokens) -> float:
    """The tempo-scaled entry motion length, falling back to the base value."""
    try:
        tempo = float(design.motion.tempo)
    except (TypeError, ValueError):
        return ENTRY_SECONDS
    if not math.isfinite(tempo) or tempo <= 0.0:
        return ENTRY_SECONDS
    return ENTRY_SECONDS / tempo


def _count_values(number: str) -> list[str] | None:
    """The formatted values a number climbs through, ending at itself.

    The literal keeps its own thousands separators and decimal places, so
    ``2,400`` ends at ``2,400`` and ``3.3`` never becomes ``3`` or ``3.30``.
    The values follow an ease-out curve but are all pre-formatted strings;
    nothing is computed at render time.
    """
    clean = number.replace(",", "")
    try:
        target = float(clean)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(target):
        return None
    decimals = len(clean.split(".", 1)[1]) if "." in clean else 0
    separator = "," if "," in number else ""
    # Only count a literal the formatter reproduces exactly; "007", "99,9" or
    # Indian grouping "1,00,000" would be rewritten, so they stay static.
    if f"{target:{separator}.{decimals}f}" != number:
        return None
    last = COUNT_STEPS - 1
    values: list[str] = []
    for position in range(COUNT_STEPS):
        progress = position / last if last else 1.0
        eased = 1.0 - (1.0 - progress) ** 3
        value = target * eased
        if value == 0.0:
            value = 0.0
        values.append(f"{value:{separator}.{decimals}f}")
    values[-1] = number
    return values


def _count_markup(headline: str) -> str | None:
    """A stack of formatted step values in one grid cell, or ``None``.

    Only the first number counts. The text around it is kept verbatim and
    sits outside the grid, so it does not move as the digits change.
    """
    match = _NUMBER.search(headline)
    if match is None:
        return None
    values = _count_values(match.group(0))
    if values is None:
        return None
    prefix = html.escape(headline[: match.start()])
    suffix = html.escape(headline[match.end() :])
    steps = "".join(f'<span class="typo-count-step">{value}</span>' for value in values)
    parts: list[str] = []
    if prefix:
        parts.append(f'<span class="typo-count-prefix">{prefix}</span>')
    parts.append(f'<span class="typo-count">{steps}</span>')
    if suffix:
        parts.append(f'<span class="typo-count-suffix">{suffix}</span>')
    return "".join(parts)


def _word_markup(text: str) -> str:
    """Words in spans, with the original whitespace left as real text."""
    parts = re.split(r"(\s+)", text)
    return "".join(
        html.escape(part)
        if part.isspace()
        else f'<span class="typo-word">{html.escape(part)}</span>'
        for part in parts
        if part
    )


def _icon_markup(props_or_bar: Mapping[str, Any], *, where: str) -> str:
    """The inline icon named by ``icon``, or ``""``; a bad name is a scene error."""
    name = str(props_or_bar.get("icon") or "").strip()
    if not name:
        return ""
    try:
        return icon_svg(name)
    except IconError as exc:
        raise SceneRenderError(f"{where}: {exc}") from exc


BIG_STAT_ALIGNS = ("center", "top")

#: Share of the frame height the ``align: top`` text block may occupy; the
#: lower part stays clear for YouTube end-screen cards.
BIG_STAT_TOP_BLOCK = 0.42


def big_stat_align_error(props: Mapping[str, Any]) -> str | None:
    """Why a ``big_stat`` ``align`` value is unusable, else ``None``."""
    align = props.get("align")
    if align is None:
        return None
    if isinstance(align, str) and align.strip().lower() in BIG_STAT_ALIGNS:
        return None
    return f"big_stat `align` must be one of {', '.join(BIG_STAT_ALIGNS)}, got {align!r}"


def _big_stat_top_css(index: int, width: int) -> str:
    """Stage override: headline + sub left-aligned in the top of the frame."""
    u = _unit(width)
    bottom = 100.0 * (1.0 - BIG_STAT_TOP_BLOCK)
    return (
        f"      .typo-{index} .typo-stage {{ bottom: {bottom:.1f}%;\n"
        f"        align-items: flex-start; justify-content: flex-start;\n"
        f"        padding-top: {int(70 * u)}px; gap: {int(18 * u)}px; }}\n"
        f"      .typo-{index} .typo-line {{ text-align: left; }}\n"
        # Fixed sizes: `cqh` would resolve against an ancestor, not this stage,
        # and shrank the end card to a footnote. One 100 px line plus a 56 px sub
        # stay inside the top 42% (150 px wrapped to two lines and ran to 75%).
        f"      .typo-{index} .typo-headline {{ font-size: {int(100 * u)}px; white-space: nowrap; }}\n"
        f"      .typo-{index} .typo-sub {{ max-width: none; font-size: {int(56 * u)}px; }}\n"
        f"      .typo-{index} .typo-rule {{ margin-left: 0; margin-right: auto; }}\n"
    )


def _big_stat(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    headline = _require(props, "headline")
    align_error = big_stat_align_error(props)
    if align_error:
        raise SceneRenderError(align_error)
    top = str(props.get("align") or "center").strip().lower() == "top"
    counted = None if props.get("count") is False else _count_markup(headline)
    if counted is None:
        head = f'<span class="typo-headline">{html.escape(headline)}</span>'
    else:
        head = f'<span class="typo-headline" aria-label="{html.escape(headline)}">{counted}</span>'
    unit = str(props.get("unit") or "").strip()
    sub = str(props.get("sub") or "").strip()
    cue = str(props.get("cue") or "").strip()
    if unit:
        head += f'<span class="typo-unit">{html.escape(unit)}</span>'
    head += '<span class="typo-rule"></span>'
    icon = _icon_markup(props, where="big_stat")
    if icon:
        head = f'<span class="typo-icon">{icon}</span>{head}'
    items = [_Item(head, cue=cue, opening=True)]
    if sub:
        items.append(_Item(html.escape(sub), class_name="typo-line typo-sub"))
    return items, _big_stat_top_css(index, width) if top else ""


def _bar_compare(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    bars = props.get("bars")
    if not isinstance(bars, list) or not 2 <= len(bars) <= 4:
        raise SceneRenderError("bar_compare needs `bars` with 2-4 entries")
    values: list[float] = []
    for bar in bars:
        if not isinstance(bar, Mapping):
            raise SceneRenderError("bar_compare bars must be objects with a `value`")
        try:
            value = float(bar["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SceneRenderError(f"bar_compare bar without a numeric `value`: {exc}") from exc
        if not math.isfinite(value):
            raise SceneRenderError(f"bar_compare bar `value` must be finite, got {value!r}")
        values.append(value)
    largest = max(values)
    if not math.isfinite(largest) or largest <= 0.0:
        largest = 1.0
    items: list[_Item] = []
    for bar, value in zip(bars, values, strict=True):
        label = html.escape(str(bar.get("label") or ""))
        unit = html.escape(str(bar.get("unit") or ""))
        icon = _icon_markup(bar, where="bar_compare bar")
        if icon:
            label = f'<span class="typo-icon typo-icon-bar">{icon}</span>{label}'
        percent = max(4.0, 100.0 * value / largest)
        formatted = f"{value:g}"
        amount = unit.replace("{v}", formatted) if "{v}" in unit else f"{formatted}{unit}"
        items.append(
            _Item(
                f'<div class="typo-bar-label">{label}</div>'
                f'<div class="typo-bar-track"><div class="typo-bar" '
                f'style="width:{percent:.1f}%"></div></div>'
                f'<div class="typo-bar-value">{amount}</div>',
                # A chart label usually reads differently from the sentence
                # that introduces it ("Wiz" vs "Google's biggest deal"), so a
                # bar may carry the spoken phrase explicitly; the cue gate
                # checks whichever phrase this ends up being. ``cue: false``
                # says the label is display copy the voice never says.
                cue=declared_cue(bar.get("cue"), str(bar.get("label") or "")),
                class_name="typo-bar-row",
            )
        )
    return items, ""


def _definition(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    term = _require(props, "term")
    gloss = _require(props, "gloss")
    mark = str(props.get("mark") or "—").strip()
    return (
        [
            _Item(
                f'<span class="typo-mark">{html.escape(mark)}</span>',
                class_name="typo-mark-row",
            ),
            _Item(
                f'{html.escape(term)}<span class="typo-rule"></span>',
                cue="" if props.get("term_cue") is False else term,
                class_name="typo-line typo-term",
            ),
            _Item(
                f'<span class="typo-gloss-text" aria-label="{html.escape(gloss)}">'
                f"{_word_markup(gloss)}</span>",
                class_name="typo-line typo-gloss",
            ),
        ],
        "",
    )


def _sequence(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    steps = props.get("steps")
    if not isinstance(steps, list) or not 3 <= len(steps) <= 5:
        raise SceneRenderError("sequence needs `steps` with 3-5 entries")
    entries: list[tuple[str, str]] = []
    for raw in steps:
        if isinstance(raw, Mapping):
            text = str(raw.get("label") or "").strip()
            # Like a bar, a step may name the spoken phrase separately when its
            # display wording is a paraphrase of the narration, or opt out
            # with ``cue: false`` when the voice never says it.
            cue = declared_cue(raw.get("cue"), text)
        else:
            text = str(raw or "").strip()
            cue = text
        if not text:
            raise SceneRenderError("sequence steps must be non-empty")
        entries.append((text, cue))
    items = [_Item('<div class="typo-connector"></div>', class_name="typo-connector-row")]
    for order, (label, cue) in enumerate(entries, start=1):
        items.append(
            _Item(
                f'<span class="typo-dot"></span>'
                f'<span class="typo-label">{order}. {html.escape(label)}</span>',
                cue=cue,
                class_name="typo-step",
                opening=order == 1,
            )
        )
    return items, ""


def _array(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    try:
        cells = int(props.get("cells", 24))
        cols = int(props.get("cols", 6))
        highlight = [int(value) for value in (props.get("highlight") or [])]
    except (TypeError, ValueError) as exc:
        raise SceneRenderError(f"array props must be integers: {exc}") from exc
    if not 4 <= cells <= 60:
        raise SceneRenderError("array needs `cells` between 4 and 60")
    if not 1 <= cols <= cells:
        raise SceneRenderError("array needs `cols` between 1 and `cells`")
    hot = {value for value in highlight if 0 <= value < cells}
    grid = "".join(
        f'<div class="typo-cell{" is-hot" if cell in hot else ""}"></div>' for cell in range(cells)
    )
    # Stagger the cells inside whatever time the scene leaves after the entry
    # motion. Dividing by ``cells`` (not ``cells - 1``) reserves room for the
    # final cell, so the last delay is strictly inside the scene and its
    # ``both`` entry actually plays instead of holding opacity 0 until the cut.
    latest = max(0.0, duration - entry)
    step = min(0.12, latest / max(cells, 1))
    extra = "\n".join(
        f"      .typo-{index} .typo-cell:nth-child({cell + 1}) "
        f"{{ animation-delay: {min(latest, entry + cell * step):.3f}s; }}"
        for cell in range(cells)
    )
    markup = f'<div class="typo-grid" style="grid-template-columns:repeat({cols},1fr)">{grid}</div>'
    return [_Item(markup, class_name="typo-grid-row")], extra


# --- chart layouts ----------------------------------------------------------
#
# ``waterfall``, ``line`` and ``donut`` draw into one shared chart box. Every
# item is a full-box *layer* (class ``typo-chart``), so each reveal is its own
# cue-timed item while all pieces share one coordinate frame. The box keeps a
# fixed aspect ratio (``--r``) sized from the stage with container units, so
# circles stay round and text never stretches at any video aspect. Pieces are
# positioned in percent of the box and animated with transforms/opacity or
# SVG dash offsets only: seekable CSS, no JavaScript.

#: A step that lowers a waterfall; the palette has no negative colour of its own.
NEGATIVE_COLOR = "#C0392B"

#: Unit symbols written before the number ("₹1,200"); anything else follows it.
_PREFIX_UNITS = frozenset({"₹", "$", "€", "£", "¥", "Rs", "Rs."})

MINUS = "\u2212"


def _number(value: Any, where: str) -> float:
    """A finite number from a YAML number or a string such as ``"1,52,000"``."""
    if isinstance(value, bool):
        raise SceneRenderError(f"{where} must be a number, got {value!r}")
    try:
        number = (
            float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
        )
    except (TypeError, ValueError) as exc:
        raise SceneRenderError(f"{where} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise SceneRenderError(f"{where} must be finite, got {value!r}")
    return number


def _decimals(*values: Any) -> int:
    """Decimal places the authored numbers use (at most 2)."""
    places = 0
    for value in values:
        text = str(value).replace(",", "").strip()
        if "." in text and "e" not in text.lower():
            places = max(places, len(text.split(".", 1)[1].rstrip("0")))
    return min(places, 2)


def indian_number(value: float, decimals: int = 0) -> str:
    """``1234567.5`` -> ``12,34,567.5`` (lakh/crore grouping, no sign)."""
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{whole}.{frac}" if frac else whole


def format_amount(value: float, unit: str = "", *, decimals: int = 0, signed: bool = False) -> str:
    """Indian-grouped amount with the unit before (``₹``) or after (``%``).

    A negative value always carries ``−``; ``signed`` adds ``+`` to a positive.
    """
    body = indian_number(value, decimals)
    if unit in _PREFIX_UNITS:
        body = f"{unit}{body}"
    elif unit:
        body = f"{body}{unit}"
    if round(value, decimals) < 0:
        return f"{MINUS}{body}"
    return f"+{body}" if signed and round(value, decimals) > 0 else body


def _label_entry(raw: Any, where: str, *, need_value: bool = True) -> tuple[str, float | None]:
    if not isinstance(raw, Mapping):
        raise SceneRenderError(f"{where} must be an object with a `label`")
    label = str(raw.get("label") or "").strip()
    if not label:
        raise SceneRenderError(f"{where} needs a non-empty `label`")
    if raw.get("value") is None:
        if need_value:
            raise SceneRenderError(f"{where} needs a numeric `value`")
        return label, None
    return label, _number(raw["value"], f"{where} `value`")


def waterfall_end_error(props: Mapping[str, Any]) -> str | None:
    """Why an explicit ``end.value`` disagrees with start + steps, else ``None``.

    The comparison is to the shown decimals (to the rupee for whole numbers).
    """
    end = props.get("end")
    if not isinstance(end, Mapping) or end.get("value") is None:
        return None
    try:
        start = props["start"]
        steps = props["steps"]
        total = _number(start["value"], "start `value`")
        raw_values = [start["value"], end["value"]]
        for step in steps:
            total += _number(step["value"], "step `value`")
            raw_values.append(step["value"])
        stated = _number(end["value"], "end `value`")
    except (KeyError, TypeError, SceneRenderError):
        return None  # shape problems are the renderer's to report
    places = _decimals(*raw_values)
    if abs(total - stated) >= 0.5 * 10 ** (-places) - 1e-9:
        shown = indian_number(total, places)
        return (
            f"waterfall `end.value` {indian_number(stated, places)} does not equal "
            f"start + steps = {shown}"
        )
    return None


def _chart_class(kind: str) -> str:
    return f"typo-chart typo-chart-{kind}"


def _waterfall(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    start_label, start_value = _label_entry(props.get("start"), "waterfall `start`")
    raw_steps = props.get("steps")
    if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 8:
        raise SceneRenderError("waterfall needs `steps` with 2-8 entries")
    steps: list[tuple[str, float, str]] = []
    for position, raw in enumerate(raw_steps, start=1):
        label, value = _label_entry(raw, f"waterfall step {position}")
        assert value is not None
        steps.append((label, value, declared_cue(raw.get("cue"), label)))
    end_raw = props.get("end")
    end_label, _ = _label_entry(end_raw, "waterfall `end`", need_value=False)
    mismatch = waterfall_end_error(props)
    if mismatch:
        raise SceneRenderError(mismatch)
    assert start_value is not None
    unit = str(props.get("unit") or "").strip()
    places = _decimals(
        start_value if not isinstance(props["start"], Mapping) else props["start"].get("value"),
        *(raw.get("value") for raw in raw_steps),
    )

    # Levels the running total passes through; every bar is a span between two.
    levels = [start_value]
    for _, value, _ in steps:
        levels.append(levels[-1] + value)
    spans: list[tuple[float, float, str]] = [(0.0, start_value, "is-total")]
    for position, (_, value, _) in enumerate(steps):
        spans.append((levels[position], levels[position + 1], "is-down" if value < 0 else "is-up"))
    spans.append((0.0, levels[-1], "is-total"))
    labels = [start_label, *(label for label, _, _ in steps), end_label]
    shown = [start_value, *(value for _, value, _ in steps), levels[-1]]
    cues = [
        declared_cue(props["start"].get("cue"), ""),
        *(cue for _, _, cue in steps),
        declared_cue(end_raw.get("cue"), ""),
    ]

    top, bottom = 14.0, 78.0
    low = min(0.0, *levels)
    high = max(0.0, *levels)
    scale = (bottom - top) / ((high - low) or 1.0)

    def y_of(value: float) -> float:
        return top + (high - value) * scale

    count = len(labels)
    slot = 100.0 / count
    pad = slot * 0.19
    label_size = 0.034 * min(1.0, 6.0 / count)
    items: list[_Item] = []
    for position in range(count):
        a, b, kind = spans[position]
        left = position * slot + pad
        bar_w = slot - 2 * pad
        y_top, y_bottom = y_of(max(a, b)), y_of(min(a, b))
        height = max(y_bottom - y_top, 0.7)
        # A bar grows away from the level it continues from: up from the
        # baseline, or down from the previous level for a negative step.
        grows_from = "0%" if (kind == "is-down" or (kind == "is-total" and b < 0)) else "100%"
        parts: list[str] = []
        if position == 0:
            parts.append(f'<div class="typo-wf-zero" style="top:{y_of(0.0):.2f}%"></div>')
        else:
            prev_right = (position - 1) * slot + slot - pad
            link = a if position <= len(steps) else levels[-1]
            parts.append(
                f'<div class="typo-wf-conn" style="left:{prev_right:.2f}%;'
                f'width:{left - prev_right:.2f}%;top:{y_of(link):.2f}%"></div>'
            )
        parts.append(
            f'<div class="typo-wf-bar {kind}" style="left:{left:.2f}%;width:{bar_w:.2f}%;'
            f'top:{y_top:.2f}%;height:{height:.2f}%;transform-origin:50% {grows_from}"></div>'
        )
        text = format_amount(
            shown[position], unit, decimals=places, signed=kind in ("is-up", "is-down")
        )
        parts.append(
            f'<div class="typo-wf-val" style="left:{position * slot:.2f}%;width:{slot:.2f}%;'
            f'bottom:{100.0 - y_top + 1.4:.2f}%;font-size:calc(var(--cw) * {label_size * 0.78:.4f})">'
            f"{html.escape(text)}</div>"
        )
        parts.append(
            f'<div class="typo-wf-label" style="left:{position * slot:.2f}%;width:{slot:.2f}%;'
            f'top:{bottom + 3.0:.2f}%;font-size:calc(var(--cw) * {label_size:.4f})">'
            f"{html.escape(labels[position])}</div>"
        )
        items.append(_Item("".join(parts), cue=cues[position], class_name=_chart_class("wf")))
    return items, ""


#: The line chart's plot rectangle, in percent of the chart box.
_LN_LEFT, _LN_RIGHT, _LN_TOP, _LN_BOTTOM = 13.0, 95.0, 8.0, 80.0
#: The line chart box is 3:2, so an SVG user unit is a percent of its width.
_LN_ASPECT = 1.5
#: Seconds the line takes to draw on, and a dot to slide between two x values.
LINE_DRAW_SECONDS = 1.2
DOT_SLIDE_SECONDS = 1.1


def _pair(raw: Any, where: str) -> tuple[float, float]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 2:
        raise SceneRenderError(f"{where} must be a [low, high] pair")
    low, high = _number(raw[0], f"{where} low"), _number(raw[1], f"{where} high")
    if not low < high:
        raise SceneRenderError(f"{where} needs low < high, got [{raw[0]}, {raw[1]}]")
    return low, high


def _line_points(props: Mapping[str, Any]) -> list[tuple[float, float]] | None:
    """Explicit ``points`` as validated, x-increasing pairs, or ``None``."""
    raw = props.get("points")
    if raw is None:
        return None
    if not isinstance(raw, list) or not 3 <= len(raw) <= 30:
        raise SceneRenderError("line `points` needs 3-30 [x, y] pairs")
    points = [_pair_xy(item, f"line point {n}") for n, item in enumerate(raw, start=1)]
    if any(b[0] <= a[0] for a, b in zip(points, points[1:], strict=False)):
        raise SceneRenderError("line `points` need strictly increasing x values")
    return points


def _pair_xy(raw: Any, where: str) -> tuple[float, float]:
    if not isinstance(raw, Sequence) or isinstance(raw, str) or len(raw) != 2:
        raise SceneRenderError(f"{where} must be an [x, y] pair")
    return _number(raw[0], f"{where} x"), _number(raw[1], f"{where} y")


def _line_marks(
    props: Mapping[str, Any],
) -> tuple[tuple[float, str, str] | None, list[tuple[float, str, str]]]:
    """``mark`` and ``dots`` as ``(x, label, cue)`` tuples."""
    mark = None
    raw_mark = props.get("mark")
    if raw_mark is not None:
        if not isinstance(raw_mark, Mapping):
            raise SceneRenderError("line `mark` must be an object {x, label, cue?}")
        label = str(raw_mark.get("label") or "").strip()
        if not label:
            raise SceneRenderError("line `mark` needs a non-empty `label`")
        mark = (
            _number(raw_mark.get("x"), "line `mark` x"),
            label,
            declared_cue(raw_mark.get("cue"), label),
        )
    dots: list[tuple[float, str, str]] = []
    raw_dots = props.get("dots")
    if raw_dots is not None:
        if not isinstance(raw_dots, list) or not 1 <= len(raw_dots) <= 3:
            raise SceneRenderError("line `dots` needs 1-3 entries {x, label, cue?}")
        for n, raw in enumerate(raw_dots, start=1):
            if not isinstance(raw, Mapping):
                raise SceneRenderError(f"line dot {n} must be an object {{x, label, cue?}}")
            label = str(raw.get("label") or "").strip()
            if not label:
                raise SceneRenderError(f"line dot {n} needs a non-empty `label`")
            dots.append(
                (
                    _number(raw.get("x"), f"line dot {n} x"),
                    label,
                    declared_cue(raw.get("cue"), label),
                )
            )
    return mark, dots


def _line(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    points = _line_points(props)
    has_formula = props.get("slope") is not None or props.get("intercept") is not None
    if points is not None and has_formula:
        raise SceneRenderError("line takes either `slope`/`intercept` or `points`, not both")
    if points is None and not has_formula:
        raise SceneRenderError("line needs `slope` + `intercept` or `points`")
    mark, dots = _line_marks(props)
    unit = str(props.get("unit") or "").strip()
    x_label = str(props.get("x_label") or "").strip()
    y_label = str(props.get("y_label") or "").strip()
    marked_x = [x for x, _, _ in ([mark] if mark else [])] + [x for x, _, _ in dots]

    if points is None:
        slope = _number(props.get("slope", 0), "line `slope`")
        intercept = _number(props.get("intercept", 0), "line `intercept`")
        if props.get("x_range") is not None:
            x0, x1 = _pair(props["x_range"], "line `x_range`")
        elif marked_x:
            x0, x1 = 0.0, max(marked_x) * 1.3 or 1.0
        else:
            raise SceneRenderError("a line from `slope` needs an `x_range` (or a mark/dots)")
        points = [(x0, slope * x0 + intercept), (x1, slope * x1 + intercept)]
        authored = [slope, intercept]
    else:
        if props.get("x_range") is not None:
            x0, x1 = _pair(props["x_range"], "line `x_range`")
        else:
            x0, x1 = points[0][0], points[-1][0]
        authored = [value for pair in props["points"] for value in pair[1:]]
    places = _decimals(*authored, *(props.get("y_range") or []))

    ys = [y for _, y in points]
    if props.get("y_range") is not None:
        y0, y1 = _pair(props["y_range"], "line `y_range`")
    else:
        low, high = min(0.0, min(ys)), max(0.0, max(ys))
        pad = ((high - low) or 1.0) * 0.08
        y0, y1 = (low - pad if low < 0 else low), high + pad
    for x in marked_x:
        if not x0 <= x <= x1:
            raise SceneRenderError(f"line mark/dot x={x:g} lies outside x_range [{x0:g}, {x1:g}]")

    def px(x: float) -> float:
        return _LN_LEFT + (x - x0) / (x1 - x0) * (_LN_RIGHT - _LN_LEFT)

    def py(y: float) -> float:
        return _LN_BOTTOM - (y - y0) / (y1 - y0) * (_LN_BOTTOM - _LN_TOP)

    def y_at(x: float) -> float:
        for (xa, ya), (xb, yb) in zip(points, points[1:], strict=False):
            if xa <= x <= xb:
                return ya + (yb - ya) * (x - xa) / (xb - xa)
        # Beyond the drawn polyline (an explicit x_range wider than the data):
        # hold the nearest end rather than inventing a value.
        return points[0][1] if x < points[0][0] else points[-1][1]

    hs = 100.0 / _LN_ASPECT  # SVG height in user units (width is 100)

    def sv(percent_y: float) -> float:
        return percent_y * hs / 100.0

    label_size = 0.03
    tick = f"font-size:calc(var(--cw) * {label_size * 0.85:.4f})"
    clip = f"ln-clip-{index}"
    axes = [
        f'<svg class="typo-ln-svg" viewBox="0 0 100 {hs:.4f}" aria-hidden="true">',
        f'<line class="typo-ln-axis" x1="{_LN_LEFT}" y1="{sv(_LN_TOP):.3f}" '
        f'x2="{_LN_LEFT}" y2="{sv(_LN_BOTTOM):.3f}"/>',
        f'<line class="typo-ln-axis" x1="{_LN_LEFT}" y1="{sv(_LN_BOTTOM):.3f}" '
        f'x2="{_LN_RIGHT}" y2="{sv(_LN_BOTTOM):.3f}"/>',
    ]
    if y0 < 0 < y1:
        axes.append(
            f'<line class="typo-ln-zero" x1="{_LN_LEFT}" y1="{sv(py(0.0)):.3f}" '
            f'x2="{_LN_RIGHT}" y2="{sv(py(0.0)):.3f}"/>'
        )
    axes.append("</svg>")
    ticks = [
        (_LN_LEFT, _LN_BOTTOM + 3.0, "x", format_amount(x0, "", decimals=0)),
        (_LN_RIGHT, _LN_BOTTOM + 3.0, "x", format_amount(x1, "", decimals=0)),
        (_LN_LEFT, _LN_TOP, "y", format_amount(y1, unit, decimals=places)),
        (_LN_LEFT, _LN_BOTTOM, "y", format_amount(y0, unit, decimals=places)),
    ]
    if y0 < 0 < y1:
        ticks.append((_LN_LEFT, py(0.0), "y", format_amount(0.0, unit)))
    for left, top, axis, text in ticks:
        if axis == "x":
            axes.append(
                f'<div class="typo-ln-tick is-x" style="left:{left:.2f}%;top:{top:.2f}%;{tick}">'
                f"{html.escape(text)}</div>"
            )
        else:
            axes.append(
                f'<div class="typo-ln-tick is-y" style="right:{100 - left + 1.2:.2f}%;'
                f'top:{top:.2f}%;{tick}">{html.escape(text)}</div>'
            )
    if x_label:
        axes.append(
            f'<div class="typo-ln-axis-label is-x" style="left:{_LN_LEFT}%;width:'
            f"{_LN_RIGHT - _LN_LEFT}%;top:{_LN_BOTTOM + 9.0}%;"
            f'font-size:calc(var(--cw) * {label_size:.4f})">{html.escape(x_label)}</div>'
        )
    if y_label:
        axes.append(
            f'<div class="typo-ln-axis-label is-y" style="left:0;top:0;'
            f'font-size:calc(var(--cw) * {label_size:.4f})">{html.escape(y_label)}</div>'
        )

    path = " ".join(
        f"{'M' if n == 0 else 'L'}{px(x):.3f} {sv(py(y)):.3f}" for n, (x, y) in enumerate(points)
    )
    plot = (
        f'<svg class="typo-ln-svg" viewBox="0 0 100 {hs:.4f}" aria-hidden="true">'
        f'<clipPath id="{clip}"><rect x="{_LN_LEFT}" y="{sv(_LN_TOP):.3f}" '
        f'width="{_LN_RIGHT - _LN_LEFT}" height="{sv(_LN_BOTTOM - _LN_TOP):.3f}"/></clipPath>'
        f'<path class="typo-ln-path" d="{path}" pathLength="1" clip-path="url(#{clip})"/></svg>'
    )
    items = [
        _Item("".join(axes), class_name=_chart_class("ln")),
        _Item(plot, class_name=_chart_class("ln")),
    ]
    extra: list[str] = []

    if mark:
        mx, mlabel, mcue = mark
        on_left = (mx - x0) / (x1 - x0) > 0.6
        side = (
            f"right:{100 - px(mx) + 1.2:.2f}%;text-align:right"
            if on_left
            else f"left:{px(mx) + 1.2:.2f}%"
        )
        items.append(
            _Item(
                f'<div class="typo-ln-mark" style="left:{px(mx):.2f}%;top:{_LN_TOP:.2f}%;'
                f'height:{_LN_BOTTOM - _LN_TOP:.2f}%"></div>'
                f'<div class="typo-ln-mark-label" style="{side};top:{_LN_TOP:.2f}%;'
                f'font-size:calc(var(--cw) * {label_size:.4f})">{html.escape(mlabel)}'
                f'<span class="typo-ln-sub">{html.escape(format_amount(mx, ""))}</span></div>',
                cue=mcue,
                class_name=_chart_class("ln"),
            )
        )

    previous_x: float | None = None
    for n, (dx, dlabel, dcue) in enumerate(dots):
        last = n == len(dots) - 1
        value = format_amount(y_at(dx), unit, decimals=places)
        flip = (dx - x0) / (x1 - x0) > 0.6
        text = (
            f'<span class="typo-ln-dot-label{" is-flip" if flip else ""}" '
            f'style="font-size:calc(var(--cw) * {label_size:.4f})">{html.escape(dlabel)}'
            f'<span class="typo-ln-sub">{html.escape(value)}</span></span>'
        )
        name = f"tp_ln_dot_{index}_{n}"
        start_x = dx if previous_x is None else previous_x
        if start_x != dx:
            lo, hi = sorted((start_x, dx))
            xs = [start_x]
            xs += [vx for vx, _ in (points if dx > start_x else reversed(points)) if lo < vx < hi]
            xs.append(dx)
            stops = "".join(
                f"{100.0 * (vx - start_x) / (dx - start_x):.3f}% "
                f"{{ left: {px(vx):.3f}%; top: {py(y_at(vx)):.3f}%; }} "
                for vx in xs
            )
            extra.append(f"      @keyframes {name} {{ {stops}}}\n")
            move = (
                f"animation: {name} {DOT_SLIDE_SECONDS:.2f}s linear var(--d, 0s) both; "
                f"left: {px(start_x):.3f}%; top: {py(y_at(start_x)):.3f}%;"
            )
        else:
            move = f"left: {px(dx):.3f}%; top: {py(y_at(dx)):.3f}%;"
        wrap = "typo-ln-dots" + ("" if last else " is-passed")
        items.append(
            _Item(
                f'<div class="{wrap}"><div class="typo-ln-dot" style="{move}">{text}</div></div>',
                cue=dcue,
                class_name=_chart_class("ln"),
            )
        )
        previous_x = dx
    return items, "".join(extra)


#: Seconds one donut segment takes to sweep in.
DONUT_SWEEP_SECONDS = 0.9
_DN_RADIUS = 40.0

#: Segment colours, in order: the palette's three inks, then a tint of each, so
#: six slices stay distinct on any design (``{}`` is filled with the palette).
_DONUT_FILLS = (
    "{accent}",
    "{ink}",
    "{muted}",
    "color-mix(in srgb, {accent} 50%, {bg})",
    "color-mix(in srgb, {ink} 50%, {bg})",
    "color-mix(in srgb, {muted} 50%, {bg})",
)


def _donut_parts(props: Mapping[str, Any]) -> list[tuple[str, float, str]]:
    raw = props.get("parts")
    if not isinstance(raw, list) or not 2 <= len(raw) <= 6:
        raise SceneRenderError("donut needs `parts` with 2-6 entries")
    parts: list[tuple[str, float, str]] = []
    for position, item in enumerate(raw, start=1):
        label, value = _label_entry(item, f"donut part {position}")
        assert value is not None
        if value <= 0:
            raise SceneRenderError(f"donut part {position} `value` must be positive, got {value:g}")
        parts.append((label, value, declared_cue(item.get("cue"), label)))
    return parts


def donut_center_error(props: Mapping[str, Any]) -> str | None:
    """Why an explicit ``center.value`` is not the sum of ``parts``, else ``None``.

    Compared to the decimals the authored numbers show (whole numbers exactly).
    """
    center = props.get("center")
    if not isinstance(center, Mapping) or center.get("value") is None:
        return None
    try:
        raw_values = [part["value"] for part in props["parts"]]
        total = sum(_number(value, "part `value`") for value in raw_values)
        stated = _number(center["value"], "center `value`")
    except (KeyError, TypeError, SceneRenderError):
        return None  # shape problems are the renderer's to report
    places = _decimals(*raw_values, center["value"])
    if abs(total - stated) >= 0.5 * 10 ** (-places) - 1e-9:
        return (
            f"donut `center.value` {indian_number(stated, places)} does not equal "
            f"the sum of parts = {indian_number(total, places)}"
        )
    return None


#: Seconds the donut centre takes to climb to each new running sum.
DONUT_COUNT_SECONDS = 0.4
#: Sub-steps per climb (the previous sum eases to the new one in this many).
DONUT_COUNT_SUBSTEPS = 5


def _donut_sum_steps(sums: Sequence[float], unit: str, places: int, texts: Sequence[str]) -> str:
    """Hidden-step spans for ``texts`` with the shared affixes pulled out."""
    final = format_amount(sums[-1], unit, decimals=places)
    match = _NUMBER.search(final)
    prefix = final[: match.start()] if match else ""
    suffix = final[match.end() :] if match else ""
    steps: list[str] = []
    for text in texts:
        core = text
        if prefix and core.startswith(prefix):
            core = core[len(prefix) :]
        if suffix and core.endswith(suffix):
            core = core[: len(core) - len(suffix)]
        steps.append(f'<span class="typo-count-step">{html.escape(core)}</span>')
    out = ""
    if prefix:
        out += f'<span class="typo-count-prefix">{html.escape(prefix)}</span>'
    out += f'<span class="typo-count typo-dn-sum">{"".join(steps)}</span>'
    if suffix:
        out += f'<span class="typo-count-suffix">{html.escape(suffix)}</span>'
    return out


def _donut_sum_markup(sums: Sequence[float], unit: str, places: int) -> str:
    """The centre value, one hidden step per running sum (before motion)."""
    texts = [format_amount(value, unit, decimals=places) for value in sums]
    return _donut_sum_steps(sums, unit, places, texts)


def _donut_count_events(
    sums: Sequence[float], part_times: Sequence[float]
) -> list[tuple[float, float]]:
    """``(time, value)`` events: each part's reveal eases to its running sum."""
    events: list[tuple[float, float]] = [(0.0, sums[0])]
    count = min(len(part_times), len(sums) - 1)
    for position in range(count):
        begin = part_times[position]
        limit = part_times[position + 1] if position + 1 < count else math.inf
        previous, target = sums[position], sums[position + 1]
        for sub in range(1, DONUT_COUNT_SUBSTEPS + 1):
            moment = begin + DONUT_COUNT_SECONDS * sub / DONUT_COUNT_SUBSTEPS
            if moment >= limit:
                break
            progress = sub / DONUT_COUNT_SUBSTEPS
            eased = 1.0 - (1.0 - progress) ** 3
            events.append(
                (
                    moment,
                    target
                    if sub == DONUT_COUNT_SUBSTEPS
                    else previous + (target - previous) * eased,
                )
            )
    # The final value always lands, even if a later reveal cut the climb short.
    if count and events[-1][1] != sums[count]:
        events.append((max(events[-1][0] + 0.01, part_times[count - 1]), sums[count]))
    ordered: list[tuple[float, float]] = []
    for moment, value in events:
        if ordered and moment <= ordered[-1][0] + 1e-6:
            ordered[-1] = (ordered[-1][0], value)
        else:
            ordered.append((moment, value))
    return ordered


def _donut_count_motion(
    item: _Item, item_html: str, *, index: int, part_times: Sequence[float]
) -> tuple[str, str]:
    """Rebuild the centre's steps so it follows the slice reveal times.

    Each step span is shown from its event time to the next one (the last stays
    on); the spans sit in one grid cell, so nothing shifts. Opacity runs on the
    step spans only and each carries its own absolute delay.
    """
    events = _donut_count_events(item.count_sums, part_times)
    texts = [
        format_amount(value, item.count_unit, decimals=item.count_places) for _, value in events
    ]
    rebuilt = _donut_sum_steps(item.count_sums, item.count_unit, item.count_places, texts)
    match = re.search(
        r'(<span class="typo-count-prefix">.*?</span>)?<span class="typo-count typo-dn-sum">.*?</span></span>(<span class="typo-count-suffix">.*?</span>)?',
        item_html,
        re.DOTALL,
    )
    if match is None:
        return item_html, ""
    item_html = item_html[: match.start()] + rebuilt + item_html[match.end() :]
    position = -1
    last = len(events) - 1

    def style(_: re.Match[str]) -> str:
        nonlocal position
        position += 1
        moment = events[position][0]
        if position == last:
            animation = f"tp_dnlast_{index} 0.100s linear {moment:.3f}s forwards"
        else:
            gap = max(events[position + 1][0] - moment, 0.001)
            animation = f"tp_dnhold_{index} {gap:.3f}s linear {moment:.3f}s forwards"
        shown = " opacity: 1;" if position == 0 else ""
        return f'<span class="typo-count-step" style="animation: {animation};{shown}">'

    item_html = re.sub(r'<span class="typo-count-step">', style, item_html)
    css = (
        f"      .typo-{index} .typo-count {{ display: inline-grid; justify-items: end;\n"
        "        font-variant-numeric: tabular-nums; }\n"
        f"      .typo-{index} .typo-count-step {{ grid-area: 1 / 1; opacity: 0; }}\n"
        f"      @keyframes tp_dnhold_{index} {{ 0%, 99% {{ opacity: 1; }} 100% {{ opacity: 0; }} }}\n"
        f"      @keyframes tp_dnlast_{index} {{ from, to {{ opacity: 1; }} }}\n"
    )
    return item_html, css


def _donut(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    parts = _donut_parts(props)
    mismatch = donut_center_error(props)
    if mismatch:
        raise SceneRenderError(mismatch)
    unit = str(props.get("unit") or "").strip()
    places = _decimals(*(part["value"] for part in props["parts"]))
    total = sum(value for _, value, _ in parts)
    center = props.get("center")
    center_cue = ""
    center_markup = ""
    center_sums: tuple[float, ...] = ()
    if center is not None:
        if not isinstance(center, Mapping):
            raise SceneRenderError("donut `center` must be an object {value, label}")
        c_label = str(center.get("label") or "").strip()
        value = (
            _number(center["value"], "donut `center` value")
            if center.get("value") is not None
            else None
        )
        if value is None and not c_label:
            raise SceneRenderError("donut `center` needs a `value` or a `label`")
        shown = total if value is None and not c_label else value
        center_cue = declared_cue(center.get("cue"), "")
        if shown is not None:
            final_text = format_amount(shown, unit, decimals=places)
            running = 0.0
            sums = [0.0]
            for _, part_value, _ in parts:
                running += part_value
                sums.append(running)
            sums[-1] = shown  # ends on the declared (or total) centre value
            center_sums = tuple(sums)
            text = _donut_sum_markup(center_sums, unit, places)
            size = min(0.1, 0.7 / max(len(final_text), 1))
            center_markup += (
                f'<span class="typo-dn-value" style="font-size:calc(var(--cw) * {size:.4f})">'
                f"{text}</span>"
            )
        if c_label:
            center_markup += f'<span class="typo-dn-caption">{html.escape(c_label)}</span>'

    circumference_gap = 0.006
    track = (
        f'<svg class="typo-dn-ring" viewBox="0 0 100 100" aria-hidden="true">'
        f'<circle class="typo-dn-track" cx="50" cy="50" r="{_DN_RADIUS}"/></svg>'
    )
    frame = track + (f'<div class="typo-dn-center">{center_markup}</div>' if center_markup else "")
    items = [
        _Item(
            frame,
            cue=center_cue,
            class_name=_chart_class("dn"),
            opening=True,
            count_sums=center_sums,
            count_unit=unit,
            count_places=places,
        )
    ]
    rows = len(parts)
    row_h = min(6.2, 36.0 / rows)
    legend_top = 63.0
    start = 0.0
    for position, (label, value, cue) in enumerate(parts):
        fraction = value / total
        dash = max(fraction - circumference_gap, 0.0005)
        ring = (
            f'<svg class="typo-dn-ring" viewBox="0 0 100 100" aria-hidden="true">'
            f'<circle class="typo-dn-seg" cx="50" cy="50" r="{_DN_RADIUS}" pathLength="1" '
            f'transform="rotate(-90 50 50)" '
            f'style="stroke:var(--dn-{position}); stroke-dasharray:{dash:.5f} 1; '
            f'stroke-dashoffset:{-start:.5f}"/></svg>'
        )
        legend = (
            f'<div class="typo-dn-row" style="top:{legend_top + position * row_h:.2f}%;'
            f'height:{row_h:.2f}%">'
            f'<span class="typo-dn-swatch" style="background:var(--dn-{position})"></span>'
            f'<span class="typo-dn-name">{html.escape(label)}</span>'
            f'<span class="typo-dn-amount">'
            f"{html.escape(format_amount(value, unit, decimals=places))}</span></div>"
        )
        items.append(
            _Item(
                ring + legend,
                cue=cue,
                class_name=_chart_class("dn"),
                opening=position == 0,
            )
        )
        start += fraction
    return items, ""


_Layout = Callable[
    [Mapping[str, Any], Mapping[str, Any], int, float, int, float],
    tuple[list[_Item], str],
]

_LAYOUTS: dict[str, _Layout] = {
    "big_stat": _big_stat,
    "bar_compare": _bar_compare,
    "definition": _definition,
    "sequence": _sequence,
    "array": _array,
    "waterfall": _waterfall,
    "line": _line,
    "donut": _donut,
}


def _count_keyframes(index: int, steps: int) -> str:
    """One opacity window per step, so the stack shows one value at a time."""
    chunks: list[str] = []
    for position in range(steps):
        begin = 100.0 * position / steps
        end = 100.0 * (position + 1) / steps
        chunks.append(
            f"      @keyframes tp_count_{index}_{position} {{\n"
            f"        0%, {begin:.4f}% {{ opacity: 0; }}\n"
            f"        {min(100.0, begin + 0.001):.4f}%, {end:.4f}% {{ opacity: 1; }}\n"
        )
        if position == steps - 1:
            chunks.append("        100% { opacity: 1; }\n")
        else:
            chunks.append(f"        {min(100.0, end + 0.001):.4f}%, 100% {{ opacity: 0; }}\n")
        chunks.append("      }\n")
    return "".join(chunks)


def _count_motion(markup: str, *, index: int, start: float) -> tuple[str, str]:
    """Give every count step its delay and opacity keyframes.

    All steps share the headline's delay; each step's own keyframes decide
    which slice of the count it owns, and the last step ends fully visible.
    """
    steps = markup.count('class="typo-count-step"')
    if steps == 0:
        return markup, ""
    position = -1

    def style(match: re.Match[str]) -> str:
        nonlocal position
        position += 1
        animation = (
            f"animation-name: tp_count_{index}_{position}; "
            f"animation-duration: {COUNT_SECONDS:.3f}s; "
            "animation-timing-function: linear; "
            "animation-fill-mode: both; "
            f"animation-delay: {start:.3f}s"
        )
        return f'<span class="typo-count-step" style="{animation}">'

    markup = re.sub(r'<span class="typo-count-step">', style, markup)
    css = (
        f"      .typo-{index} .typo-count {{ display: inline-grid; justify-items: end;\n"
        "        font-variant-numeric: tabular-nums; }\n"
        f"      .typo-{index} .typo-count-step {{ grid-area: 1 / 1; opacity: 0;\n"
        "        animation-fill-mode: both; }\n"
        f"{_count_keyframes(index, steps)}"
    )
    return markup, css


def _word_motion(markup: str, *, index: int, start: float, entry: float, seconds: float) -> str:
    """Stagger the gloss words across the reveal window after its item."""
    words = markup.count('class="typo-word"')
    if words == 0:
        return markup
    step = WORD_SPREAD_SECONDS / (words - 1) if words > 1 else 0.0
    duration = min(0.45, max(0.18, entry))
    latest = max(start, seconds - duration) if seconds > 0.0 else start + (words - 1) * step
    position = -1

    def style(match: re.Match[str]) -> str:
        nonlocal position
        position += 1
        delay = min(latest, start + position * step)
        animation = (
            f"animation-name: tp_word_{index}; "
            f"animation-duration: {duration:.3f}s; "
            "animation-timing-function: ease-out; "
            "animation-fill-mode: both; "
            f"animation-delay: {delay:.3f}s"
        )
        return f'<span class="typo-word" style="{animation}">'

    return re.sub(r'<span class="typo-word">', style, markup)


def _base_css(index: int, design: DesignTokens, duration: float, width: int, entry: float) -> str:
    """The per-scene stylesheet; every selector is scoped to ``.typo-{index}``."""
    u = _unit(width)
    palette = design.palette
    body = font_stack(design.type.body)
    display = font_stack(design.type.display)
    return f"""
      @keyframes tp_entry_{index} {{
        from {{ opacity: 0; transform: translate3d(0, {int(22 * u)}px, 0); }}
        to {{ opacity: 1; transform: translate3d(0, 0, 0); }}
      }}
      @keyframes tp_drift_{index} {{
        from {{ transform: scale(1); }}
        to {{ transform: scale({design.motion.drift}); }}
      }}
      @keyframes tp_grow_{index} {{ from {{ width: 0; }} }}
      @keyframes tp_word_{index} {{
        from {{ opacity: 0; transform: translate3d(0, {int(12 * u)}px, 0); }}
        to {{ opacity: 1; transform: translate3d(0, 0, 0); }}
      }}
      @keyframes tp_icon_{index} {{
        from {{ opacity: 0; transform: scale(0.6); }}
        to {{ opacity: 1; transform: scale(1); }}
      }}
      @keyframes tp_rule_{index} {{ from {{ width: 0; }} to {{ width: 100%; }} }}
      .typo-{index} {{ position: absolute; inset: 0; background: {palette.bg}; overflow: hidden; }}
      .typo-{index} .typo-stage {{ position: absolute; inset: 0; display: flex;
        flex-direction: column; align-items: center; justify-content: center;
        gap: {int(26 * u)}px; padding: 0 {int(90 * u)}px; box-sizing: border-box;
        animation: tp_drift_{index} {duration:.3f}s linear both; }}
      .typo-{index} .typo-line {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both;
        font-family: {body}; color: {palette.ink}; text-align: center;
        font-size: {int(46 * u)}px; line-height: 1.25; }}
      .typo-{index} .typo-headline {{ font-family: {display};
        font-weight: {design.type.display_weight}; font-size: {int(168 * u)}px;
        line-height: 0.95; letter-spacing: -0.02em; color: {palette.ink}; }}
      .typo-{index} .typo-icon {{ display: block; width: {int(140 * min(u, 1.4))}px;
        height: {int(140 * min(u, 1.4))}px; margin: 0 auto {int(18 * u)}px;
        color: {palette.accent};
        animation: tp_icon_{index} {entry * 1.6:.3f}s ease-out var(--d, 0s) both; }}
      .typo-{index} .typo-icon-svg {{ display: block; width: 100%; height: 100%; }}
      .typo-{index} .typo-icon-bar {{ display: inline-block; width: {int(40 * u)}px;
        height: {int(40 * u)}px; margin: 0 {int(10 * u)}px 0 0; vertical-align: middle;
        animation-name: tp_icon_{index}; }}
      .typo-{index} .typo-rule {{ display: block; height: {max(2, int(3 * u))}px;
        margin: {int(14 * u)}px auto 0; background: {palette.accent};
        animation: tp_rule_{index} {duration:.3f}s linear both; }}
      .typo-{index} .typo-unit {{ font-family: {display}; font-weight: 500;
        font-size: {int(48 * u)}px; color: {palette.accent}; margin-left: {int(10 * u)}px; }}
      .typo-{index} .typo-sub {{ font-size: {int(44 * u)}px; color: {palette.muted};
        max-width: 26ch; }}
      .typo-{index} .typo-mark-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; }}
      .typo-{index} .typo-mark {{ display: block; font-family: {display};
        font-weight: 600; font-size: {int(28 * u)}px; letter-spacing: 0.18em;
        text-transform: uppercase; color: {palette.accent}; }}
      .typo-{index} .typo-term {{ font-family: {display};
        font-weight: {design.type.display_weight}; font-size: {int(96 * u)}px;
        color: {palette.ink}; }}
      .typo-{index} .typo-gloss {{ max-width: 24ch; color: {palette.muted}; }}
      .typo-{index} .typo-word {{ display: inline-block; opacity: 0;
        animation-fill-mode: both; }}
      .typo-{index} .typo-bar-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%;
        display: grid; grid-template-columns: {int(180 * u)}px 1fr {int(150 * u)}px;
        align-items: center; gap: {int(18 * u)}px; font-family: {body};
        font-size: {int(38 * u)}px; color: {palette.ink}; }}
      .typo-{index} .typo-bar-label {{ text-align: right; font-weight: 600; }}
      .typo-{index} .typo-bar-track {{ height: {int(44 * u)}px; background: {palette.bg};
        border: {max(1, int(2 * u))}px solid {palette.muted};
        border-radius: {int(4 * u)}px; overflow: hidden; }}
      .typo-{index} .typo-bar {{ height: 100%; background: {palette.accent};
        animation: tp_grow_{index} {entry * 1.6:.3f}s ease-out both; }}
      .typo-{index} .typo-bar-value {{ font-family: {display};
        font-weight: {design.type.display_weight}; font-size: {int(44 * u)}px;
        white-space: nowrap; }}
      .typo-{index} .typo-connector-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; flex: none; }}
      .typo-{index} .typo-connector {{ width: {max(2, int(4 * u))}px;
        height: {int(180 * u)}px; background: {palette.muted}; }}
      .typo-{index} .typo-step {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%;
        display: flex; align-items: center; gap: {int(26 * u)}px;
        font-family: {body}; font-size: {int(42 * u)}px; color: {palette.ink}; }}
      .typo-{index} .typo-label {{ text-align: left; }}
      .typo-{index} .typo-dot {{ width: {int(26 * u)}px; height: {int(26 * u)}px;
        border-radius: 50%; background: {palette.accent}; flex: none; }}
      .typo-{index} .typo-grid-row {{ opacity: 0;
        animation: tp_entry_{index} {entry:.3f}s ease-out both; width: 100%; }}
      .typo-{index} .typo-grid {{ display: grid; gap: {int(14 * u)}px; width: 100%; }}
      .typo-{index} .typo-cell {{ aspect-ratio: 1 / 1; background: {palette.bg};
        border: {max(1, int(2 * u))}px solid {palette.muted}; }}
      .typo-{index} .typo-cell.is-hot {{ background: {palette.accent};
        border-color: {palette.accent};
        animation: tp_entry_{index} {entry:.3f}s ease-out both; }}
    """


def _chart_css(index: int, design: DesignTokens, width: int, entry: float) -> str:
    """Shared box, typography and keyframes for the chart layouts."""
    palette = design.palette
    body = font_stack(design.type.body)
    display = font_stack(design.type.display)
    u = _unit(width)
    dn_vars = " ".join(
        f"--dn-{position}: {fill.format(accent=palette.accent, ink=palette.ink, muted=palette.muted, bg=palette.bg)};"
        for position, fill in enumerate(_DONUT_FILLS)
    )
    return f"""
      @keyframes tp_wfgrow_{index} {{ from {{ transform: scaleY(0); }} }}
      .typo-{index} .typo-stage {{ container-type: size; }}
      .typo-{index} .typo-chart {{ position: absolute; inset: 0; margin: auto; opacity: 0;
        --r: 1.5; --cw: min(92cqw, calc(76cqh * var(--r)));
        width: var(--cw); height: calc(var(--cw) / var(--r));
        animation: tp_entry_{index} {entry:.3f}s ease-out both;
        font-family: {body}; color: {palette.ink}; }}
      .typo-{index} .typo-chart > * {{ position: absolute; box-sizing: border-box; }}
      .typo-{index} .typo-wf-zero {{ left: 0; width: 100%; height: {max(1, int(2 * u))}px;
        background: {palette.muted}; opacity: 0.6; }}
      .typo-{index} .typo-wf-conn {{ height: {max(1, int(2 * u))}px;
        background: {palette.muted}; }}
      .typo-{index} .typo-wf-bar {{ border-radius: {int(3 * u)}px;
        animation: tp_wfgrow_{index} {entry * 2.2:.3f}s ease-out var(--d, 0s) both; }}
      .typo-{index} .typo-wf-bar.is-total {{ background: {palette.accent}; }}
      .typo-{index} .typo-wf-bar.is-up {{ background: {palette.muted}; }}
      .typo-{index} .typo-wf-bar.is-down {{ background: {NEGATIVE_COLOR}; }}
      .typo-{index} .typo-wf-val {{ text-align: center; font-family: {display};
        font-weight: {design.type.display_weight}; line-height: 1.1; white-space: nowrap; }}
      .typo-{index} .typo-wf-label {{ text-align: center; line-height: 1.2;
        overflow-wrap: anywhere; color: {palette.ink}; }}
      @keyframes tp_dnsweep_{index} {{ from {{ stroke-dasharray: 0 1; }} }}
      .typo-{index} .typo-chart-dn {{ --r: 0.8; --cw: min(92cqw, 76cqh);
        {dn_vars} }}
      .typo-{index} .typo-dn-ring {{ left: 15%; top: 0; width: 70%; height: calc(var(--cw) * 0.7); }}
      .typo-{index} .typo-dn-track {{ fill: none; stroke: {palette.muted}; opacity: 0.22;
        stroke-width: 14; }}
      .typo-{index} .typo-dn-seg {{ fill: none; stroke-width: 14; stroke-linecap: butt;
        animation: tp_dnsweep_{index} {DONUT_SWEEP_SECONDS:.2f}s ease-out var(--d, 0s) both; }}
      .typo-{index} .typo-dn-center {{ left: 15%; top: 0; width: 70%; height: calc(var(--cw) * 0.7);
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        text-align: center; }}
      .typo-{index} .typo-dn-value {{ font-family: {display}; font-weight: {design.type.display_weight};
        line-height: 1; color: {palette.ink}; white-space: nowrap; }}
      .typo-{index} .typo-dn-caption {{ font-size: calc(var(--cw) * 0.036); margin-top: 0.4em;
        color: {palette.muted}; max-width: 90%; line-height: 1.2; overflow-wrap: anywhere; }}
      .typo-{index} .typo-dn-row {{ left: 8%; width: 84%; display: flex; align-items: center;
        gap: calc(var(--cw) * 0.025); font-size: calc(var(--cw) * 0.04); color: {palette.ink}; }}
      .typo-{index} .typo-dn-swatch {{ flex: none; width: 0.7em; height: 0.7em;
        border-radius: 0.14em; }}
      .typo-{index} .typo-dn-name {{ flex: 1; min-width: 0; line-height: 1.15;
        overflow-wrap: anywhere; }}
      .typo-{index} .typo-dn-amount {{ font-family: {display}; font-weight: {design.type.display_weight};
        white-space: nowrap; }}
      @keyframes tp_lndraw_{index} {{ from {{ stroke-dashoffset: 1; }} to {{ stroke-dashoffset: 0; }} }}
      @keyframes tp_ln_hide_{index} {{ from {{ opacity: 1; }} to {{ opacity: 0; }} }}
      .typo-{index} .typo-ln-svg {{ position: absolute; inset: 0; width: 100%; height: 100%;
        overflow: hidden; }}
      .typo-{index} .typo-ln-axis {{ stroke: {palette.muted}; stroke-width: 0.25; }}
      .typo-{index} .typo-ln-zero {{ stroke: {palette.ink}; stroke-width: 0.35;
        stroke-dasharray: 1.2 0.8; }}
      .typo-{index} .typo-ln-path {{ fill: none; stroke: {palette.accent}; stroke-width: 1.1;
        stroke-linejoin: round; stroke-dasharray: 1; stroke-dashoffset: 1;
        animation: tp_lndraw_{index} {LINE_DRAW_SECONDS:.2f}s ease-in-out var(--d, 0s) both; }}
      .typo-{index} .typo-ln-tick {{ color: {palette.muted}; white-space: nowrap;
        line-height: 1; font-variant-numeric: tabular-nums; }}
      .typo-{index} .typo-ln-tick.is-x {{ transform: translateX(-50%); }}
      .typo-{index} .typo-ln-tick.is-y {{ transform: translateY(-50%); }}
      .typo-{index} .typo-ln-axis-label {{ color: {palette.ink}; text-align: center;
        line-height: 1.2; font-weight: 600; }}
      .typo-{index} .typo-ln-axis-label.is-y {{ text-align: left; white-space: nowrap; }}
      .typo-{index} .typo-ln-mark {{ border-left: {max(2, int(3 * u))}px dashed {palette.ink};
        width: 0; }}
      .typo-{index} .typo-ln-mark-label {{ font-family: {display};
        font-weight: {design.type.display_weight}; line-height: 1.15; white-space: nowrap; }}
      .typo-{index} .typo-ln-sub {{ display: block; font-family: {body}; font-weight: 500;
        color: {palette.accent}; font-size: 0.85em; }}
      .typo-{index} .typo-ln-dots {{ inset: 0; }}
      .typo-{index} .typo-ln-dot {{ position: absolute; box-sizing: border-box; width: {int(30 * u)}px; height: {int(30 * u)}px;
        margin: -{int(15 * u)}px 0 0 -{int(15 * u)}px; border-radius: 50%;
        background: {palette.accent}; border: {max(3, int(5 * u))}px solid {palette.bg};
        box-shadow: 0 0 0 {max(2, int(3 * u))}px {palette.accent}; }}
      .typo-{index} .typo-ln-dots.is-passed {{
        animation: tp_ln_hide_{index} 0.2s ease-out var(--n, 9999s) forwards; }}
      .typo-{index} .typo-ln-dot-label {{ position: absolute; left: 150%; bottom: 120%;
        font-family: {display}; font-weight: {design.type.display_weight};
        line-height: 1.15; white-space: nowrap; color: {palette.ink}; }}
      .typo-{index} .typo-ln-dot-label.is-flip {{ left: auto; right: 150%; text-align: right; }}
    """


def _layout_items(
    scene: Mapping[str, Any],
    *,
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str, float]:
    """Dispatch ``scene``'s layout and return its items, extra CSS and length.

    One dispatch shared by :func:`render_typographic` and :func:`scene_cues`, so
    the QA gate reads exactly the cues the renderer will act on.
    """
    props = dict(scene.get("props") or {})
    layout = str(props.get("layout") or "").strip()
    layout_fn = _LAYOUTS.get(layout)
    if layout_fn is None:
        raise SceneRenderError(
            f"scene {scene.get('id', index)!r} has unknown layout {layout!r}; "
            f"choose one of {sorted(_LAYOUTS)}"
        )
    seconds = _seconds(duration)
    items, extra_css = layout_fn(props, scene, index, seconds, width, entry)
    return items, extra_css, seconds


def _cue_map(items: Sequence[_Item]) -> dict[str, str]:
    """``{item_N: phrase}`` for every item the narration can cue."""
    return {f"item_{position}": item.cue for position, item in enumerate(items) if item.cue}


def scene_cues(
    scene: Mapping[str, Any],
    *,
    index: int = 0,
    duration: float = 0.0,
    width: int = 1080,
    entry: float = 0.0,
) -> dict[str, str]:
    """The cue map :func:`render_typographic` builds for ``scene``.

    QA checks these phrases against the scene's spoken words without needing a
    :class:`DesignTokens` (it requires ``name`` and ``palette`` and cannot be
    default-constructed). ``entry`` only feeds the array layout's CSS, never a
    cue value, so the default of ``0.0`` is safe for a caller with no design.
    """
    items, _extra_css, _ = _layout_items(
        scene, index=int(index), duration=duration, width=width, entry=entry
    )
    return _cue_map(items)


def _silenced_label_entry(entry: Any, words: Sequence[Mapping[str, Any]]) -> Any:
    """A step/bar entry whose default cue the narration never says."""
    if isinstance(entry, str):
        if entry.strip() and find_cue(words, entry) is None:
            return {"label": entry, "cue": False}
        return entry
    if isinstance(entry, Mapping):
        entry = dict(entry)
        label = str(entry.get("label") or "")
        if label.strip() and find_cue(words, declared_cue(entry.get("cue"), label)) is None:
            entry["cue"] = False
        return entry
    return entry


def silence_unspoken_typographic_cues(
    props: Mapping[str, Any], words: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``props`` with every default cue the narration never says switched off.

    Only three layouts default a cue from their own text: a definition's term,
    a sequence step's label and a bar's label. ``big_stat`` and ``array`` carry
    a cue only when the author names one, and the draft layer already drops
    those that cannot resolve.
    """
    out = dict(props)
    layout = str(out.get("layout") or "").strip()
    if layout == "definition":
        term = str(out.get("term") or "").strip()
        if term and find_cue(words, term) is None:
            out["term_cue"] = False
    elif layout == "sequence":
        steps = out.get("steps")
        if isinstance(steps, Sequence) and not isinstance(steps, str):
            out["steps"] = [_silenced_label_entry(step, words) for step in steps]
    elif layout == "bar_compare":
        bars = out.get("bars")
        if isinstance(bars, Sequence) and not isinstance(bars, str):
            out["bars"] = [_silenced_label_entry(bar, words) for bar in bars]
    elif layout == "waterfall":
        steps = out.get("steps")
        if isinstance(steps, Sequence) and not isinstance(steps, str):
            out["steps"] = [_silenced_label_entry(step, words) for step in steps]
    elif layout == "donut":
        parts = out.get("parts")
        if isinstance(parts, Sequence) and not isinstance(parts, str):
            out["parts"] = [_silenced_label_entry(part, words) for part in parts]
    elif layout == "line":
        if isinstance(out.get("mark"), Mapping):
            out["mark"] = _silenced_label_entry(out["mark"], words)
        dots = out.get("dots")
        if isinstance(dots, Sequence) and not isinstance(dots, str):
            out["dots"] = [_silenced_label_entry(dot, words) for dot in dots]
    return out


def _delay_style(item: _Item, item_html: str, delay: float, following: float | None = None) -> str:
    """The item's reveal delay, plus ``--d`` when its children time off it.

    Icons and chart layers animate pieces inside the item (an icon's pop, a
    bar's grow) that must start with the item itself; they read the delay from
    the inherited ``--d`` custom property so one resolved cue drives them all.
    """
    style = f"animation-delay:{delay:.3f}s"
    if "typo-icon" in item_html or "typo-chart" in item.class_name:
        style += f";--d:{delay:.3f}s"
    if following is not None and "typo-chart" in item.class_name:
        # The next layer's reveal, for a piece that hands over to it (a line
        # chart's dot hides as the following dot starts sliding).
        style += f";--n:{following:.3f}s"
    return style


def render_typographic(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    index: int,
    duration: float,
    width: int = 1080,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> SceneRender:
    """Render ``scene`` for the compiler; ``index`` scopes every selector."""
    index = int(index)
    entry = _entry_seconds(design)
    items, extra_css, seconds = _layout_items(
        scene, index=index, duration=duration, width=width, entry=entry
    )
    cues = _cue_map(items)
    times: Mapping[str, float] = (
        resolve_cues(words or [], cues, lead=0.0) if (words and cues) else {}
    )

    stagger = min(0.40, max(0.10, (seconds - entry) / max(len(items), 1) / 2.5))
    latest = max(0.0, seconds - entry)
    delays: list[float] = []
    for position in range(len(items)):
        fallback = entry * 0.4 + position * stagger
        cued = times.get(f"item_{position}")
        # A word outside the scene (or a malformed timestamp) falls back to the
        # stagger, and no delay may outlive the scene. The clamp keeps the
        # entry motion and the line's readable hold inside the scene: a cue
        # that lands a second before the cut would otherwise flash.
        resolved = fallback if cued is None or not math.isfinite(cued) else cued
        budget = seconds - entry - _read_floor_seconds(items[position].html)
        delays.append(min(latest, max(0.0, budget), max(0.0, resolved)))

    openings = [position for position, item in enumerate(items) if item.opening]
    if openings:
        # The layout's meaningful opening (a headline, the first step, the
        # first slice and donut frame) must be on screen even when the
        # narration first names it late. Uncued scaffolding such as a sequence
        # connector or a donut track no longer masks it by revealing sooner.
        if any(delays[position] > 0.5 for position in openings):
            for position in openings:
                delays[position] = 0.4
    elif delays:
        earliest = min(range(len(delays)), key=delays.__getitem__)
        if delays[earliest] > 0.5:
            delays[earliest] = 0.4

    motion_css: list[str] = []
    rendered: list[str] = []
    for item, delay in zip(items, delays, strict=True):
        item_html = item.html
        if item.count_sums and "typo-dn-sum" in item_html:
            item_html, count_css = _donut_count_motion(
                item, item_html, index=index, part_times=delays[1 : len(item.count_sums)]
            )
            motion_css.append(count_css)
        elif 'class="typo-count"' in item_html:
            item_html, count_css = _count_motion(item_html, index=index, start=delay)
            motion_css.append(count_css)
        if "typo-gloss" in item.class_name:
            item_html = _word_motion(
                item_html, index=index, start=delay, entry=entry, seconds=seconds
            )
        rendered.append(item_html)
    body = "".join(
        f'<div class="{item.class_name}" '
        f'style="{_delay_style(item, item_html, delay, following)}">{item_html}</div>'
        for item, item_html, delay, following in zip(
            items, rendered, delays, [*delays[1:], None], strict=True
        )
    )
    markup = f'<div class="typo-{index}"><div class="typo-stage">{body}</div></div>'
    # A bar that grows inside a row still hidden by its own reveal would already
    # be full when the row appears; every bar starts growing with its row.
    bar_css = "".join(
        f"      .typo-{index} .typo-bar-row:nth-child({position}) .typo-bar "
        f"{{ animation-delay: {delay:.3f}s; }}\n"
        for position, (item, delay) in enumerate(zip(items, delays, strict=True), start=1)
        if item.class_name == "typo-bar-row"
    )
    charts = any("typo-chart" in item.class_name for item in items)
    return SceneRender(
        markup=tag_non_latin_text(markup),
        css=NON_LATIN_CSS
        + _base_css(index, design, seconds, width, entry)
        + (_chart_css(index, design, width, entry) if charts else "")
        + extra_css
        + bar_css
        + "".join(motion_css),
    )
