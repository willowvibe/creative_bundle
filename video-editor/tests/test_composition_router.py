"""Tests for the composition router."""

from __future__ import annotations

from video_editor_core.composition_router import choose_engine, graph_needs_hyperframes
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph
from video_editor_ffmpeg.render_options import RenderOptions


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "s.mp4",
        "has_audio": False,
        "cuts": [Cut(source_start=0, source_end=5, timeline_start=0, timeline_end=5)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def _text_layer(**style: object) -> Layer:
    return Layer(
        kind="text",
        name="t",
        rect=Rect(width=100, height=40),
        text="hi",
        style=dict(style),
    )


def test_simple_graph_routes_to_ffmpeg() -> None:
    assert graph_needs_hyperframes(_graph()) is False
    assert choose_engine(_graph(), hyperframes_available=True) == "ffmpeg"


def test_shape_layer_needs_hyperframes() -> None:
    graph = _graph(overlays=[Layer(kind="shape", name="box", rect=Rect(width=10, height=10))])
    assert graph_needs_hyperframes(graph) is True
    assert choose_engine(graph, hyperframes_available=True) == "hyperframes"


def test_motion_style_needs_hyperframes() -> None:
    graph = _graph(overlays=[_text_layer(motion=True)])
    assert choose_engine(graph, hyperframes_available=True) == "hyperframes"


def test_hyperframes_style_engine_needs_hyperframes() -> None:
    graph = _graph(overlays=[_text_layer(engine="hyperframes")])
    assert choose_engine(graph, hyperframes_available=True) == "hyperframes"


def test_transition_needs_hyperframes() -> None:
    graph = _graph(options=RenderOptions(transition="fade"))
    assert choose_engine(graph, hyperframes_available=True) == "hyperframes"


def test_graph_preference_hyperframes() -> None:
    graph = _graph(render_preference="hyperframes")
    assert choose_engine(graph, hyperframes_available=True) == "hyperframes"


def test_graph_preference_ffmpeg_beats_heuristic() -> None:
    graph = _graph(
        render_preference="ffmpeg",
        overlays=[Layer(kind="shape", name="box", rect=Rect(width=10, height=10))],
    )
    assert choose_engine(graph, requested="auto", hyperframes_available=True) == "ffmpeg"


def test_requested_engine_wins() -> None:
    assert choose_engine(_graph(), requested="hyperframes", hyperframes_available=True) == (
        "hyperframes"
    )
    assert (
        choose_engine(
            _graph(render_preference="hyperframes"), requested="ffmpeg", hyperframes_available=True
        )
        == "ffmpeg"
    )


def test_unavailable_hyperframes_falls_back_to_ffmpeg() -> None:
    graph = _graph(
        render_preference="hyperframes",
        overlays=[Layer(kind="shape", name="box", rect=Rect(width=10, height=10))],
    )
    assert choose_engine(graph, hyperframes_available=False) == "ffmpeg"
