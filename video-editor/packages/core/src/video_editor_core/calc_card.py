"""On-screen calculations whose arithmetic is checked before anything renders.

A business breakdown lives or dies on its sums. A scene may show a ``calc``
card (``పాలు 24 L × ₹62 = −₹1,488``) and the closing scene may be a ``ledger``
that totals the day. Every row's expression is evaluated from its own numbers
and must equal the amount it shows (to the rupee, or to the shown decimals); a
ledger's rows must add up to its total. A wrong sum is a script error, not a
frame someone notices later.

``in_tally`` rows are cash-box moves: they read with a sign (``−₹1,488``,
``+₹6,000``) and, in the script check, must add up to the scene's tally change.
Other rows (a per-cup cost, a share, a monthly figure) read without a sign.
"""

from __future__ import annotations

import html
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from video_editor_core.fonts import font_stack
from video_editor_core.tally_chip import MINUS, indian_format

_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[×x*÷/+\-−]")
_OPERATORS = {"×": "*", "x": "*", "*": "*", "÷": "/", "/": "/", "+": "+", "-": "-", "−": "-"}

_ROW_KEYS = frozenset({"label", "expr", "amount", "unit", "in_tally"})

#: A row stays on screen at least this long, even when its amount is the
#: scene's last word.
MIN_ROW_HOLD_SECONDS = 1.6

#: The card fades in this long before its first row, so the box never sits empty.
CARD_LEAD_SECONDS = 0.08

#: Seconds a row takes to slide in.
ROW_ENTRY_SECONDS = 0.35


@dataclass(frozen=True)
class CalcRow:
    label: str
    expr: str
    amount: float
    unit: str = "₹"
    in_tally: bool = True


def evaluate(expr: str) -> float | None:
    """The value of ``expr`` from its numbers and operators; ``None`` without an operator.

    Units and words are ignored (``24 L × ₹62`` is ``24 * 62``). ``×``/``÷``
    bind tighter than ``+``/``−``.
    """
    numbers: list[float] = []
    operators: list[str] = []
    expect_number = True
    for token in _TOKEN.findall(expr):
        if token[0].isdigit():
            if not expect_number:
                return None
            numbers.append(float(token.replace(",", "")))
            expect_number = False
        elif token == "x" and expect_number:
            continue
        else:
            if expect_number:
                return None
            operators.append(_OPERATORS[token])
            expect_number = True
    if len(numbers) == 1 and not operators:
        # A bare figure must still equal the amount it sits beside.
        return numbers[0]
    if not operators or len(numbers) != len(operators) + 1:
        return None
    # Fold * and / first, then + and -.
    terms = [numbers[0]]
    signs: list[str] = []
    for operator, number in zip(operators, numbers[1:], strict=True):
        if operator == "*":
            terms[-1] *= number
        elif operator == "/":
            if number == 0:
                return None
            terms[-1] /= number
        else:
            signs.append(operator)
            terms.append(number)
    total = terms[0]
    for sign, term in zip(signs, terms[1:], strict=True):
        total = total + term if sign == "+" else total - term
    return total


def _decimals(amount: float) -> int:
    text = f"{amount}"
    return 0 if float(amount).is_integer() else len(text.split(".", 1)[1])


def _plain(value: float, decimals: int) -> str:
    """Indian-grouped, unsigned, with ``decimals`` places."""
    whole = int(abs(value))
    text = indian_format(whole)
    if decimals:
        fraction = f"{abs(value):.{decimals}f}".split(".", 1)[1]
        text = f"{text}.{fraction}"
    return text


def _matches(value: float, row: CalcRow) -> bool:
    target = abs(row.amount)
    if row.unit == "%":
        return abs(value * 100 - target) <= 0.5
    decimals = _decimals(row.amount)
    if decimals:
        return round(value, decimals) == round(target, decimals)
    return abs(value - target) <= 0.5


