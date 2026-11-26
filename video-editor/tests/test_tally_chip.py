"""The running tally chip (a business breakdown's cash box)."""

from __future__ import annotations

import re

import pytest
from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.tally_chip import Tally, indian_format, parse_tally, render_tally


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (999, "999"),
        (5600, "5,600"),
        (-34000, "−34,000"),
        (140000, "1,40,000"),
        (12500000, "1,25,00,000"),
    ],
)
def test_indian_grouping_with_a_true_minus(value: int, text: str) -> None:
    assert indian_format(value) == text


def test_parse_reads_label_and_value() -> None:
    assert parse_tally({"label": "గల్లా పెట్టె", "value": -34000}) == Tally(
        label="గల్లా పెట్టె", value=-34000
    )
    assert parse_tally(5600) == Tally(label="", value=5600)
    assert parse_tally(None) is None


@pytest.mark.parametrize("bad", [{"label": "x"}, {"value": "lots"}, "many", 1.5])
def test_parse_rejects_what_is_not_a_whole_amount(bad: object) -> None:
    with pytest.raises(ValueError, match="tally"):
        parse_tally(bad)


def _steps(markup: str) -> list[str]:
    return re.findall(r"class=\"tally-step[^\"]*\"[^>]*>([^<]*)<", markup)


def test_a_first_tally_shows_its_value_without_counting() -> None:
    markup, _css = render_tally(Tally("cash", -34000), None, index=2, width=1920)

    assert _steps(markup) == ["−₹34,000"]
    assert "tally-neg" in markup
    assert ">cash<" in markup


def test_a_later_tally_counts_from_the_previous_value_to_its_own() -> None:
    markup, css = render_tally(Tally("cash", 5600), -34000, index=5, width=1920)
    steps = _steps(markup)

    assert steps[0] == "−₹34,000"
    assert steps[-1] == "₹5,600"
    assert len(steps) > 2
    assert "tally-pos" in markup
    assert "@keyframes tally_5_0" in css


def test_the_chip_rides_on_every_scene_that_declares_a_tally() -> None:
    plan = {
        "job_id": "j",
        "aspect_ratio": "16:9",
        "scenes": [
            {
                "id": "a",
                "type": "narration",
                "duration_seconds": 3.0,
                "props": {"tally": {"label": "cash", "value": -12500}},
            },
            {"id": "b", "type": "narration", "duration_seconds": 3.0, "props": {}},
            {
                "id": "c",
                "type": "narration",
                "duration_seconds": 3.0,
                "props": {"tally": {"label": "cash", "value": -34000}},
            },
        ],
    }
    composition = from_scene_plan(plan)
    chips = {
        scene.id: next((layer for layer in scene.layers if layer.id.endswith("_tally")), None)
        for scene in composition.scenes
    }

    assert chips["b"] is None
    assert chips["a"] is not None and chips["c"] is not None
    assert _steps(chips["a"].content.text) == ["−₹12,500"]
    # Scene c counts on from scene a's value, across the scene without one.
    assert _steps(chips["c"].content.text)[0] == "−₹12,500"
    assert chips["c"].z > max(
        layer.z for layer in composition.scenes[2].layers if layer is not chips["c"]
    )


def test_no_two_steps_are_visible_at_the_same_instant() -> None:
    from video_editor_core.tally_chip import _keyframes

    css = _keyframes(1, 12)
    windows = []
    for match in re.finditer(r"([\d.]+)%, ([\d.]+)% \{ opacity: 1; \}", css):
        windows.append((float(match.group(1)), float(match.group(2))))
    for (_, end), (begin, _) in zip(windows, windows[1:], strict=False):
        assert begin > end


def test_each_step_is_coloured_by_its_own_sign() -> None:
    markup, _ = render_tally(Tally("cash", 5600), -34000, index=1, width=1920)
    classes = re.findall(r'class="tally-step (tally-neg|tally-pos)"', markup)
    assert classes[0] == "tally-neg"
    assert classes[-1] == "tally-pos"


def test_the_chip_shows_the_change_that_moved_it() -> None:
    markup, _ = render_tally(Tally("cash", -1812), -1488, index=4, width=1920)
    assert re.search(r'class="tally-change tally-neg"[^>]*>−₹324<', markup)

    up, _ = render_tally(Tally("cash", 2432), -3568, index=7, width=1920)
    assert re.search(r'class="tally-change tally-pos"[^>]*>\+₹6,000<', up)


def test_an_unchanged_or_first_tally_shows_no_change_line() -> None:
    first, _ = render_tally(Tally("cash", 0), None, index=0, width=1920)
    same, _ = render_tally(Tally("cash", 2432), 2432, index=8, width=1920)
    assert "tally-change" not in first and "tally-change" not in same


def test_the_count_can_wait_for_the_calculation_to_be_spoken() -> None:
    markup, _css = render_tally(Tally("cash", -1812), -1488, index=4, width=1920, delay=9.5)

    step_styles = re.findall(r'class="tally-step[^"]*" style="([^"]*)"', markup)
    assert step_styles and all("9.50s" in style for style in step_styles)
    change = re.search(r'class="tally-change[^"]*" style="([^"]*)"', markup)
    # The change pill lands when the count ends (9.5 + COUNT_SECONDS 0.9), never beside
    # an intermediate value.
    assert change is not None and "10.40s" in change.group(1)
