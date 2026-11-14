"""Static Saju graphics for content packs (#311)."""

from __future__ import annotations

import pytest
from video_editor_core.composition import Canvas
from video_editor_core.designs import load_design
from video_editor_core.graphics import COMPONENTS, GraphicError, render_graphic

TOKENS = load_design("saju_series")
CANVAS = Canvas(width=1920, height=1080, fps=30)


def test_the_components_are_registered() -> None:
    assert {"four_pillars", "element_bars", "decade_ribbon", "medallion_ring"} <= set(COMPONENTS)


def test_four_pillars_renders_escaped_cards() -> None:
    markup = render_graphic(
        "four_pillars",
        {
            "pillars": [
                {"label": "Year", "glyph": "甲", "sub": "wood"},
                {"label": "Month", "glyph": "乙"},
                {"label": "Day", "glyph": "丙"},
                {"label": "Hour", "glyph": "丁"},
            ]
        },
        TOKENS,
        CANVAS,
    )
    assert "甲" in markup and "Hour" in markup
    assert "ve_g_card" in markup


def test_element_bars_scale_to_the_peak() -> None:
    markup = render_graphic(
        "element_bars",
        {"elements": [{"name": "Wood", "value": 40}, {"name": "Fire", "value": 20}]},
        TOKENS,
        CANVAS,
    )
    assert "Wood" in markup and "Fire" in markup
    assert "ve_g_bar" in markup


def test_decade_ribbon_marks_the_current_decade() -> None:
    markup = render_graphic(
        "decade_ribbon", {"start": 1984, "end": 2044, "current": 2026}, TOKENS, CANVAS
    )
    assert "1984" in markup and "2024" in markup


def test_medallion_ring_positions_each_item() -> None:
    markup = render_graphic("medallion_ring", {"items": ["甲", "乙", "丙", "丁"]}, TOKENS, CANVAS)
    assert markup.count("rotate(") >= 4
    assert "60" in markup


def test_unknown_components_and_bad_data_fail_closed() -> None:
    with pytest.raises(GraphicError, match="unknown graphic"):
        render_graphic("nope", {}, TOKENS, CANVAS)
    with pytest.raises(GraphicError, match="non-empty"):
        render_graphic("four_pillars", {}, TOKENS, CANVAS)


def test_strength_gauge_scales_the_bar() -> None:
    markup = render_graphic(
        "strength_gauge", {"label": "Metal", "value": 72, "max": 100}, TOKENS, CANVAS
    )
    assert "Metal" in markup and "72" in markup and "ve_g_gauge" in markup


def test_feeding_cycle_and_rings() -> None:
    cycle = render_graphic(
        "feeding_cycle",
        {"steps": [{"label": "Wood", "glyph": "木"}, {"label": "Fire", "glyph": "火"}]},
        TOKENS,
        CANVAS,
    )
    assert "Wood" in cycle and "Fire" in cycle and "↺" in cycle
    rings = render_graphic(
        "sixty_cycle_rings", {"items": ["甲子", "乙丑"], "current": 1}, TOKENS, CANVAS
    )
    assert "甲子" in rings and "乙丑" in rings


def test_countdown_has_one_number_per_beat() -> None:
    markup = render_graphic("countdown", {"from": 3}, TOKENS, CANVAS)
    assert markup.count("ve_g_count") >= 3
    for number in ("3", "2", "1"):
        assert f">{number}</div>" in markup