def parse_calc(raw: object) -> list[CalcRow]:
    """Read a ``calc`` prop (a list of rows) and check every row's arithmetic."""
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError(f"calc must be a list of rows, got {raw!r}")
    rows: list[CalcRow] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"calc row must be a mapping, got {item!r}")
        unknown = sorted(str(key) for key in item if key not in _ROW_KEYS)
        if unknown:
            # Usually a comma inside an unquoted flow-mapping value
            # (`expr: ₹3,081 ÷ 5` splits into `expr: ₹3` and a stray key).
            raise ValueError(
                f"calc row {item.get('label')!r} has unknown key(s) {unknown}; "
                "quote values that contain commas"
            )
        amount = item.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int | float):
            raise ValueError(f"calc row needs a numeric `amount`, got {amount!r}")
        row = CalcRow(
            label=str(item.get("label") or "").strip(),
            expr=str(item.get("expr") or "").strip(),
            amount=amount,
            unit=str(item.get("unit", "₹")),
            in_tally=bool(item.get("in_tally", True)),
        )
        value = evaluate(row.expr)
        if value is not None and not _matches(value, row):
            decimals = max(_decimals(row.amount), 0 if float(value).is_integer() else 2)
            shown = f"{value * 100:.0f}%" if row.unit == "%" else _plain(value, decimals)
            wanted = f"{abs(row.amount):g}%" if row.unit == "%" else _plain(row.amount, decimals)
            raise ValueError(f"calc row {row.label!r}: {row.expr} = {shown}, not {wanted}")
        rows.append(row)
    return rows


def format_amount(row: CalcRow) -> str:
    """``−₹1,488`` / ``+₹6,000`` for tally rows, ``₹6.67`` / ``42%`` otherwise."""
    if row.unit == "%":
        return f"{row.amount:g}%"
    body = f"{row.unit}{_plain(row.amount, _decimals(row.amount))}"
    if not row.in_tally:
        return f"{MINUS}{body}" if row.amount < 0 else body
    return f"{MINUS}{body}" if row.amount < 0 else f"+{body}"


def ledger_total_error(rows: Sequence[CalcRow], total: float) -> str | None:
    """Why ``total`` is not the sum of ``rows``, or ``None`` when it is."""
    added = sum(row.amount for row in rows)
    if math.isclose(added, total, abs_tol=0.5):
        return None
    return f"ledger rows add up to ₹{_plain(added, 0)}, not ₹{_plain(total, 0)}"


def _tone(row: CalcRow) -> str:
    if not row.in_tally:
        return "calc-plain"
    return "calc-neg" if row.amount < 0 else "calc-pos"


def _row_markup(row: CalcRow, css_class: str, delay: float) -> str:
    expr = f'<span class="{css_class}-expr">{html.escape(row.expr)} =</span>' if row.expr else ""
    return (
        f'<div class="{css_class}-row" style="animation-delay: {delay:.2f}s">'
        f'<span class="{css_class}-label">{html.escape(row.label)}</span>{expr}'
        f'<span class="{css_class}-amount {_tone(row)}">{html.escape(format_amount(row))}</span>'
        "</div>"
    )


def render_calc_card(
    rows: Sequence[CalcRow], *, index: int, width: int, reveal_at: Sequence[float]
) -> tuple[str, str]:
    """A compact dark card that appears with its first row and grows as rows are added.

    The box fades in ``CARD_LEAD_SECONDS`` before row 0; until a row's moment it
    takes no height (``max-height: 0``), so the box never shows reserved space.
    """
    unit = max(0.5, width / 1920.0)
    times = [reveal_at[i] if i < len(reveal_at) else 0.3 for i in range(len(rows))]
    body = "".join(_row_markup(row, "calc", times[i]) for i, row in enumerate(rows))
    card_at = max(0.0, (min(times) if times else 0.3) - CARD_LEAD_SECONDS)
    markup = (
        f'<div class="calc calc-{index}" style="animation: calc_card_{index} '
        f'{CARD_LEAD_SECONDS:.2f}s ease-out {card_at:.2f}s both">{body}</div>'
    )
    selector = f".calc-{index}"
    row_height = 64 * unit
    row_gap = 10 * unit
    css = (
        f"      {selector} {{ display: inline-flex; flex-direction: column; opacity: 0;\n"
        f"        padding: {16 * unit:.0f}px {24 * unit:.0f}px; border-radius: {14 * unit:.0f}px;\n"
        "        background: rgba(12, 14, 18, 0.80); color: #fff;\n"
        f"        font-family: {font_stack(None)};\n"
        f"        box-shadow: 0 {4 * unit:.0f}px {18 * unit:.0f}px rgba(0, 0, 0, 0.35); }}\n"
        f"      @keyframes calc_card_{index} {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}\n"
        f"      {selector} .calc-row {{ display: grid; grid-template-columns: auto auto auto;\n"
        f"        column-gap: {16 * unit:.0f}px; align-items: baseline; overflow: hidden;\n"
        f"        animation: calc_grow_{index} {ROW_ENTRY_SECONDS}s ease-out both; }}\n"
        f"      @keyframes calc_grow_{index} {{\n"
        "        from { opacity: 0; max-height: 0; margin-top: 0; transform: translateY(8px); }\n"
        f"        to {{ opacity: 1; max-height: {row_height:.0f}px; margin-top: {row_gap:.0f}px;\n"
        "          transform: none; } }\n"
        f"      {selector} .calc-row:first-child {{ margin-top: 0 !important; }}\n"
        f"      {selector} .calc-label {{ font-size: {34 * unit:.0f}px; font-weight: 600; }}\n"
        f"      {selector} .calc-expr {{ font-size: {32 * unit:.0f}px;\n"
        "        color: rgba(255, 255, 255, 0.82);\n"
        "        font-variant-numeric: tabular-nums; }\n"
        f"      {selector} .calc-amount {{ font-size: {42 * unit:.0f}px; font-weight: 800;\n"
        "        text-align: right; font-variant-numeric: tabular-nums; }\n"
        f"      {selector} .calc-neg {{ color: #ff6b5e; }}\n"
        f"      {selector} .calc-pos {{ color: #5fe08a; }}\n"
    )
    return markup, css


