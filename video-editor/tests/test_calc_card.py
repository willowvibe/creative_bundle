"""On-screen calculations whose arithmetic is checked before anything renders."""

from __future__ import annotations

import re

import pytest
from video_editor_core.calc_card import (
    CalcRow,
    evaluate,
    format_amount,
    ledger_total_error,
    parse_calc,
    render_calc_card,
    render_ledger,
)


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("24 L × ₹62", 1488.0),
        ("₹3,081 ÷ 5", 616.2),
        ("400 × ₹0.60", 240.0),
        ("24 L × ₹2 × 30", 1440.0),
        ("₹2,432 × 30", 72960.0),
        ("₹1,488 ÷ ₹3,568", 1488 / 3568),
        ("రోజుకి", None),
    ],
)
def test_expressions_evaluate_from_their_numbers(expr: str, value: float | None) -> None:
    result = evaluate(expr)
    assert result == (pytest.approx(value) if value is not None else None)


def test_a_row_whose_sum_is_wrong_is_refused() -> None:
    with pytest.raises(ValueError, match="24 L × ₹62 = 1,488, not 1,500"):
        parse_calc([{"label": "పాలు", "expr": "24 L × ₹62", "amount": -1500}])


def test_rounding_to_the_rupee_is_allowed() -> None:
    rows = parse_calc([{"label": "గ్యాస్", "expr": "₹3,081 ÷ 5", "amount": -616}])
    assert rows == [CalcRow(label="గ్యాస్", expr="₹3,081 ÷ 5", amount=-616)]


def test_percent_rows_compare_against_the_ratio() -> None:
    rows = parse_calc(
        [
            {
                "label": "పాల వాటా",
                "expr": "₹1,488 ÷ ₹3,568",
                "amount": 42,
                "unit": "%",
                "in_tally": False,
            }
        ]
    )
    assert format_amount(rows[0]) == "42%"


def test_amounts_read_as_money_with_a_leading_sign_only_when_they_move_the_tally() -> None:
    assert format_amount(CalcRow("x", "", -1488)) == "−₹1,488"
    assert format_amount(CalcRow("x", "", 6000)) == "+₹6,000"
    assert format_amount(CalcRow("x", "", 6.67, in_tally=False)) == "₹6.67"
    assert format_amount(CalcRow("x", "", 72960, in_tally=False)) == "₹72,960"


def test_the_ledger_total_must_be_the_sum_of_its_rows() -> None:
    rows = [CalcRow("పాలు", "", -1488), CalcRow("అమ్మకం", "", 6000)]
    assert ledger_total_error(rows, 4512) is None
    assert "rows add up to ₹4,512, not ₹4,500" in (ledger_total_error(rows, 4500) or "")


def test_the_card_shows_every_row_and_reveals_them_in_turn() -> None:
    rows = [CalcRow("టీ పొడి", "1 kg × ₹180", -180), CalcRow("చక్కెర", "3.2 kg × ₹45", -144)]
    markup, css = render_calc_card(rows, index=3, width=1920, reveal_at=[0.5, 2.0])

    assert "1 kg × ₹180" in markup and "−₹180" in markup and "−₹144" in markup
    delays = [float(d) for d in re.findall(r"animation-delay: ([\d.]+)s", markup)]
    assert delays == [0.5, 2.0]
    assert "@keyframes calc_grow_3" in css


def test_the_ledger_draws_rows_then_a_ruled_total() -> None:
    rows = [CalcRow("పాలు", "", -1488), CalcRow("అమ్మకం", "", 6000)]
    markup, _css = render_ledger(
        rows, total_label="రోజుకి మిగులు", total=4512, index=9, width=1920, duration=8.0
    )

    assert markup.index("−₹1,488") < markup.index("+₹6,000") < markup.index("₹4,512")
    assert 'class="ledger-total' in markup


def _plan(scenes: list[dict]) -> dict:
    return {"job_id": "j", "aspect_ratio": "16:9", "design": "cream_editorial", "scenes": scenes}


def test_a_calc_row_appears_when_its_amount_is_spoken() -> None:
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    scene = {
        "id": "milk",
        "type": "narration",
        "duration_seconds": 6.0,
        "props": {
            "calc": [{"label": "పాలు", "expr": "24 L × ₹62", "amount": -1488}],
            "word_times": [
                {"word": "రోజుకి", "start": 0.2, "end": 0.8},
                {"word": "₹1,488", "start": 3.1, "end": 3.9},
            ],
        },
    }
    composition = from_scene_plan(_plan([scene]))
    card = next(layer for layer in composition.scenes[0].layers if layer.id.endswith("_calc"))

    assert "animation-delay: 3.10s" in card.content.text
    assert card.rect.x > 1920 / 2  # top-right, clear of the tally chip and captions


