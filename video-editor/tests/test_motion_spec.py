"""Tests for the motion registry: naming, defaults, clamping, validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from video_editor_core.motion import (
    PRIMITIVES,
    MotionCSS,
    MotionError,
    build_motion,
    register,
)


def spec(name: str, **params: object) -> SimpleNamespace:
    return SimpleNamespace(name=name, params=params, delay=0.0, duration=None, ease=None)


@pytest.fixture
def dummy():
    @register("_dummy")
    def _build(params, name, duration, ease):
        return MotionCSS(
            keyframes=f"@keyframes {name} {{}}",
            declaration=f"{name} {duration:.3f}s {ease}",
        )

    yield
    PRIMITIVES.pop("_dummy", None)


def test_unknown_motion_lists_the_registry(dummy):
    with pytest.raises(MotionError) as exc:
        build_motion(spec("nope"), clip_duration=2.0)
    assert "nope" in str(exc.value)
    assert "_dummy" in str(exc.value)


def test_identical_name_and_params_share_a_keyframe_name(dummy):
    a = build_motion(spec("_dummy", distance=10), clip_duration=2.0)
    b = build_motion(spec("_dummy", distance=10), clip_duration=9.0)
    c = build_motion(spec("_dummy", distance=12), clip_duration=2.0)
    assert a.keyframes == b.keyframes
    assert a.keyframes != c.keyframes


def test_duration_defaults_to_the_clip(dummy):
    assert "3.500s" in build_motion(spec("_dummy"), clip_duration=3.5).declaration


def test_duration_is_clamped_to_the_remaining_clip(dummy):
    # A 2.0 s clip with a 0.5 s delay leaves 1.5 s. The two times must stay
    # distinct: when delay == duration a substring match cannot tell them
    # apart, which is how an inert clamp survived review once already.
    motion = spec("_dummy")
    motion.delay = 0.5
    motion.duration = 9.0
    declaration = build_motion(motion, clip_duration=2.0).declaration
    assert declaration.split()[1] == "1.500s"
    assert declaration.endswith(" 0.500s")


def test_delay_is_written_as_the_second_time(dummy):
    motion = spec("_dummy")
    motion.delay = 0.25
    assert build_motion(motion, clip_duration=2.0).declaration.endswith(" 0.250s")


def test_delay_is_clamped_inside_the_clip(dummy):
    motion = spec("_dummy")
    motion.delay = 9.0
    assert build_motion(motion, clip_duration=2.0).declaration.endswith(" 1.999s")


def test_a_primitive_with_no_declaration_is_returned_untouched():
    @register("_silent")
    def _build(params, name, duration, ease):
        return MotionCSS(keyframes="", declaration="", props="", markup="<i></i>")

    try:
        built = build_motion(spec("_silent"), clip_duration=2.0)
        assert built.declaration == ""
        assert built.markup == "<i></i>"
    finally:
        PRIMITIVES.pop("_silent", None)


def test_a_delay_with_no_declaration_is_rejected():
    # There is no ``animation`` value to append the delay to, so accepting it
    # would render the motion at t=0 while its duration was clamped by a delay
    # that went nowhere.
    @register("_silent")
    def _build(params, name, duration, ease):
        return MotionCSS(keyframes="", declaration="", props="", markup="<i></i>")

    try:
        motion = spec("_silent")
        motion.delay = 0.5
        with pytest.raises(MotionError, match="delay"):
            build_motion(motion, clip_duration=2.0)
    finally:
        PRIMITIVES.pop("_silent", None)


@pytest.mark.parametrize("tempo", [0, -1.0, float("nan"), float("inf"), "fast", None])
def test_a_bad_tempo_is_rejected(dummy, tempo):
    with pytest.raises(MotionError, match="tempo"):
        build_motion(spec("_dummy"), clip_duration=2.0, tempo=tempo)


def test_negative_delay_is_rejected(dummy):
    motion = spec("_dummy")
    motion.delay = -1.0
    with pytest.raises(MotionError):
        build_motion(motion, clip_duration=2.0)


def test_non_finite_duration_is_rejected(dummy):
    motion = spec("_dummy")
    motion.duration = float("nan")
    with pytest.raises(MotionError):
        build_motion(motion, clip_duration=2.0)


def test_motioncss_addition_concatenates_parts():
    css = MotionCSS(keyframes="a", declaration="one") + MotionCSS(keyframes="b", props="p")
    assert css.keyframes == "a\nb"
    assert css.declaration == "one"
    assert css.props == "p"
