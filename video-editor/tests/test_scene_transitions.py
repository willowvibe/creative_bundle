"""Tests for the scene-transition pre-pass."""

from __future__ import annotations

import pytest
from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
)
from video_editor_core.render_graph import Rect
from video_editor_core.scene_transitions import (
    SceneTransitionError,
    apply_scene_transitions,
)


def _layer(
    layer_id: str,
    start: float,
    end: float | None,
    *,
    motions: list[MotionSpec] | None = None,
) -> CompositionLayer:
    return CompositionLayer(
        id=layer_id,
        kind="text",
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=start, end=end),
        content=LayerContent(text="x"),
        motions=list(motions or []),
    )


def _scene(
    scene_id: str,
    start: float,
    end: float,
    layers: list[CompositionLayer],
    *,
    transition: MotionSpec | None = None,
) -> CompositionScene:
    return CompositionScene(
        id=scene_id,
        start=start,
        end=end,
        layers=layers,
        transition_in=transition,
    )


def _two_scenes(
    *,
    transition: MotionSpec | None = None,
    outgoing: list[CompositionLayer] | None = None,
    incoming: list[CompositionLayer] | None = None,
) -> Composition:
    first = outgoing or [_layer("a", 0.0, 4.0, motions=[MotionSpec(name="drift")])]
    second = incoming or [_layer("b", 4.0, 8.0, motions=[MotionSpec(name="drift")])]
    return Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=8.0,
        scenes=[
            _scene("s0", 0.0, 4.0, first),
            _scene(
                "s1",
                4.0,
                8.0,
                second,
                transition=transition or MotionSpec(name="crossfade", duration=0.5),
            ),
        ],
    )


def test_a_composition_without_transitions_is_returned_unchanged():
    comp = Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=4.0,
        scenes=[_scene("s0", 0.0, 4.0, [_layer("a", 0.0, 4.0)])],
    )
    assert apply_scene_transitions(comp) is comp


def test_crossfade_appends_a_fade_to_every_incoming_layer():
    comp = _two_scenes(
        incoming=[
            _layer("b", 4.0, 8.0, motions=[MotionSpec(name="drift")]),
            _layer("caption", 4.5, 6.0),
        ]
    )
    out = apply_scene_transitions(comp)
    first, second = out.scenes[1].layers
    assert [motion.name for motion in first.motions] == ["drift", "fade"]
    assert first.motions[-1].duration == pytest.approx(0.5)
    assert first.motions[-1].ease == "linear"
    # Every layer fades in, including one that starts after the boundary.
    assert [motion.name for motion in second.motions] == ["fade"]


def test_crossfade_extends_the_outgoing_full_window_layer():
    comp = _two_scenes()
    out = apply_scene_transitions(comp)
    layer = out.scenes[0].layers[0]
    assert layer.time.end == pytest.approx(4.5)
    assert [motion.name for motion in layer.motions] == ["drift", "fade_out"]
    assert layer.motions[-1].delay == pytest.approx(4.0)
    assert layer.motions[-1].duration == pytest.approx(0.5)
    assert layer.motions[-1].ease == "linear"


def test_crossfade_leaves_a_short_lived_layer_alone():
    comp = _two_scenes(
        outgoing=[
            _layer("a", 0.0, 4.0, motions=[MotionSpec(name="drift")]),
            _layer("caption", 1.0, 2.5),
        ]
    )
    out = apply_scene_transitions(comp)
    caption = out.scenes[0].layers[1]
    assert caption.time.end == pytest.approx(2.5)
    assert [motion.name for motion in caption.motions] == []


def test_crossfade_closes_an_open_ended_outgoing_layer():
    comp = _two_scenes(outgoing=[_layer("a", 0.0, None)])
    out = apply_scene_transitions(comp)
    layer = out.scenes[0].layers[0]
    assert layer.time.end == pytest.approx(4.5)
    assert layer.motions[-1].name == "fade_out"


def test_a_transition_on_the_first_scene_fades_in_without_fading_out():
    comp = Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=4.0,
        scenes=[
            _scene(
                "s0",
                0.0,
                4.0,
                [_layer("a", 0.0, 4.0)],
                transition=MotionSpec(name="crossfade", duration=0.5),
            )
        ],
    )
    out = apply_scene_transitions(comp)
    layer = out.scenes[0].layers[0]
    assert layer.time.end == pytest.approx(4.0)
    assert [motion.name for motion in layer.motions] == ["fade"]


