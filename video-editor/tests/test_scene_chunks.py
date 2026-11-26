"""Tests for cutting a composition into scene-aligned chunks."""

from __future__ import annotations

import math

import pytest
from video_editor_core.composition import (
    AudioCue,
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
)
from video_editor_core.render_graph import Rect
from video_editor_core.scene_chunks import (
    ChunkSpan,
    cut_points,
    frame_count,
    plan_scene_chunks,
    slice_composition,
)


def _layer(layer_id: str, start: float, end: float | None) -> CompositionLayer:
    return CompositionLayer(
        id=layer_id,
        kind="text",
        rect=Rect(x=0, y=0, width=100, height=100),
        time=LayerTime(start=start, end=end),
        content=LayerContent(text=layer_id),
    )


def _comp(
    durations: list[float],
    extra: dict[int, list[CompositionLayer]] | None = None,
    fps: int = 30,
) -> Composition:
    scenes: list[CompositionScene] = []
    t = 0.0
    for i, d in enumerate(durations):
        end = round(t + d, 6)
        layers = [_layer(f"s{i}_text", t, end), *((extra or {}).get(i, []))]
        scenes.append(CompositionScene(id=f"s{i}", start=t, end=end, layers=layers))
        t = end
    return Composition(
        job_id="job",
        canvas=Canvas(width=1920, height=1080, fps=fps),
        duration=t,
        scenes=scenes,
        audio=[AudioCue(id="narration", source="/n.mp3", start=0.0)],
    )


def test_frame_count_uses_the_frame_grid() -> None:
    assert frame_count(3.0, 30) == 90
    assert frame_count(1.01, 30) == 30


def test_every_scene_boundary_is_a_cut_when_layers_stay_inside() -> None:
    assert cut_points(_comp([10, 10, 10])) == {1, 2}


def test_a_layer_across_a_boundary_blocks_that_cut() -> None:
    comp = _comp([10, 10, 10], extra={1: [_layer("wide", 10.0, 25.0)]})
    assert cut_points(comp) == {1}


def test_an_open_ended_layer_blocks_every_later_cut() -> None:
    comp = _comp([10, 10, 10], extra={0: [_layer("forever", 0.0, None)]})
    assert cut_points(comp) == set()


def test_scenes_group_to_the_target_and_a_short_tail_merges() -> None:
    spans = plan_scene_chunks(_comp([10, 10, 10, 10, 10]), target_seconds=20, min_seconds=12)
    assert [span.scene_ids for span in spans] == [("s0", "s1"), ("s2", "s3", "s4")]
    assert [span.index for span in spans] == [0, 1]
    assert spans[1].start_frame == 600 and spans[1].end_frame == 1500


def test_chunk_frames_add_up_to_the_whole_video_off_grid() -> None:
    comp = _comp([1.01, 1.02, 0.97])
    spans = plan_scene_chunks(comp, target_seconds=1.0, min_seconds=0.5)
    assert len(spans) == 3
    assert sum(span.frames for span in spans) == frame_count(comp.duration, 30)
    assert spans[0].end_frame == spans[1].start_frame


def test_slice_rebases_to_zero_and_drops_audio() -> None:
    comp = _comp([10, 10, 10])
    span = ChunkSpan(index=1, scene_ids=("s1", "s2"), start_frame=300, end_frame=900, fps=30)
    sliced = slice_composition(comp, span)
    assert sliced.duration == pytest.approx(20.0)
    assert [(s.id, s.start, s.end) for s in sliced.scenes] == [("s1", 0.0, 10.0), ("s2", 10.0, 20.0)]
    layer = sliced.scenes[1].layers[0]
    assert (layer.time.start, layer.time.end) == (10.0, 20.0)
    assert sliced.audio == [] and sliced.audio_mode == "external"


def test_the_same_scenes_slice_identically_at_any_offset() -> None:
    early = _comp([10, 10])
    late = _comp([5, 10, 10])
    # s0 of ``early`` and s2 of ``late`` (its own chunk) are both a 10 s scene with one
    # text layer; the layer ids differ by index, so compare times only.
    a = slice_composition(early, plan_scene_chunks(early, target_seconds=10, min_seconds=1)[0])
    b_spans = plan_scene_chunks(late, target_seconds=10, min_seconds=1)
    b = slice_composition(late, b_spans[1])
    assert [(s.start, s.end) for s in a.scenes] == [(s.start, s.end) for s in b.scenes]
    assert a.scenes[0].layers[0].time == b.scenes[0].layers[0].time


@pytest.mark.parametrize("frames", [1403, 2035])
def test_slice_duration_never_renders_an_extra_frame(frames: int) -> None:
    comp = _comp([frames / 30])
    span = ChunkSpan(index=0, scene_ids=("s0",), start_frame=0, end_frame=frames, fps=30)
    d = slice_composition(comp, span).duration
    assert math.ceil(round(d, 3) * 30) == frames
    assert round(d * 30) == frames
