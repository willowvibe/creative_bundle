"""Render a composition as cached scene chunks, then join and add audio once.

Pipeline: apply scene transitions → plan chunks at scene cuts → for each chunk,
slice (rebased to 0, video only) → compile its own HyperFrames project → hash
→ reuse the cached MP4 or render it → concat by stream copy → mix the audio
cues once → mux. Chunks render one at a time; HyperFrames already spreads each
one over its own Chrome workers. See docs/plans/2026-11-25-chunked-render-concept.md.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from video_editor_core.chunk_cache import (
    RenderCache,
    count_video_frames,
    project_hash,
    render_settings_key,
)
from video_editor_core.chunk_media import concat_videos, mix_audio_cues, mux_audio
from video_editor_core.compile_composition import compile_composition
from video_editor_core.composition import Composition
from video_editor_core.hyperframes_cli import render_project
from video_editor_core.scene_chunks import (
    DEFAULT_MIN_SECONDS,
    DEFAULT_TARGET_SECONDS,
    frame_count,
    plan_scene_chunks,
    slice_composition,
)
from video_editor_core.scene_transitions import apply_scene_transitions

logger = structlog.get_logger(__name__)

#: Flipped to True by T6 once a measured run matches the single-pass render.
CHUNKED_DEFAULT = True
_ON = {"1", "on", "true", "yes"}
_OFF = {"0", "off", "false", "no"}


def chunked_render_enabled() -> bool:
    raw = os.environ.get("VE_RENDER_CHUNKS", "").strip().lower()
    if raw in _ON:
        return True
    if raw in _OFF:
        return False
    return CHUNKED_DEFAULT


class ChunkedRenderError(RuntimeError):
    pass


@dataclass
class ChunkResult:
    index: int
    scene_ids: tuple[str, ...]
    key: str
    cached: bool
    frames: int
    seconds: float


@dataclass
class ChunkedRenderReport:
    output: Path
    total_frames: int
    chunks: list[ChunkResult] = field(default_factory=list)

    @property
    def rendered(self) -> int:
        return sum(1 for chunk in self.chunks if not chunk.cached)

    @property
    def reused(self) -> int:
        return sum(1 for chunk in self.chunks if chunk.cached)

    def to_json(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "total_frames": self.total_frames,
            "rendered": self.rendered,
            "reused": self.reused,
            "chunks": [{**asdict(chunk), "scene_ids": list(chunk.scene_ids)} for chunk in self.chunks],
        }


def _render_chunk(project: Path, output: Path, frames: int, cli_path: str, timeout: int) -> None:
    """One HyperFrames render (a seam for tests; ``frames`` is what the caller checks)."""
    del frames
    render_project(project, output, cli_path=cli_path, timeout=timeout)


def render_composition_chunked(
    composition: Composition,
    output_path: Path,
    work_dir: Path,
    *,
    cache_dir: Path,
    cli_path: str = "hyperframes",
    timeout: int = 1800,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    min_seconds: float = DEFAULT_MIN_SECONDS,
) -> ChunkedRenderReport:
    output_path = Path(output_path)
    work_dir = Path(work_dir)
    composition = apply_scene_transitions(composition)
    fps = composition.canvas.fps
    spans = plan_scene_chunks(composition, target_seconds=target_seconds, min_seconds=min_seconds)
    cache = RenderCache(cache_dir)
    settings = render_settings_key(fps, cli_path)
    chunk_root = work_dir / "chunks"
    shutil.rmtree(chunk_root, ignore_errors=True)
    report = ChunkedRenderReport(output=output_path, total_frames=frame_count(composition.duration, fps))
    parts: list[Path] = []
    for span in spans:
        started = time.monotonic()
        project = chunk_root / f"chunk_{span.index:03d}"
        compile_composition(slice_composition(composition, span), project)
        key = project_hash(project, settings)
        path = cache.lookup(key, span.frames)
        cached = path is not None
        if path is None:
            rendered = project / "rendered.mp4"
            _render_chunk(project, rendered, span.frames, cli_path, timeout)
            got = count_video_frames(rendered)
            if got != span.frames:
                raise ChunkedRenderError(
                    f"chunk {span.index} ({', '.join(span.scene_ids)}) rendered {got} frames, "
                    f"expected {span.frames}"
                )
            path = cache.store(key, rendered, frames=span.frames, scene_ids=span.scene_ids)
        seconds = round(time.monotonic() - started, 2)
        report.chunks.append(
            ChunkResult(span.index, span.scene_ids, key, cached, span.frames, seconds)
        )
        logger.info(
            "chunked_render_chunk", index=span.index, scenes=list(span.scene_ids),
            cached=cached, frames=span.frames, seconds=seconds,
        )
        parts.append(path)

    joined = concat_videos(parts, work_dir / "chunks_joined.mp4")
    got = count_video_frames(joined)
    if got != report.total_frames:
        raise ChunkedRenderError(f"joined video has {got} frames, expected {report.total_frames}")
    if composition.audio and composition.audio_mode == "embed":
        audio = mix_audio_cues(composition.audio, composition.duration, work_dir / "chunks_audio.wav")
        mux_audio(joined, audio, output_path)
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(joined, output_path)
    cache.prune({chunk.key for chunk in report.chunks})
    (work_dir / "chunked_render.json").write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")
    logger.info("chunked_render_done", rendered=report.rendered, reused=report.reused,
                frames=report.total_frames)
    return report
