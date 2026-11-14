"""Tests for the transform primitives."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import PRIMITIVES, MotionError, build_motion
from video_editor_core.motion.primitives import KEN_BURNS_MOVES


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


def test_every_transform_primitive_is_registered():
    for name in (
        "drift",
        "parallax",
        "rise",
        "pop",
        "fade",
        "fade_out",
        "stamp",
        "flip",
        "lock",
        "pulse",
        "wipe",
        "draw",
        "camera",
        "mask_reveal",
        "flip_out",
        "morph",
        "cursor",
    ):
        assert name in PRIMITIVES


def test_ken_burns_moves_are_the_legacy_four():
    assert KEN_BURNS_MOVES == (
        ("scale(1) translate(0, 0)", "scale(1.12) translate(0, -1%)"),
        ("scale(1.08) translate(-2.5%, 0)", "scale(1.08) translate(2.5%, 0)"),
        ("scale(1.12) translate(0, -1%)", "scale(1.02) translate(0, 0)"),
        ("scale(1.08) translate(2.5%, 1%)", "scale(1.08) translate(-2.5%, -1%)"),
    )


def test_drift_selects_the_requested_move_and_runs_for_the_clip():
    css = build_motion(spec("drift", move=2), clip_duration=4.0)
    assert "from { transform: scale(1.12) translate(0, -1%); }" in css.keyframes
    assert "to { transform: scale(1.02) translate(0, 0); }" in css.keyframes
    assert "4.000s" in css.declaration
    assert css.props == "transform-origin:center;"


def test_drift_wraps_the_move_index():
    assert "@keyframes" in build_motion(spec("drift", move=5), clip_duration=2.0).keyframes


def test_drift_accepts_an_explicit_transform_pair():
    css = build_motion(
        spec("drift", **{"from": "scale(1)", "to": "scale(1.07)"}), clip_duration=3.0
    )
    assert "to { transform: scale(1.07); }" in css.keyframes


def test_parallax_moves_layers_by_their_rate():
    background = build_motion(spec("parallax", rate=0.35), clip_duration=6.0)
    foreground = build_motion(spec("parallax", rate=1.0), clip_duration=6.0)
    assert "translate3d(.35%,0,0)" in background.keyframes
    assert "translate3d(-.35%,0,0)" in background.keyframes
    assert "translate3d(1%,0,0)" in foreground.keyframes
    assert "translate3d(-1%,0,0)" in foreground.keyframes
    assert "6.000s" in background.declaration
    assert background.props == "transform-origin:center;"


def test_parallax_accepts_an_explicit_pair():
    css = build_motion(
        spec("parallax", **{"from": "translate3d(0,0,0)", "to": "translate3d(-2%,0,0)"}),
        clip_duration=3.0,
    )
    assert "from { transform: translate3d(0,0,0); }" in css.keyframes
    assert "to { transform: translate3d(-2%,0,0); }" in css.keyframes


def test_parallax_rejects_a_non_numeric_rate():
    with pytest.raises(MotionError, match="rate must be a number"):
        build_motion(spec("parallax", rate="fast"), clip_duration=3.0)


def test_rise_uses_its_distance_and_default_length():
    css = build_motion(spec("rise", distance=40), clip_duration=4.0)
    assert "translate3d(0,40px,0)" in css.keyframes
    assert "0.450s" in css.declaration


def test_pop_and_fade_are_registered_with_their_own_lengths():
    assert "scale(.94)" in build_motion(spec("pop"), clip_duration=4.0).keyframes
    assert "0.400s" in build_motion(spec("pop"), clip_duration=4.0).declaration
    assert "opacity:0" in build_motion(spec("fade"), clip_duration=4.0).keyframes


def test_fade_out_holds_the_last_frame():
    # ``forwards`` is the difference between a crossfade and a clip that snaps
    # back to full opacity after its exit animation.
    css = build_motion(spec("fade_out"), clip_duration=4.0)
    assert "from { opacity:1; }" in css.keyframes
    assert "to { opacity:0; }" in css.keyframes
    assert "forwards" in css.declaration


def test_fade_out_takes_its_delay_from_the_pre_pass():
    css = build_motion(
        SimpleNamespace(name="fade_out", params={}, delay=3.5, duration=0.5, ease=None),
        clip_duration=4.0,
    )
    # ``build_motion`` appends the delay as the shorthand's second <time>;
    # the fill mode can sit between the two and still parses.
    assert css.declaration.endswith("3.500s")
    assert "forwards" in css.declaration
    assert "0.500s" in css.declaration


def test_stamp_settles_from_120_percent():
    css = build_motion(spec("stamp"), clip_duration=4.0)
    assert "scale(1.2)" in css.keyframes
    assert "blur(10px)" in css.keyframes
    assert "blur(0)" in css.keyframes


def test_flip_turns_the_tile_over():
    css = build_motion(spec("flip"), clip_duration=4.0)
    assert "rotateY(90deg)" in css.keyframes
    assert "backface-visibility:hidden" in css.props


def test_lock_slides_from_the_named_side():
    left = build_motion(spec("lock", side="left"), clip_duration=4.0)
    right = build_motion(spec("lock", side="right"), clip_duration=4.0)
    assert "translate3d(-60px,0,0)" in left.keyframes
    assert "translate3d(60px,0,0)" in right.keyframes


def test_lock_rejects_an_unknown_side():
    with pytest.raises(MotionError):
        build_motion(spec("lock", side="up"), clip_duration=4.0)


def test_pulse_breathes_for_the_whole_clip():
    css = build_motion(spec("pulse"), clip_duration=3.0)
    assert "infinite alternate" in css.declaration
    assert "brightness(1.35)" in css.keyframes


def test_wipe_reveals_from_the_named_edge():
    css = build_motion(spec("wipe", direction="right"), clip_duration=3.0)
    assert "clip-path: inset(0 100% 0 0)" in css.keyframes
    assert "clip-path: inset(0 0 0 0)" in css.keyframes
    assert "will-change:clip-path" in css.props


def test_wipe_rejects_an_unknown_direction():
    with pytest.raises(MotionError):
        build_motion(spec("wipe", direction="diagonal"), clip_duration=3.0)


def test_draw_uses_the_path_length_of_one():
    # ``draw`` lives in motion/text.py: the compiler sets pathLength="1".
    css = build_motion(spec("draw"), clip_duration=3.0)
    assert "stroke-dashoffset:1" in css.keyframes
    assert "stroke-dashoffset:0" in css.keyframes


def test_camera_dollies_in_perspective():
    css = build_motion(spec("camera", distance=80, rotate=4), clip_duration=3.0)
    assert "perspective(1200px)" in css.keyframes
    assert "translate3d(0,0,-80px)" in css.keyframes
    assert "rotateX(4deg)" in css.keyframes


def test_mask_reveal_expands_a_circle():
    css = build_motion(spec("mask_reveal", radius=60), clip_duration=3.0)
    assert "circle(0% at 50% 50%)" in css.keyframes
    assert "circle(60% at 50% 50%)" in css.keyframes


def test_flip_out_turns_a_card_away():
    css = build_motion(spec("flip_out"), clip_duration=3.0)
    assert "rotateY(-90deg)" in css.keyframes
    assert "backface-visibility:hidden" in css.props


def test_morph_animates_the_two_declared_states():
    css = build_motion(
        spec("morph", **{"from": "border-radius:4px", "to": "border-radius:999px"}),
        clip_duration=3.0,
    )
    assert "border-radius:4px" in css.keyframes
    assert "border-radius:999px" in css.keyframes


def test_cursor_emits_offset_path_markup():
    css = build_motion(spec("cursor", path="M 0 0 L 100 100", size=24), clip_duration=3.0)
    assert "offset-path:path('M 0 0 L 100 100')" in css.markup
    assert "offset-distance:0%" in css.keyframes
    assert "offset-distance:100%" in css.keyframes
    assert css.declaration == ""


def test_cursor_requires_a_path():
    with pytest.raises(MotionError, match="path"):
        build_motion(spec("cursor"), clip_duration=3.0)
