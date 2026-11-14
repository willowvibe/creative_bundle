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
    """One revealable block: markup, the words that cue it, and its class."""

    html: str
    cue: str = ""
    class_name: str = "typo-line"


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


def _big_stat(
    props: Mapping[str, Any],
    scene: Mapping[str, Any],
    index: int,
    duration: float,
    width: int,
    entry: float,
) -> tuple[list[_Item], str]:
    headline = _require(props, "headline")
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
    items = [_Item(head, cue=cue)]
    if sub:
        items.append(_Item(html.escape(sub), class_name="typo-line typo-sub"))
    return items, ""


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
        percent = max(4.0, 100.0 * value / largest)
        items.append(
            _Item(
                f'<div class="typo-bar-label">{label}</div>'
                f'<div class="typo-bar-track"><div class="typo-bar" '
                f'style="width:{percent:.1f}%"></div></div>'
                f'<div class="typo-bar-value">{value:g}{unit}</div>',
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
        font-weight: {design.type.display_weight}; font-size: {int(44 * u)}px; }}
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
    return out


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

    motion_css: list[str] = []
    rendered: list[str] = []
    for item, delay in zip(items, delays, strict=True):
        item_html = item.html
        if 'class="typo-count"' in item_html:
            item_html, count_css = _count_motion(item_html, index=index, start=delay)
            motion_css.append(count_css)
        if "typo-gloss" in item.class_name:
            item_html = _word_motion(
                item_html, index=index, start=delay, entry=entry, seconds=seconds
            )
        rendered.append(item_html)
    body = "".join(
        f'<div class="{item.class_name}" style="animation-delay:{delay:.3f}s">{item_html}</div>'
        for item, item_html, delay in zip(items, rendered, delays, strict=True)
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
    return SceneRender(
        markup=markup,
        css=_base_css(index, design, seconds, width, entry)
        + extra_css
        + bar_css
        + "".join(motion_css),
    )
