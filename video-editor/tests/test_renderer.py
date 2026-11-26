"""Tests for the renderer selection + HyperFrames/FFmpeg render flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core import renderer as renderer_mod
from video_editor_core.render_graph import Cut, RenderGraph
from video_editor_core.renderer import (
    FFmpegRenderer,
    HyperFramesRenderer,
    select_renderer,
)


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "source.mp4",
        "cuts": [
            Cut(
                source_start=0.0,
                source_end=5.0,
                timeline_start=0.0,
                timeline_end=5.0,
            )
        ],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run_ffmpeg(cmd: list[str], **kwargs: object) -> None:
        calls.append(cmd)

    monkeypatch.setattr(renderer_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(renderer_mod, "_validate_output", lambda p: None)
    return calls


def test_ffmpeg_renderer_runs_one_pass(tmp_path: Path, recorded: list[list[str]]) -> None:
    out = tmp_path / "out.mp4"
    result = FFmpegRenderer().render(_graph(), out)
    assert result == out
    assert len(recorded) == 1
    assert recorded[0][0] == "ffmpeg"
    assert "-filter_complex" in recorded[0]


def test_hyperframes_renderer_compose_then_mux(
    tmp_path: Path, recorded: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    hf_calls: list[dict[str, object]] = []

    def fake_hf(project_dir: Path, out: Path, **kwargs: object) -> Path:
        hf_calls.append({"project": project_dir, "out": out, **kwargs})
        out.write_bytes(b"composed")
        return out

    monkeypatch.setattr(renderer_mod, "hyperframes_render_project", fake_hf)
    graph = _graph()

    out = tmp_path / "out.mp4"
    result = HyperFramesRenderer(timeout=42).render(graph, out)

    assert result == out
    # single-cut full graph: no FFmpeg cut pass, only the audio mux
    assert len(recorded) == 1
    mux = recorded[-1]
    assert mux[mux.index("-c:v") + 1] == "copy"
    assert len(hf_calls) == 1
    assert hf_calls[0]["timeout"] == 42
    # the HTML project was written
    assert (tmp_path / "out.hf" / "index.html").exists()


def test_hyperframes_renderer_no_audio_copies_composed(
    tmp_path: Path, recorded: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_hf(project_dir: Path, out: Path, **kwargs: object) -> Path:
        out.write_bytes(b"composed")
        return out

    monkeypatch.setattr(renderer_mod, "hyperframes_render_project", fake_hf)
    graph = _graph(has_audio=False)
    out = tmp_path / "out.mp4"
    HyperFramesRenderer().render(graph, out)
    # no ffmpeg at all; the rendered composition is copied straight out
    assert recorded == []
    assert out.exists()


def test_select_renderer_explicit() -> None:
    assert isinstance(select_renderer("ffmpeg"), FFmpegRenderer)
    assert isinstance(select_renderer("hyperframes"), HyperFramesRenderer)


def test_select_renderer_auto_prefers_hyperframes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": True)
    assert isinstance(select_renderer("auto"), HyperFramesRenderer)
    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": False)
    assert isinstance(select_renderer("auto"), FFmpegRenderer)


def test_render_graph_auto_routes_simple_graph_to_ffmpeg(
    tmp_path: Path, recorded: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # HyperFrames is available, but a simple graph must stay on the fast path.
    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": True)
    out = tmp_path / "out.mp4"
    renderer_mod.render_graph(_graph(), out, engine="auto")
    assert len(recorded) == 1  # one FFmpeg pass


def test_hyperframes_timeout_scales_with_video_length() -> None:
    assert renderer_mod.hyperframes_timeout(30.0) == 600
    # A 560 s explainer's caption pass needs ~1 h and hit the old fixed 600 s.
    assert renderer_mod.hyperframes_timeout(560.5) > 6000


def test_render_graph_derives_the_hyperframes_timeout_from_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    class FakeRenderer:
        def __init__(self, cli_path: str = "hyperframes", timeout: int = 0) -> None:
            seen.append(timeout)

        def render(self, graph: RenderGraph, output: Path) -> Path:
            return output

    monkeypatch.setattr(renderer_mod, "HyperFramesRenderer", FakeRenderer)
    monkeypatch.setattr(renderer_mod, "choose_engine", lambda *a, **k: "hyperframes")
    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": True)
    long = _graph(
        cuts=[Cut(source_start=0.0, source_end=560.0, timeline_start=0.0, timeline_end=560.0)]
    )
    renderer_mod.render_graph(long, tmp_path / "a.mp4", engine="auto")
    renderer_mod.render_graph(long, tmp_path / "b.mp4", engine="auto", timeout=77)
    assert seen == [renderer_mod.hyperframes_timeout(560.0), 77]
