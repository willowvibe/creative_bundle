"""Tests for the render graph model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from video_editor_core.render_graph import (
    AudioTrack,
    Cut,
    Layer,
    Rect,
    RenderGraph,
    Timing,
    edl_to_cuts,
)
from video_editor_core.timeline import EDL, Segment


def _cut(src_start: float, src_end: float, tl_start: float, tl_end: float) -> Cut:
    return Cut(
        source_start=src_start,
        source_end=src_end,
        timeline_start=tl_start,
        timeline_end=tl_end,
        label="keep speech",
    )


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "source.mp4",
        "cuts": [_cut(0.0, 5.0, 0.0, 5.0)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def test_minimal_graph_validates_and_reports_duration() -> None:
    graph = _graph()
    assert graph.duration == 5.0
    assert graph.ordered_cuts()[0].duration == 5.0


def test_contiguous_two_cut_graph_is_accepted() -> None:
    graph = _graph(cuts=[_cut(0.0, 2.0, 0.0, 2.0), _cut(10.0, 13.0, 2.0, 5.0)])
    assert [c.label for c in graph.ordered_cuts()] == ["keep speech", "keep speech"]
    assert graph.duration == 5.0


def test_overlapping_cuts_rejected() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        _graph(cuts=[_cut(0.0, 4.0, 0.0, 4.0), _cut(4.0, 6.0, 3.0, 5.0)])


def test_non_contiguous_cuts_rejected() -> None:
    with pytest.raises(ValidationError, match="not contiguous"):
        _graph(cuts=[_cut(0.0, 2.0, 0.0, 2.0), _cut(10.0, 13.0, 3.0, 6.0)])


def test_timeline_must_start_at_zero() -> None:
    with pytest.raises(ValidationError, match="start at 0"):
        _graph(cuts=[_cut(2.0, 5.0, 1.0, 4.0)])


def test_cut_geometry_validated() -> None:
    with pytest.raises(ValidationError, match="source_end must be > source_start"):
        _cut(5.0, 2.0, 0.0, 2.0)


def test_graph_requires_a_cut() -> None:
    with pytest.raises(ValidationError, match="at least one cut"):
        _graph(cuts=[])


def test_media_layer_requires_source() -> None:
    with pytest.raises(ValidationError, match="requires a source"):
        Layer(kind="logo", name="brand", rect=Rect(x=0, y=0, width=10, height=10))


def test_text_layer_needs_no_source() -> None:
    layer = Layer(
        kind="text",
        name="caption",
        rect=Rect(x=0, y=900, width=1920, height=200),
        text="hello",
    )
    assert layer.source == ""


def test_timing_ordering_validated() -> None:
    with pytest.raises(ValidationError, match="timing.end must be > start"):
        Timing(start=3.0, end=1.0)


def test_audio_track_requires_source_and_non_negative_start() -> None:
    with pytest.raises(ValidationError, match="requires a source"):
        AudioTrack(kind="music", source="")
    with pytest.raises(ValidationError, match=">= 0"):
        AudioTrack(kind="music", source="m.mp3", start=-1.0)


def test_edl_to_cuts_maps_segments() -> None:
    edl = EDL(
        job_id="job-1",
        source_path="source.mp4",
        segments=[
            Segment(
                id="seg_0000",
                source_start=1.0,
                source_end=3.0,
                timeline_start=0.0,
                timeline_end=2.0,
                label="a",
            ),
            Segment(
                id="seg_0001",
                source_start=8.0,
                source_end=11.0,
                timeline_start=2.0,
                timeline_end=5.0,
                label="b",
            ),
        ],
    )
    cuts = edl_to_cuts(edl)
    assert cuts[0].source_start == 1.0
    assert cuts[1].timeline_end == 5.0
    assert cuts[1].label == "b"


def test_edl_to_cuts_rejects_non_edl() -> None:
    with pytest.raises(TypeError):
        edl_to_cuts(object())