def test_the_pass_clears_transition_in_and_is_idempotent():
    once = apply_scene_transitions(_two_scenes())
    twice = apply_scene_transitions(once)
    assert once.scenes[1].transition_in is None
    assert twice is once
    assert once.model_dump() == twice.model_dump()


def test_the_result_still_satisfies_the_composition_invariants():
    out = apply_scene_transitions(_two_scenes())
    # ``model_copy`` skips validation, so prove the emitted IR is a legal model.
    Composition.model_validate(out.model_dump())


def test_scene_windows_are_not_changed():
    comp = _two_scenes()
    out = apply_scene_transitions(comp)
    assert [(scene.start, scene.end) for scene in out.scenes] == [
        (scene.start, scene.end) for scene in comp.scenes
    ]


def test_unknown_transition_names_are_rejected():
    comp = _two_scenes(transition=MotionSpec(name="whip-pan", duration=0.5))
    with pytest.raises(SceneTransitionError, match="unknown transition 'whip-pan'"):
        apply_scene_transitions(comp)


def test_a_transition_needs_an_explicit_duration():
    comp = _two_scenes(transition=MotionSpec(name="crossfade"))
    with pytest.raises(SceneTransitionError, match="needs an explicit duration"):
        apply_scene_transitions(comp)


def test_a_transition_longer_than_both_sides_is_rejected():
    comp = Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=8.0,
        scenes=[
            _scene("s0", 0.0, 1.0, [_layer("a", 0.0, 1.0)]),
            _scene(
                "s1",
                1.0,
                8.0,
                [_layer("b", 1.0, 8.0)],
                transition=MotionSpec(name="crossfade", duration=2.0),
            ),
        ],
    )
    with pytest.raises(SceneTransitionError, match="longer than the 1.000s"):
        apply_scene_transitions(comp)


def test_transition_params_are_rejected_rather_than_dropped():
    comp = _two_scenes(transition=MotionSpec(name="crossfade", duration=0.5, params={"x": 1}))
    with pytest.raises(SceneTransitionError, match="takes no params"):
        apply_scene_transitions(comp)


def test_a_delayed_transition_is_rejected_rather_than_dropped():
    comp = _two_scenes(transition=MotionSpec(name="crossfade", duration=0.5, delay=0.2))
    with pytest.raises(SceneTransitionError, match="cannot be delayed"):
        apply_scene_transitions(comp)


def test_an_outgoing_layer_that_already_outlives_the_composition_is_an_error():
    comp = _two_scenes(outgoing=[_layer("a", 0.0, 8.0)])
    with pytest.raises(SceneTransitionError, match="past the composition duration"):
        apply_scene_transitions(comp)


@pytest.mark.parametrize(
    ("name", "incoming", "outgoing"),
    [
        ("ink_bleed", "mask_reveal", "fade_out"),
        ("mist_dissolve", "fade", "fade_out"),
        ("push_through", "camera", None),
        ("hanji_turn", "flip", "flip_out"),
        ("seal_stamp", "stamp", None),
    ],
)
def test_named_transitions_emit_their_primitives(
    name: str, incoming: str, outgoing: str | None
) -> None:
    result = apply_scene_transitions(_two_scenes(transition=MotionSpec(name=name, duration=0.5)))
    incoming_motions = [motion.name for motion in result.scenes[1].layers[0].motions]
    assert incoming in incoming_motions
    outgoing_layer = result.scenes[0].layers[0]
    outgoing_motions = [motion.name for motion in outgoing_layer.motions]
    if outgoing is None:
        assert outgoing_layer.time.end == 4.0
        assert "fade_out" not in outgoing_motions
    else:
        assert outgoing in outgoing_motions
        assert outgoing_layer.time.end == 4.5


def test_cut_emits_no_motion_and_does_not_extend() -> None:
    result = apply_scene_transitions(_two_scenes(transition=MotionSpec(name="cut", duration=0.5)))
    assert [motion.name for motion in result.scenes[1].layers[0].motions] == ["drift"]
    assert result.scenes[0].layers[0].time.end == 4.0
