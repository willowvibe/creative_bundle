"""Tests for the render graph builder."""

from __future__ import annotations

from types import SimpleNamespace

from video_editor_core.render_graph import AudioTrack, Layer, Rect
from video_editor_core.render_graph_builder import RenderGraphBuilder
from video_editor_core.timeline import EDL, compute_timeline_segments


def _layer(
    kind: str, name: str, start: float, end: float | None, text: str = ""
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        name=name,
        rect=SimpleNamespace(x=0, y=0, width=1920, height=200),
        timing={"start": start, "end": end},
        style={},
        source="",
        text=text,
    )


def _plan() -> SimpleNamespace:
    return SimpleNamespace(
        segments=[
            SimpleNamespace(
                timeline_start=0.0,
                layers=[
                    _layer("video", "base_video", 0.0, None),
                    _layer("text", "title", 1.0, 3.0, "Hi"),
                ],
            ),
            SimpleNamespace(
                timeline_start=5.0,
                layers=[_layer("text", "caption_line", 0.5, 1.5, "There")],
            ),
        ]
    )


def test_extend_graphics_plan_flattens_and_skips_base_video() -> None:
    graph = (
        RenderGraphBuilder.from_source("job", "src.mp4", width=1920, height=1080, duration=10.0)
        .extend_graphics_plan(_plan())
        .build()
    )
    names = [layer.name for layer in graph.overlays]
    assert names == ["title", "caption_line"]
    assert graph.overlays[0].timing.start == 1.0
    assert graph.overlays[0].timing.end == 3.0
    # segment 2 starts at 5.0s; its relative 0.5-1.5 becomes 5.5-6.5
    assert graph.overlays[1].timing.start == 5.5
    assert graph.overlays[1].timing.end == 6.5


def test_from_source_with_explicit_duration() -> None:
    graph = RenderGraphBuilder.from_source(
        "job", "src.mp4", width=1280, height=720, duration=12.5
    ).build()
    assert graph.duration == 12.5
    assert graph.cuts[0].source_start == 0.0
    assert graph.cuts[0].source_end == 12.5


def test_from_edl_uses_source_and_cuts() -> None:
    edl = EDL(
        job_id="job",
        source_path="orig.mp4",
        segments=compute_timeline_segments([(0.0, 2.0, "a"), (5.0, 7.0, "b")]),
    )
    graph = RenderGraphBuilder.from_edl("job", edl, width=1920, height=1080).build()
    assert graph.source == "orig.mp4"
    assert len(graph.cuts) == 2
    assert graph.duration == 4.0


def test_add_layer_and_audio() -> None:
    graph = (
        RenderGraphBuilder.from_source("job", "s.mp4", width=1920, height=1080, duration=10.0)
        .add_layer(Layer(kind="text", name="t", rect=Rect(x=0, y=0, width=10, height=10), text="x"))
        .add_audio(AudioTrack(kind="music", source="m.mp3", duck=True))
        .build()
    )
    assert graph.overlays[0].name == "t"
    assert graph.audio[0].kind == "music"
    assert graph.audio[0].duck is True
