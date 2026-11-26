"""Tests for the chunked scene render with a per-chunk cache."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core import chunk_cache, chunked_render
from video_editor_core.chunk_cache import count_video_frames
from video_editor_core.chunked_render import chunked_render_enabled, render_composition_chunked
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

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


@pytest.fixture
def renders(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace the HyperFrames call with a clip of the requested length; record each call."""
    calls: list[Path] = []
    monkeypatch.setenv("VE_RENDER_GPU", "0")
    monkeypatch.setattr(chunk_cache, "_hyperframes_version", lambda cli: "test")

    def fake(project: Path, output: Path, frames: int, cli_path: str, timeout: int) -> None:
        calls.append(project)
        _clip(output, frames)

    monkeypatch.setattr(chunked_render, "_render_chunk", fake)
    return calls


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)],
        check=True,
    )
    return path


def _comp(tmp_path: Path, texts: list[str]) -> Composition:
    scenes = []
    for i, text in enumerate(texts):
        layer = CompositionLayer(
            id=f"{text}_layer",
            kind="text",
            rect=Rect(x=0, y=0, width=200, height=100),
            time=LayerTime(start=float(i), end=float(i + 1)),
            content=LayerContent(text=text),
        )
        scenes.append(CompositionScene(id=text, start=float(i), end=float(i + 1), layers=[layer]))
    tone = _tone(tmp_path / "narration.wav", float(len(texts)))
    return Composition(
        job_id="job",
        canvas=Canvas(width=320, height=180, fps=30),
        duration=float(len(texts)),
        scenes=scenes,
        audio=[AudioCue(id="narration", source=str(tone), start=0.0)],
    )


def _run(tmp_path: Path, comp: Composition):
    return render_composition_chunked(
        comp, tmp_path / "out.mp4", tmp_path / "work", cache_dir=tmp_path / "cache",
        target_seconds=1.0, min_seconds=0.5,
    )


def test_first_render_renders_every_chunk_and_muxes_audio(tmp_path: Path, renders: list[Path]) -> None:
    report = _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    assert len(renders) == 3 and report.rendered == 3 and report.reused == 0
    assert count_video_frames(tmp_path / "out.mp4") == 90
    data = json.loads((tmp_path / "work" / "chunked_render.json").read_text())
    assert [c["scene_ids"] for c in data["chunks"]] == [["a"], ["b"], ["c"]]


def test_unchanged_rerun_renders_nothing(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    assert renders == [] and report.reused == 3


def test_one_changed_scene_renders_one_chunk(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["a", "B", "c"]))
    assert len(renders) == 1 and report.rendered == 1 and report.reused == 2


def test_inserting_a_scene_keeps_later_chunks_cached(tmp_path: Path, renders: list[Path]) -> None:
    _run(tmp_path, _comp(tmp_path, ["a", "b", "c"]))
    renders.clear()
    report = _run(tmp_path, _comp(tmp_path, ["intro", "a", "b", "c"]))
    assert len(renders) == 1 and report.reused == 3
    assert count_video_frames(tmp_path / "out.mp4") == 120


def test_switch_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_RENDER_CHUNKS", "1")
    assert chunked_render_enabled() is True
    monkeypatch.setenv("VE_RENDER_CHUNKS", "off")
    assert chunked_render_enabled() is False
    monkeypatch.delenv("VE_RENDER_CHUNKS")
    assert chunked_render_enabled() is chunked_render.CHUNKED_DEFAULT