def test_a_ledger_scene_renders_its_rows_and_total() -> None:
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    scene = {
        "id": "day",
        "type": "typographic",
        "duration_seconds": 8.0,
        "props": {
            "layout": "ledger",
            "heading": "ఒక రోజు లెక్క",
            "rows": [
                {"label": "ఖర్చు", "amount": -3568},
                {"label": "అమ్మకం", "amount": 6000},
            ],
            "total": {"label": "మిగులు", "amount": 2432},
        },
    }
    composition = from_scene_plan(_plan([scene]))
    layer = composition.scenes[0].layers[0]

    assert "ledger-total" in layer.content.text and "+₹2,432" in layer.content.text


def test_a_truncated_expression_is_caught_not_skipped() -> None:
    # YAML flow mappings split on commas: `expr: ₹3,081 ÷ 5` arrives as `expr: ₹3`
    # plus a stray key. Both symptoms must be refused.
    with pytest.raises(ValueError, match="unknown key"):
        parse_calc([{"label": "గ్యాస్", "expr": "₹3", "081 ÷ 5 రోజులు": None, "amount": -616}])
    with pytest.raises(ValueError, match="₹3 = 3, not 616"):
        parse_calc([{"label": "గ్యాస్", "expr": "₹3", "amount": -616}])


def test_a_bare_number_that_equals_its_amount_is_fine() -> None:
    assert parse_calc([{"label": "హెల్పర్", "expr": "₹600", "amount": -600}])


def test_the_ledger_sits_high_enough_to_clear_the_captions() -> None:
    rows = [CalcRow("x", "", -1)] * 8
    _markup, css = render_ledger(rows, total_label="t", total=-8, index=1, width=1920, duration=8.0)
    assert "justify-content: flex-start" in css
    assert "padding: 80px" in css  # starts ~7% down; nine rows end near 55%


def test_a_row_spoken_at_the_very_end_still_holds_on_screen() -> None:
    from video_editor_core.adapters.from_scene_plan import from_scene_plan
    from video_editor_core.calc_card import MIN_ROW_HOLD_SECONDS

    scene = {
        "id": "cup",
        "type": "narration",
        "duration_seconds": 6.0,
        "props": {
            "calc": [
                {"label": "per cup", "expr": "₹2,668 ÷ 400", "amount": 6.67, "in_tally": False}
            ],
            "word_times": [{"word": "₹6.67.", "start": 5.8, "end": 6.0}],
        },
    }
    card = next(
        layer
        for layer in from_scene_plan(_plan([scene])).scenes[0].layers
        if layer.id.endswith("_calc")
    )
    delay = float(re.search(r"animation-delay: ([\d.]+)s", card.content.text).group(1))

    assert delay <= 6.0 - MIN_ROW_HOLD_SECONDS


def test_a_nine_row_ledger_ends_above_the_caption_band() -> None:
    _markup, css = render_ledger(
        [CalcRow("x", "", -1)] * 8, total_label="t", total=-8, index=1, width=1920, duration=8.0
    )
    top = int(re.search(r"padding: (\d+)px", css).group(1))
    row = int(re.search(r"\.ledger-label \{ font-size: (\d+)px", css).group(1))
    gap = int(re.search(r"gap: (\d+)px", css).group(1))
    # heading + 8 rows + ruled total, at ~1.3 line height, must end by 62% of 1080.
    estimate = top + 60 + 8 * (row * 1.3 + gap) + 90
    assert estimate <= 0.62 * 1080


def test_the_card_appears_just_before_its_first_row_not_with_the_scene() -> None:
    from video_editor_core.calc_card import CARD_LEAD_SECONDS

    rows = [CalcRow("టీ పొడి", "1 kg × ₹180", -180), CalcRow("చక్కెర", "3.2 kg × ₹45", -144)]
    markup, css = render_calc_card(rows, index=3, width=1920, reveal_at=[2.5, 4.0])

    card_delay = float(
        re.search(r'class="calc calc-3" style="[^"]*?([\d.]+)s both', markup).group(1)
    )
    assert card_delay == pytest.approx(2.5 - CARD_LEAD_SECONDS)
    assert re.search(r"\.calc-3 \{[^}]*opacity: 0", css)


def test_hidden_rows_take_no_space_so_the_card_grows_row_by_row() -> None:
    _markup, css = render_calc_card(
        [CalcRow("x", "", -1), CalcRow("y", "", -2)], index=2, width=1920, reveal_at=[1.0, 3.0]
    )
    assert "max-height: 0" in css
    assert not re.search(r"\.calc-2 \{[^}]*gap:", css)


def test_the_expression_is_dimmed_by_colour_not_opacity() -> None:
    # HyperFrames dropped the whole expression when it carried its own opacity
    # inside the animated, clipped row (chai-stall pilot); colour renders.
    _markup, css = render_calc_card(
        [CalcRow("x", "1 × ₹2", -2)], index=1, width=1920, reveal_at=[0.5]
    )
    rule = re.search(r"\.calc-1 \.calc-expr \{([^}]*)\}", css).group(1)
    assert "opacity" not in rule
    assert "color: rgba(255, 255, 255" in rule
