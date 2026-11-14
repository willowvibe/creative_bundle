"""Tests for the Composition model and its invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    canvas_for_aspect,
    quantise,
)
from video_editor_core.render_graph import Rect


def canvas() -> Canvas:
    return Canvas(width=1080, height=1920, fps=30)


def text_layer(layer_id: str = "title", **overrides: object) -> CompositionLayer:
    base: dict[str, object] = {
        "id": layer_id,
        "kind": "text",
        "rect": Rect(x=0, y=200, width=1080, height=300),
        "time": LayerTime(start=0.0, end=4.0),
        "content": LayerContent(text="Hello"),
    }
    base.update(overrides)
    return CompositionLayer(**base)  # type: ignore[arg-type]


def composition(**overrides: object) -> Composition:
    base: dict[str, object] = {
        "job_id": "job",
        "canvas": canvas(),
        "duration": 4.0,
        "scenes": [CompositionScene(id="s0", start=0.0, end=4.0, layers=[text_layer()])],
    }
    base.update(overrides)
    return Composition(**base)  # type: ignore[arg-type]


def test_a_minimal_composition_validates():
    comp = composition()
    assert comp.scenes[0].layers[0].content.text == "Hello"
    assert comp.audio_mode == "embed"


def test_scenes_must_be_contiguous():
    with pytest.raises(ValidationError, match="expected"):
        composition(
            scenes=[
                CompositionScene(id="s0", start=0.0, end=2.0, layers=[text_layer()]),
                CompositionScene(id="s1", start=2.5, end=4.0),
            ]
        )


def test_scenes_must_cover_the_declared_duration():
    with pytest.raises(ValidationError, match="duration"):
        composition(duration=6.0)


def test_duplicate_layer_ids_are_rejected():
    with pytest.raises(ValidationError, match="duplicate layer id"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0", start=0.0, end=4.0, layers=[text_layer("same"), text_layer("same")]
                )
            ]
        )


def test_duplicate_layer_ids_across_scenes_are_rejected():
    with pytest.raises(ValidationError, match="duplicate layer id"):
        composition(
            scenes=[
                CompositionScene(id="s0", start=0.0, end=2.0, layers=[text_layer("same")]),
                CompositionScene(id="s1", start=2.0, end=4.0, layers=[text_layer("same")]),
            ]
        )


def test_a_layer_may_not_end_past_the_composition_duration():
    with pytest.raises(ValidationError, match="past the composition duration"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[text_layer(time=LayerTime(start=0.0, end=5.0))],
                )
            ]
        )


def test_an_open_ended_layer_may_not_start_past_the_composition_duration():
    with pytest.raises(ValidationError, match="past the composition duration"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[text_layer(time=LayerTime(start=10.0))],
                )
            ]
        )


@pytest.mark.parametrize("layer_id", ["my title", 'a"b'])
def test_layer_ids_must_match_the_html_id_pattern(layer_id: str):
    with pytest.raises(ValidationError, match="String should match pattern"):
        text_layer(layer_id)


@pytest.mark.parametrize("scene_id", ["my title", 'a"b'])
def test_scene_ids_must_match_the_html_id_pattern(scene_id: str):
    with pytest.raises(ValidationError, match="String should match pattern"):
        CompositionScene(id=scene_id, start=0.0, end=4.0)


def test_unknown_motion_names_are_rejected():
    with pytest.raises(ValidationError, match="unknown motion"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[text_layer(motions=[MotionSpec(name="teleport")])],
                )
            ]
        )


def test_known_motion_names_are_accepted():
    comp = composition(
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=4.0,
                layers=[text_layer(motions=[MotionSpec(name="stamp", delay=0.2)])],
            )
        ]
    )
    assert comp.scenes[0].layers[0].motions[0].name == "stamp"


def test_a_layer_may_not_end_before_it_starts():
    with pytest.raises(ValidationError):
        LayerTime(start=2.0, end=1.0)


def test_a_layer_may_not_sit_outside_the_canvas():
    with pytest.raises(ValidationError, match="canvas"):
        composition(
            scenes=[
                CompositionScene(
                    id="s0",
                    start=0.0,
                    end=4.0,
                    layers=[text_layer(rect=Rect(x=900, y=200, width=400, height=300))],
                )
            ]
        )


def test_a_media_layer_needs_a_source_and_a_kind():
    with pytest.raises(ValidationError):
        CompositionLayer(
            id="still",
            kind="media",
            rect=Rect(x=0, y=0, width=1080, height=1920),
            time=LayerTime(start=0.0, end=1.0),
            content=LayerContent(source="still.png"),
        )


def test_a_media_layer_with_a_kind_still_needs_a_source():
    with pytest.raises(ValidationError, match="no source"):
        CompositionLayer(
            id="still",
            kind="media",
            media_kind="image",
            rect=Rect(x=0, y=0, width=1080, height=1920),
            time=LayerTime(start=0.0, end=1.0),
            content=LayerContent(source=""),
        )


def test_audio_cues_carry_id_track_and_volume():
    cue = AudioCue(id="narration", source="narration.mp3", track=150, volume=0.5)
    assert cue.id == "narration"
    assert cue.track == 150
    assert cue.volume == 0.5
    assert cue.source == "narration.mp3"
    assert cue.start == 0.0 and cue.duration is None


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        ("9:16", (1080, 1920)),
        ("16:9", (1920, 1080)),
        ("1:1", (1080, 1080)),
        ("4:5", (1080, 1350)),
        ("4:3", (1440, 1080)),
        (None, (1920, 1080)),
    ],
)
def test_canvas_for_aspect_matches_the_scene_plan_mapping(aspect, expected):
    assert canvas_for_aspect(aspect) == expected


def test_quantise_snaps_to_the_frame_grid():
    assert quantise(2.51, 30) == pytest.approx(2.5)
    assert quantise(0.02, 30) == pytest.approx(0.033333, abs=1e-5)


def test_a_word_span_may_not_ask_for_a_highlight_the_emitter_ignores():
    """``WordSpan.highlight`` is not emitted yet; a silent drop is worse."""
    from video_editor_core.composition import WordSpan

    assert WordSpan(text="one", highlight=False).highlight is False
    with pytest.raises(ValidationError, match="not emitted until slice 2"):
        WordSpan(text="one", highlight=True)


def test_a_scene_may_declare_a_transition_with_a_duration():
    """The pre-pass emits it, so the model must accept what it will process."""
    scene = {"id": "s1", "start": 0.0, "end": 4.0, "layers": [text_layer()]}
    built = CompositionScene(
        **scene,
        transition_in=MotionSpec(name="crossfade", duration=0.5),  # type: ignore[arg-type]
    )
    assert built.transition_in is not None
    assert built.transition_in.name == "crossfade"
    assert built.transition_in.duration == pytest.approx(0.5)
