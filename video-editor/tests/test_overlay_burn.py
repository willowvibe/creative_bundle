"""Captions burn: transparent overlay page -> PNG sequence -> FFmpeg overlay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from video_editor_core import overlay_burn
from video_editor_core import renderer as renderer_mod
from video_editor_core.hyperframes_cli import RenderSegmentError
from video_editor_core.hyperframes_project import compile_hyperframes_project
from video_editor_core.overlay_burn import (
    chunk_graph,
    overlay_command,
    plan_chunks,
    render_overlay_burn,
)
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph, Timing

TELUGU = "సెంటర్ ముందు క్యూ కడుతుంది."


def _graph(texts: list[str], seconds: float = 12.0, step: float = 2.0) -> RenderGraph:
    layers = [
        Layer(
            kind="text",
            name="caption_line",
            rect=Rect(x=100, y=900, width=1700, height=100),
            text=text,
            style={
                "highlight_color": "#FFC107",
                "words": [{"text": word, "start": 0.0, "end": 0.5} for word in text.split()],
            },
            timing=Timing(start=i * step, end=(i + 1) * step),
        )
        for i, text in enumerate(texts)
    ]
    return RenderGraph(
        job_id="cap",
        width=1920,
        height=1080,
        fps=30,
        source="mixed.mp4",
        cuts=[Cut(source_start=0.0, source_end=seconds, timeline_start=0.0, timeline_end=seconds)],
        overlays=layers,
    )


# --- the page has no base video ------------------------------------------------


def test_the_caption_project_has_no_base_video_and_is_transparent(tmp_path: Path) -> None:
    graph = _graph([TELUGU, "second line"])

    compile_hyperframes_project(graph, tmp_path / "p", overlay_only=True)
    html = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")

    assert "<video" not in html
    assert 'id="base_video"' not in html
    assert "mixed.mp4" not in html
    assert "html, body { margin: 0; padding: 0; background: transparent; }" in html
    assert "background: #000" not in html
    assert "క్యూ" in html  # the caption text itself is on the page
    assert 'id="caption_line_0"' in html


def test_the_normal_project_still_has_its_base_video(tmp_path: Path) -> None:
    compile_hyperframes_project(_graph(["a"]), tmp_path / "p")
    html = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")

    assert 'id="base_video"' in html
    assert "background: #000" in html


# --- chunking never cuts a caption line in two ------------------------------------


def test_chunks_cover_the_timeline_and_cut_no_layer() -> None:
    graph = _graph([f"line {i}" for i in range(15)], seconds=30.0)  # 2 s lines

    chunks = plan_chunks(graph, target_seconds=7.0)

    assert chunks[0][0] == 0 and chunks[-1][1] == 900
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:], strict=False))
    for _, end in chunks[:-1]:
        # every boundary is a line boundary (a multiple of 60 frames), never mid-line
        assert end % 60 == 0
    assert [end - start for start, end in chunks][:2] == [240, 240]


def test_a_layer_that_spans_everything_leaves_one_chunk() -> None:
    graph = _graph(["a"], seconds=20.0, step=20.0)

    assert plan_chunks(graph, target_seconds=5.0) == [(0, 600)]


def test_chunk_graph_shifts_layers_to_the_chunk_start() -> None:
    graph = _graph([f"line {i}" for i in range(6)], seconds=12.0)

    sub = chunk_graph(graph, 120, 240)  # 4 s .. 8 s

    assert sub.duration == pytest.approx(4.0)
    assert [(x.text, x.timing.start, x.timing.end) for x in sub.overlays] == [
        ("line 2", 0.0, 2.0),
        ("line 3", 2.0, 4.0),
    ]
    assert sub.overlays[0].style["words"] == graph.overlays[2].style["words"]
    assert not sub.has_audio


# --- the FFmpeg overlay --------------------------------------------------------------


def test_overlay_command_lays_frame_n_on_frame_n_and_copies_audio() -> None:
    cmd = overlay_command("mixed.mp4", "/w/frames", "out.mp4", fps=30, encoder="h264_nvenc")

    assert cmd[cmd.index("-i") + 1] == "mixed.mp4"
    # the PNG sequence is read at exactly the video's fps from frame 1
    assert cmd[cmd.index("-framerate") + 1] == "30"
    assert cmd[cmd.index("-start_number") + 1] == "1"
    assert "/w/frames/%06d.png" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=x=0:y=0" in graph and "shortest=1" in graph
    assert "out_color_matrix=bt709" in graph
    assert cmd[cmd.index("-c:v") + 1] == "h264_nvenc"
    assert cmd[cmd.index("-c:a") + 1] == "copy"
    assert cmd[cmd.index("-map") + 1] == "[v]" and "0:a?" in cmd
    assert cmd[-1] == "out.mp4"


def test_overlay_command_falls_back_to_libx264() -> None:
    cmd = overlay_command("v.mp4", "f", "o.mp4", fps=24, encoder="libx264")

    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "h264_nvenc" not in cmd
    assert cmd[cmd.index("-framerate") + 1] == "24"


def test_encoder_is_nvenc_only_with_a_gpu_and_the_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_burn, "_ffmpeg_has_nvenc", lambda: True)
    monkeypatch.setattr(overlay_burn, "_gpu_render_enabled", lambda: True)
    assert overlay_burn.pick_encoder() == "h264_nvenc"
    monkeypatch.setattr(overlay_burn, "_gpu_render_enabled", lambda: False)
    assert overlay_burn.pick_encoder() == "libx264"
    monkeypatch.setattr(overlay_burn, "_gpu_render_enabled", lambda: True)
    monkeypatch.setattr(overlay_burn, "_ffmpeg_has_nvenc", lambda: False)
    assert overlay_burn.pick_encoder() == "libx264"


class Recorder:
    """Stands in for HyperFrames (writes frames) and FFmpeg (records commands)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hf: list[dict[str, Any]] = []
        self.ffmpeg: list[list[str]] = []
        monkeypatch.setattr(overlay_burn, "render_project", self.render_project)
        monkeypatch.setattr(overlay_burn, "run_ffmpeg", self.run_ffmpeg)
        monkeypatch.setattr(overlay_burn, "pick_encoder", lambda: "libx264")

    def render_project(self, project: Path, out: Path, **kwargs: Any) -> Path:
        html = (Path(project) / "index.html").read_text(encoding="utf-8")
        self.hf.append({"html": html, "out": out, **kwargs})
        frames = round(float(html.split('data-duration="')[1].split('"')[0]) * kwargs["fps"])
        out.mkdir(parents=True)
        for number in range(1, frames + 1):
            (out / f"frame_{number:06d}.png").write_bytes(b"png")
        return out

    def run_ffmpeg(self, cmd: list[str], **kwargs: Any) -> None:
        self.ffmpeg.append(cmd)
        if cmd[-1].endswith("blank.png"):
            Path(cmd[-1]).write_bytes(b"blank")
        else:
            Path(cmd[-1]).write_bytes(b"video")


