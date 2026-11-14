from __future__ import annotations

import pytest
from video_editor_core.composition import Canvas
from video_editor_core.designs import load_design
from video_editor_core.layer_presets import (
    LayerPresetError,
    caption,
    chip,
    end_card,
    pill,
    scrims,
)
from video_editor_core.render_graph import Rect

CANVAS = Canvas(width=1080, height=1920, fps=30)
SAFE = (86, 154, 908, 1382)


@pytest.fixture
def tokens():
    return load_design("saju_series")


def test_scrims_cover_each_edge_full_bleed(tokens) -> None:
    top, bottom = scrims(tokens, CANVAS)
    assert top.rect == Rect(x=0, y=0, width=1080, height=320)
    assert bottom.rect == Rect(x=0, y=1300, width=1080, height=620)
    assert top.content.startswith("linear-gradient(")
    assert top.style["radius"] == 0


def test_chip_sits_inside_the_safe_area(tokens) -> None:
    placed = chip(tokens, CANVAS)
    assert placed is not None
    assert placed.rect.x == SAFE[0]
    assert placed.rect.width == SAFE[2]
    assert placed.rect.y >= SAFE[1]
    assert placed.style["font"] == "Noto Serif KR"
    assert placed.style["font_size"] == 96
    assert placed.style["text_effect"] == "shadow"


def test_a_chip_font_that_is_not_bundled_is_refused(tokens) -> None:
    tokens.layers.chip["font"] = "Noto Sans KR"
    with pytest.raises(LayerPresetError, match="Noto Sans KR"):
        chip(tokens, CANVAS)


def test_pills_stack_down_from_the_first_row(tokens) -> None:
    first = pill(tokens, CANVAS, index=0)
    second = pill(tokens, CANVAS, index=1)
    assert first.rect.y == 520
    assert second.rect.y == 520 + 92
    assert first.rect.width == 908
    assert first.style["background"] == "rgba(15,11,30,0.72)"


def test_caption_box_stays_inside_the_safe_bottom_edge(tokens) -> None:
    placed = caption(tokens, CANVAS)
    assert placed.rect.y + placed.rect.height <= 1536
    assert placed.rect.y == 1356
    assert placed.style["highlight_color"] == "#F59E0B"
    assert placed.style["upcoming_opacity"] == 0.6


def test_a_caption_box_over_the_safe_edge_is_refused(tokens) -> None:
    tokens.layers.caption_line["box"] = {"x": 90, "y": 1400, "width": 900, "height": 180}
    with pytest.raises(LayerPresetError, match="safe"):
        caption(tokens, CANVAS)


def test_end_card_places_text_and_url_in_two_layers(tokens) -> None:
    text, url = end_card(tokens, CANVAS)
    assert text.rect.y == 760
    assert url.rect.y == text.rect.y + text.rect.height
    assert text.rect.height + url.rect.height == 320
    assert text.style["font"] == "Cormorant Garamond"
    assert url.style["font_size"] == 36
    assert url.style["font_color"] == "#F59E0B"


def test_a_scrim_without_a_gradient_is_refused(tokens) -> None:
    tokens.layers.scrim["top"] = ""
    with pytest.raises(LayerPresetError, match="gradient"):
        scrims(tokens, CANVAS)


def test_an_unknown_safe_area_profile_is_refused(tokens) -> None:
    tokens.layers.safe_area = "no_such_profile"
    with pytest.raises(LayerPresetError, match="no_such_profile"):
        chip(tokens, CANVAS)
