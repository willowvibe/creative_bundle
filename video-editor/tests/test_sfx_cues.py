"""Tests for the Composition → SFX cue table."""

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
from video_editor_core.sfx_cues import (
    CueSpec,
    SfxCue,
    cue_table_from_dict,
    cues_from_composition,
)


def _layer(
    layer_id: str,
    start: float,
    end: float,
    motions: list[MotionSpec],
    *,
    preset: str = "",
) -> CompositionLayer:
    return CompositionLayer(
        id=layer_id,
        kind="text",
        preset=preset,
        rect=Rect(x=0, y=0, width=1080, height=1920),
        time=LayerTime(start=start, end=end),
        content=LayerContent(text="x"),
        motions=motions,
    )


def _composition(*, second_layer: CompositionLayer | None = None) -> Composition:
    second = second_layer or _layer("second", 4.0, 8.0, [MotionSpec(name="lock")])
    return Composition(
        job_id="job",
        canvas=Canvas(width=1080, height=1920, fps=30),
        duration=8.0,
        scenes=[
            CompositionScene(
                id="s0",
                start=0.0,
                end=4.0,
                layers=[
                    _layer("title", 0.0, 4.0, [MotionSpec(name="stamp", delay=0.2)]),
                    _layer("card", 1.0, 4.0, [MotionSpec(name="flip")]),
                    _layer("pill", 0.0, 4.0, [MotionSpec(name="rise")]),
                    _layer("caption", 0.5, 4.0, [MotionSpec(name="pop")], preset="caption_line"),
                ],
            ),
            CompositionScene(
                id="s1",
                start=4.0,
                end=8.0,
                layers=[second],
                transition_in=MotionSpec(name="crossfade", duration=0.5),
            ),
        ],
    )


def test_one_cue_per_table_motion_at_its_delayed_start() -> None:
    cues = cues_from_composition(_composition())
    assert [(cue.sound, cue.start) for cue in cues] == [
        ("hit", 0.2),
        ("card_flip", 1.0),
        ("latch", 4.0),
    ]
    assert cues[0].id == "hit_0000200"
    assert cues[0].motion == "stamp"
    assert cues[0].layer_id == "title"


def test_rise_and_captions_are_not_cues() -> None:
    cues = cues_from_composition(_composition())
    assert "whoosh" not in {cue.sound for cue in cues}
    assert all(cue.layer_id != "caption" for cue in cues)
    assert all(cue.motion != "rise" for cue in cues)


def test_scene_cuts_are_opt_in() -> None:
    comp = _composition(second_layer=_layer("plain", 4.0, 8.0, []))
    comp = comp.model_copy(
        update={
            "scenes": [
                comp.scenes[0],
                comp.scenes[1].model_copy(update={"transition_in": None}),
            ]
        }
    )
    assert [cue for cue in cues_from_composition(comp) if cue.motion == "scene.cut"] == []
    cued = cues_from_composition(comp, scene_cuts=True)
    assert ("whoosh", 4.0) in [(cue.sound, cue.start) for cue in cued]
    assert next(cue for cue in cued if cue.start == 4.0).motion == "scene.cut"


def test_a_scene_transition_is_a_whoosh_when_nothing_else_lands() -> None:
    comp = _composition(second_layer=_layer("plain", 4.0, 8.0, []))
    cues = cues_from_composition(comp)
    assert [(cue.sound, cue.start) for cue in cues] == [
        ("hit", 0.2),
        ("card_flip", 1.0),
        ("whoosh", 4.0),
    ]
    assert cues[-1].motion == "scene.transition"


def test_a_cluster_collapses_to_its_earliest_cue() -> None:
    comp = _composition(
        second_layer=_layer(
            "second",
            4.0,
            8.0,
            [MotionSpec(name="lock"), MotionSpec(name="pop", delay=0.01)],
        )
    )
    cues = cues_from_composition(comp, min_gap=0.05)
    # The transition whoosh, the lock and the pop are within 10 ms. The cluster
    # keeps its earliest cue; at the same start the lower priority number wins,
    # so the lock survives and the whoosh/pop are dropped.
    assert [cue.sound for cue in cues].count("whoosh") == 0
    assert [cue.sound for cue in cues].count("click") == 0
    assert [cue.sound for cue in cues].count("latch") == 1


def test_max_cues_keeps_the_earliest() -> None:
    cues = cues_from_composition(_composition(), max_cues=2)
    assert [cue.sound for cue in cues] == ["hit", "card_flip"]


def test_the_cue_list_is_deterministic() -> None:
    first = cues_from_composition(_composition())
    second = cues_from_composition(_composition())
    assert [cue.model_dump() for cue in first] == [cue.model_dump() for cue in second]


def test_a_bad_cap_or_gap_is_refused() -> None:
    with pytest.raises(ValueError, match="max_cues"):
        cues_from_composition(_composition(), max_cues=0)
    with pytest.raises(ValueError, match="min_gap"):
        cues_from_composition(_composition(), min_gap=-0.1)


def test_a_custom_table_can_rename_a_sound() -> None:
    cues = cues_from_composition(
        _composition(),
        table={"stamp": CueSpec("thud", 0.7)},
    )
    assert cues[0].sound == "thud"
    assert cues[0].volume == 0.7


def test_a_table_dict_is_validated() -> None:
    table = cue_table_from_dict({"stamp": {"sound": "thud", "volume": 0.7}})
    assert table["stamp"] == CueSpec("thud", 0.7)
    with pytest.raises(ValueError, match="no sound"):
        cue_table_from_dict({"stamp": {}})
    with pytest.raises(ValueError, match="volume"):
        cue_table_from_dict({"stamp": {"sound": "hit", "volume": 0}})


def test_a_cue_validates_its_times() -> None:
    with pytest.raises(ValueError):
        SfxCue(id="hit_0000000", sound="hit", start=-1.0)