def test_render_overlay_burn_assembles_a_gapless_sequence_and_overlays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec = Recorder(monkeypatch)
    graph = _graph([TELUGU, "two", "three", "four"], seconds=12.0)  # 8 s of lines, then a gap
    out = tmp_path / "captioned.mp4"

    result = render_overlay_burn(graph, out, chunk_seconds=4.0, keep_work=True)

    assert result == out and out.read_bytes() == b"video"
    # two chunks hold lines; the last 4 s hold none and are never sent to Chrome
    assert len(rec.hf) == 2
    for call in rec.hf:
        assert call["output_format"] == "png-sequence"
        assert call["fps"] == 30
        assert "<video" not in call["html"]
    frames = sorted((tmp_path / "captioned.overlay" / "frames").glob("*.png"))
    assert [f.name for f in frames][:2] == ["000001.png", "000002.png"]
    assert len(frames) == 360 + overlay_burn.TAIL_PAD_FRAMES  # gapless, with the tail pad
    assert frames[-1].read_bytes() == b"blank"
    overlay = rec.ffmpeg[-1]
    assert overlay[overlay.index("-i") + 1] == "mixed.mp4"
    assert overlay[overlay.index("-c:a") + 1] == "copy"


def test_the_work_directory_is_removed_unless_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Recorder(monkeypatch)

    render_overlay_burn(_graph(["a", "b"], seconds=4.0), tmp_path / "c.mp4")

    assert not (tmp_path / "c.overlay").exists()


# --- no drawtext for Telugu ------------------------------------------------------------


def _hyperframes_fails(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    ffmpeg_runs: list[str] = []

    def boom(*args: Any, **kwargs: Any) -> Path:
        raise RenderSegmentError("HyperFrames render timed out after 6726s")

    class FakeFFmpeg:
        def render(self, graph: RenderGraph, output: Path) -> Path:
            ffmpeg_runs.append(graph.job_id)
            return output

    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": True)
    monkeypatch.setattr("video_editor_core.overlay_burn.render_project", boom)
    monkeypatch.setattr(renderer_mod, "hyperframes_render_project", boom)
    monkeypatch.setattr(renderer_mod, "FFmpegRenderer", FakeFFmpeg)
    monkeypatch.setattr(overlay_burn, "pick_encoder", lambda: "libx264")
    monkeypatch.setattr(
        overlay_burn, "run_ffmpeg", lambda cmd, **kw: Path(cmd[-1]).write_bytes(b"png")
    )
    return ffmpeg_runs


def test_a_failed_telugu_caption_render_raises_instead_of_drawing_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg_runs = _hyperframes_fails(monkeypatch)

    with pytest.raises(renderer_mod.ComplexScriptRenderError, match="complex-script shaping"):
        renderer_mod.burn_overlays(_graph([TELUGU]), tmp_path / "o.mp4")

    assert ffmpeg_runs == []  # FFmpeg's drawtext never ran


def test_a_failed_latin_caption_render_may_fall_back_to_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg_runs = _hyperframes_fails(monkeypatch)

    renderer_mod.burn_overlays(_graph(["plain english"]), tmp_path / "o.mp4")

    assert ffmpeg_runs == ["cap"]


def test_render_graph_does_not_fall_back_to_drawtext_for_telugu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg_runs = _hyperframes_fails(monkeypatch)
    graph = _graph([TELUGU]).model_copy(update={"render_preference": "hyperframes"})

    with pytest.raises(renderer_mod.ComplexScriptRenderError):
        renderer_mod.render_graph(graph, tmp_path / "o.mp4", engine="auto")
    assert ffmpeg_runs == []

    latin = _graph(["plain"]).model_copy(update={"render_preference": "hyperframes"})
    renderer_mod.render_graph(latin, tmp_path / "o2.mp4", engine="auto")
    assert ffmpeg_runs == ["cap"]


def test_burn_overlays_without_hyperframes_refuses_telugu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer_mod, "hyperframes_available", lambda cli="x": False)

    with pytest.raises(renderer_mod.ComplexScriptRenderError):
        renderer_mod.burn_overlays(_graph([TELUGU]), tmp_path / "o.mp4")