def render_ledger(
    rows: Sequence[CalcRow],
    *,
    total_label: str,
    total: float,
    index: int,
    width: int,
    duration: float,
    heading: str = "",
) -> tuple[str, str]:
    """The closing ledger: rows in turn, then a ruled total."""
    unit = max(0.5, width / 1920.0)
    spread = max(0.4, duration * 0.55)
    step = spread / max(1, len(rows))
    body = "".join(_row_markup(row, "ledger", 0.3 + i * step) for i, row in enumerate(rows))
    total_row = CalcRow(total_label, "", total)
    total_delay = 0.3 + spread + 0.2
    heading_markup = f'<div class="ledger-heading">{html.escape(heading)}</div>' if heading else ""
    markup = (
        f'<div class="ledger ledger-{index}">{heading_markup}{body}'
        f'<div class="ledger-total ledger-row" style="animation-delay: {total_delay:.2f}s">'
        f'<span class="ledger-label">{html.escape(total_label)}</span>'
        f'<span class="ledger-amount {_tone(total_row)}">{html.escape(format_amount(total_row))}'
        "</span></div></div>"
    )
    selector = f".ledger-{index}"
    css = (
        f"      {selector} {{ position: absolute; inset: 0; display: flex; flex-direction: column;\n"
        f"        justify-content: flex-start; padding: {80 * unit:.0f}px {360 * unit:.0f}px 0;\n"
        f"        gap: {4 * unit:.0f}px;\n"
        f"        font-family: {font_stack(None)}; color: #1d1b18; }}\n"
        f"      {selector} .ledger-heading {{ font-size: {40 * unit:.0f}px; font-weight: 700;\n"
        f"        margin-bottom: {12 * unit:.0f}px; }}\n"
        f"      {selector} .ledger-row {{ display: grid; grid-template-columns: 1fr auto;\n"
        f"        column-gap: {24 * unit:.0f}px; align-items: baseline; opacity: 0;\n"
        f"        animation: ledger_in_{index} {ROW_ENTRY_SECONDS}s ease-out both; }}\n"
        f"      {selector} .ledger-label {{ font-size: {32 * unit:.0f}px; font-weight: 600; }}\n"
        f"      {selector} .ledger-amount {{ font-size: {34 * unit:.0f}px; font-weight: 800;\n"
        "        text-align: right; font-variant-numeric: tabular-nums; }\n"
        f"      {selector} .ledger-total {{ border-top: {4 * unit:.0f}px solid #1d1b18;\n"
        f"        padding-top: {14 * unit:.0f}px; margin-top: {8 * unit:.0f}px; }}\n"
        f"      {selector} .ledger-total .ledger-label,\n"
        f"      {selector} .ledger-total .ledger-amount {{ font-size: {52 * unit:.0f}px; }}\n"
        f"      {selector} .calc-neg {{ color: #c0392b; }}\n"
        f"      {selector} .calc-pos {{ color: #1e8449; }}\n"
        f"      @keyframes ledger_in_{index} {{ from {{ opacity: 0; transform: translateY(12px); }}\n"
        "        to { opacity: 1; transform: none; } }\n"
    )
    return markup, css
