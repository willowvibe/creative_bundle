"""The running tally chip: one figure that follows the viewer across scenes.

A business breakdown keeps a running total on screen (the cash box at
``−34,000`` before the first sale, ``+5,600`` at closing). Any scene may declare
``tally: {label: ..., value: ...}``; the chip sits in the top corner and its
digits count from the last declared value to this one, so the number moves only
when the story changes it.

Values are whole rupees written in Indian grouping (``1,40,000``) with a true
minus sign. The steps are pre-formatted strings revealed by CSS keyframes, like
the typographic count-up, so a seeked frame always shows a legal value.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass

from video_editor_core.fonts import font_stack

#: Seconds the digits take to travel, and how many values they pass through.
COUNT_SECONDS = 1.2
COUNT_STEPS = 12
#: The count starts once the cut has settled.
COUNT_DELAY_SECONDS = 0.3
#: After a calc row's result lands, let it read before the tally reacts to it.
TALLY_AFTER_RESULT_SECONDS = 1.0

MINUS = "−"


@dataclass(frozen=True)
class Tally:
    label: str
    value: int
    prefix: str = "₹"


def indian_format(value: int) -> str:
    """``140000`` -> ``1,40,000``; negatives get a true minus sign."""
    digits = str(abs(int(value)))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pairs: list[str] = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        digits = ",".join([*pairs, tail])
    return f"{MINUS}{digits}" if value < 0 else digits


def money(value: int, prefix: str = "₹") -> str:
    """``-34000`` -> ``−₹34,000``: the sign leads, then the currency."""
    digits = indian_format(abs(value))
    return f"{MINUS}{prefix}{digits}" if value < 0 else f"{prefix}{digits}"


def parse_tally(raw: object) -> Tally | None:
    """Read a scene's ``tally`` prop: a mapping with ``value``, or a bare integer."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"tally must be a whole amount, got {raw!r}")
    if isinstance(raw, int):
        return Tally(label="", value=raw)
    if isinstance(raw, Mapping):
        value = raw.get("value")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"tally needs a whole-number `value`, got {value!r}")
        return Tally(
            label=str(raw.get("label") or "").strip(),
            value=value,
            prefix=str(raw.get("prefix", "₹")),
        )
    raise ValueError(f"tally must be a whole amount or {{label, value}}, got {raw!r}")


def _values(start: int, end: int) -> list[int]:
    """Ease-out steps from ``start`` to ``end``; one value when they are equal."""
    if start == end:
        return [end]
    last = COUNT_STEPS - 1
    values = []
    for position in range(COUNT_STEPS):
        eased = 1.0 - (1.0 - position / last) ** 3
        values.append(round(start + (end - start) * eased))
    values[0], values[-1] = start, end
    # Drop repeats so no step shows the same digits twice.
    return [v for i, v in enumerate(values) if i == 0 or v != values[i - 1]]


def _keyframes(index: int, steps: int) -> str:
    """Step ``i`` owns the ``i``-th slice of the count; the last one stays.

    A step turns on just *after* the previous one turns off, so a frame that
    lands exactly on a boundary never shows two values (same rule as the
    typographic count-up).
    """
    if steps <= 1:
        return ""
    chunks = []
    for i in range(steps):
        a = 100.0 * i / steps
        b = 100.0 * (i + 1) / steps
        on = "0%" if i == 0 else f"{a + 0.001:.4f}%"
        lead = "" if i == 0 else f"0%, {a:.4f}% {{ opacity: 0; }} "
        tail = (
            "100% { opacity: 1; }"
            if i == steps - 1
            else f"{b + 0.001:.4f}%, 100% {{ opacity: 0; }}"
        )
        hold = f"{on}, {b:.4f}% {{ opacity: 1; }} " if i < steps - 1 else f"{on} {{ opacity: 1; }} "
        chunks.append(f"      @keyframes tally_{index}_{i} {{ {lead}{hold}{tail} }}\n")
    return "".join(chunks)


def render_tally(
    tally: Tally,
    previous: int | None,
    *,
    index: int,
    width: int,
    delay: float = COUNT_DELAY_SECONDS,
) -> tuple[str, str]:
    """Markup and CSS for one scene's chip, counting on from ``previous``."""
    unit = max(0.5, width / 1920.0)
    values = _values(tally.value if previous is None else previous, tally.value)
    animated = len(values) > 1
    steps = []
    for position, value in enumerate(values):
        style = (
            f' style="animation: tally_{index}_{position} {COUNT_SECONDS:.2f}s linear '
            f'{delay:.2f}s both"'
            if animated
            else ""
        )
        tone = "tally-neg" if value < 0 else "tally-pos"
        text = html.escape(money(value, tally.prefix))
        steps.append(f'<span class="tally-step {tone}"{style}>{text}</span>')
    sign = "tally-neg" if tally.value < 0 else "tally-pos"
    label = f'<span class="tally-label">{html.escape(tally.label)}</span>' if tally.label else ""
    change = ""
    if previous is not None and previous != tally.value:
        delta = tally.value - previous
        sign_text = money(delta, tally.prefix) if delta < 0 else f"+{money(delta, tally.prefix)}"
        tone = "tally-neg" if delta < 0 else "tally-pos"
        appear = delay
        change = (
            f'<span class="tally-change {tone}" style="animation: tally_change_{index} '
            f'0.3s ease-out {appear:.2f}s both">{html.escape(sign_text)}</span>'
        )
    markup = (
        f'<div class="tally tally-{index} {sign}">{label}'
        f'<span class="tally-value">'
        f'<span class="tally-count">{"".join(steps)}</span></span>{change}</div>'
    )
    css = (
        f"      .tally-{index} {{ display: inline-flex; flex-direction: column; gap: {6 * unit:.0f}px;\n"
        f"        padding: {14 * unit:.0f}px {24 * unit:.0f}px; border-radius: {14 * unit:.0f}px;\n"
        "        background: rgba(12, 14, 18, 0.78); color: #fff;\n"
        f"        font-family: {font_stack(None)};\n"
        f"        box-shadow: 0 {4 * unit:.0f}px {18 * unit:.0f}px rgba(0, 0, 0, 0.35); }}\n"
        f"      .tally-{index} .tally-label {{ font-size: {26 * unit:.0f}px; font-weight: 600;\n"
        "        opacity: 0.85; }\n"
        f"      .tally-{index} .tally-value {{ font-size: {56 * unit:.0f}px; font-weight: 800;\n"
        "        line-height: 1; font-variant-numeric: tabular-nums; display: inline-flex; }\n"
        f"      .tally-{index} .tally-neg {{ color: #ff6b5e; }}\n"
        f"      .tally-{index} .tally-pos {{ color: #5fe08a; }}\n"
        f"      .tally-{index} .tally-count {{ display: inline-grid; justify-items: start; }}\n"
        f"      .tally-{index} .tally-step {{ grid-area: 1 / 1; }}\n"
        f"      .tally-{index} .tally-change {{ font-size: {30 * unit:.0f}px; font-weight: 700;\n"
        "        font-variant-numeric: tabular-nums; }\n"
        f"      @keyframes tally_change_{index} {{ from {{ opacity: 0; transform: translateY(-6px); }}\n"
        "        to { opacity: 1; transform: none; } }\n"
        f"{_keyframes(index, len(values))}"
    )
    return markup, css
