"""Content-addressed cache for caption overlay chunks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_core import chunk_cache, overlay_burn
from video_editor_core.chunk_cache import OverlayCache
from video_editor_core.overlay_burn import TAIL_PAD_FRAMES, render_overlay_frames
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph, Timing

CHUNK_SECONDS = 4.0
TEXTS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


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


class RenderRecorder:
    """Stands in for HyperFrames and FFmpeg, counting real render requests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        monkeypatch.setattr(chunk_cache, "_hyperframes_version", lambda cli: "0.0.0-test")
        monkeypatch.setattr(overlay_burn, "render_project", self.render_project)
        monkeypatch.setattr(overlay_burn, "run_ffmpeg", self.run_ffmpeg)

    def render_project(self, project: Path, out: Path, **kwargs: Any) -> Path:
        html = (Path(project) / "index.html").read_text(encoding="utf-8")
        self.calls.append({"project": str(project), "out": Path(out), "html": html, **kwargs})
        frames = round(float(html.split('data-duration="')[1].split('"')[0]) * kwargs["fps"])
        out.mkdir(parents=True)
        for number in range(1, frames + 1):
            (out / f"frame_{number:06d}.png").write_bytes(f"png-{number}".encode())
        return out

    def run_ffmpeg(self, cmd: list[str], **kwargs: Any) -> None:
        Path(cmd[-1]).write_bytes(b"blank")


def _render(
    graph: RenderGraph, work_dir: Path, cache_dir: Path, recorder: RenderRecorder
) -> tuple[Path, int]:
    recorder.calls.clear()
    return render_overlay_frames(
        graph,
        work_dir,
        cli_path="hyperframes",
        timeout=60,
        chunk_seconds=CHUNK_SECONDS,
        cache_dir=cache_dir,
    )


def _names(frames_dir: Path) -> list[str]:
    return [path.name for path in sorted(frames_dir.glob("*.png"))]


def _bytes(frames_dir: Path) -> list[bytes]:
    return [path.read_bytes() for path in sorted(frames_dir.glob("*.png"))]


def test_second_identical_run_reuses_every_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VE_OVERLAY_CACHE", raising=False)
    recorder = RenderRecorder(monkeypatch)
    graph = _graph(TEXTS)
    cache = tmp_path / "overlay_cache"

    first, total = _render(graph, tmp_path / "first", cache, recorder)
    assert len(recorder.calls) == 3  # every non-blank chunk rendered once
    first_names = _names(first)
    first_bytes = _bytes(first)
    assert len(first_names) == total + TAIL_PAD_FRAMES
    assert (cache / "manifest.json").exists()

    second, _ = _render(graph, tmp_path / "second", cache, recorder)
    assert recorder.calls == []  # all three chunks came from the cache
    assert _names(second) == first_names
    assert _bytes(second) == first_bytes


def test_changing_one_caption_rerenders_only_its_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VE_OVERLAY_CACHE", raising=False)
    recorder = RenderRecorder(monkeypatch)
    cache = tmp_path / "overlay_cache"
    _render(_graph(TEXTS), tmp_path / "first", cache, recorder)

    changed = list(TEXTS)
    changed[2] = "charlie changed"
    _render(_graph(changed), tmp_path / "second", cache, recorder)

    assert len(recorder.calls) == 1
    assert "changed" in recorder.calls[0]["html"]


def test_a_stale_entry_with_the_wrong_frame_count_is_rerendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VE_OVERLAY_CACHE", raising=False)
    recorder = RenderRecorder(monkeypatch)
    cache = tmp_path / "overlay_cache"
    graph = _graph(TEXTS)
    _render(graph, tmp_path / "first", cache, recorder)

    entries = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))["entries"]
    victim_key, victim_entry = sorted(entries.items())[0]
    victim = cache / victim_key
    sorted(victim.glob("*.png"))[-1].unlink()
    assert len(list(victim.glob("*.png"))) == victim_entry["frames"] - 1

    _render(graph, tmp_path / "second", cache, recorder)

    assert len(recorder.calls) == 1
    assert len(list(victim.glob("*.png"))) == victim_entry["frames"]


def test_prune_removes_unused_keys_and_stray_directories(tmp_path: Path) -> None:
    cache = OverlayCache(tmp_path / "cache")
    for key in ("keep", "drop"):
        source = tmp_path / f"source_{key}"
        source.mkdir()
        for number in range(1, 4):
            (source / f"{number:06d}.png").write_bytes(b"png")
        cache.store(key, source, frames=3, start=0, end=3, fps=30)
    (cache.root / "stray").mkdir()

    removed = cache.prune({"keep"})

    assert sorted(removed) == ["drop", "stray"]
    assert sorted(path.name for path in cache.root.iterdir() if path.is_dir()) == ["keep"]
    entries = json.loads((cache.root / "manifest.json").read_text(encoding="utf-8"))["entries"]
    assert set(entries) == {"keep"}


def test_the_environment_switch_disables_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VE_OVERLAY_CACHE", "0")
    recorder = RenderRecorder(monkeypatch)
    graph = _graph(TEXTS)
    cache = tmp_path / "overlay_cache"

    _render(graph, tmp_path / "first", cache, recorder)
    assert len(recorder.calls) == 3
    _render(graph, tmp_path / "second", cache, recorder)
    assert len(recorder.calls) == 3  # no chunk was reused
    assert not (cache / "manifest.json").exists()


def test_the_key_ignores_absolute_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VE_OVERLAY_CACHE", raising=False)
    recorder = RenderRecorder(monkeypatch)
    graph = _graph(["alpha", "bravo", "alpha", "bravo", "alpha", "bravo"])

    _render(graph, tmp_path / "first", tmp_path / "overlay_cache", recorder)

    assert len(recorder.calls) == 1  # chunks 1 and 2 repeat chunk 0 at a later time


def test_blank_chunks_are_not_rendered_or_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VE_OVERLAY_CACHE", raising=False)
    recorder = RenderRecorder(monkeypatch)
    cache = tmp_path / "overlay_cache"

    frames, total = _render(_graph(["alpha", "bravo"]), tmp_path / "first", cache, recorder)

    assert len(recorder.calls) == 1
    assert len(_names(frames)) == total + TAIL_PAD_FRAMES
    entries = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 1
