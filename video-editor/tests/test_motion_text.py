"""Tests for the draw, count, pill and word-highlight primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import PRIMITIVES, MotionError, build_motion


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


def test_text_primitives_are_registered():
    for name in ("draw", "count", "pill", "word_highlight"):
        assert name in PRIMITIVES


def test_draw_strokes_a_path_without_measuring_it():
    css = build_motion(spec("draw"), clip_duration=4.0)
    assert "stroke-dashoffset:1" in css.keyframes
    assert "stroke-dashoffset:0" in css.keyframes
    assert "stroke-dasharray:1" in css.props
    assert "0.800s" in css.declaration


def test_count_builds_a_digit_strip_and_animates_it_itself():
    css = build_motion(spec("count", to=3), clip_duration=4.0)
    assert ">0<" in css.markup and ">3<" in css.markup
    assert "ve-count-strip" in css.markup
    assert "translateY(-3em)" in css.keyframes
    # The strip carries its own animation, so the layer must not get one.
    assert css.declaration == ""
    assert "animation:" in css.markup


def test_count_rejects_a_backwards_range():
    with pytest.raises(MotionError):
        build_motion(spec("count", **{"from": 5, "to": 2}), clip_duration=4.0)


def test_pill_pops_up_by_its_distance():
    css = build_motion(spec("pill"), clip_duration=4.0)
    assert "translate3d(0, 16px, 0)" in css.keyframes
    assert "0.340s" in css.declaration


def test_word_highlight_is_a_marker_the_rich_text_emitter_applies():
    css = build_motion(spec("word_highlight"), clip_duration=4.0)
    assert css.keyframes == ""
    assert css.declaration == ""
