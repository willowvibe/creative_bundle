"""Render a :class:`RenderGraph` with HyperFrames (primary) or FFmpeg (fallback).

HyperFrames is the renderer of record for the visual composition: it renders an
HTML composition project (``hyperframes render <dir>``). The graph's mixed audio
is then muxed onto the rendered video by FFmpeg in a single audio pass —
HyperFrames requires an ``<audio>`` element to point at a real audio file and
does not do speech-aware ducking, so the graph's audio buses stay on FFmpeg.

When HyperFrames is unavailable (or fails), the FFmpeg renderer does the whole
thing in one pass.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import run_ffmpeg

from video_editor_core.compile_ffmpeg import (
    compile_ffmpeg,
    compile_ffmpeg_mux_audio,
)
from video_editor_core.composition_router import choose_engine
from video_editor_core.hyperframes_cli import (
    HyperFramesLintError,
    RenderSegmentError,
    hyperframes_available,
)
from video_editor_core.hyperframes_cli import (
    render_project as hyperframes_render_project,
)
from video_editor_core.hyperframes_project import compile_hyperframes_project
from video_editor_core.render_graph import RenderGraph

logger = structlog.get_logger(__name__)

EngineName = str  # "ffmpeg" | "hyperframes" | "auto"

_FULL_CUT_EPSILON = 1e-3


class RenderError(RuntimeError):
    """Raised when a render produces no usable output."""


def _validate_output(output: Path) -> None:
    """Ensure the render produced a non-empty, probeable, non-zero video."""
    if not output.exists() or output.stat().st_size == 0:
        raise RenderError(f"render produced empty output: {output}")
    try:
        info = probe_media(output)
    except Exception as exc:  # noqa: BLE001
        raise RenderError(f"render output is not probeable: {output}") from exc
    if info.duration is None or info.duration <= 0:
        raise RenderError(f"render produced zero-duration output: {output}")


class Renderer(Protocol):
    """Renders a render graph to a single output file."""

    def render(self, graph: RenderGraph, output: Path) -> Path: ...


class FFmpegRenderer:
    """Single-pass renderer — the whole graph compiles to one FFmpeg command."""

    def render(self, graph: RenderGraph, output: Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        for argv in compile_ffmpeg(graph, output):
            run_ffmpeg(argv)
        _validate_output(output)
        logger.info("ffmpeg_render_complete", job_id=graph.job_id, output=str(output))
        return output


class HyperFramesRenderer:
    """HyperFrames HTML-composition renderer with an FFmpeg audio mux.

    Steps: (1) resolve the base video (the graph source, or an FFmpeg-produced
    cut when the graph trims), (2) write and render the HTML composition,
    (3) mix the graph's audio buses and mux them onto the video with ``-c:v
    copy``.
    """

    def __init__(self, cli_path: str = "hyperframes", timeout: int = 600):
        self.cli_path = cli_path
        self.timeout = timeout

    def _resolve_base_video(self, graph: RenderGraph, project_dir: Path) -> str:
        cuts = graph.ordered_cuts()
        single_full = (
            len(cuts) == 1
            and abs(cuts[0].source_start) < _FULL_CUT_EPSILON
            and abs(cuts[0].source_end - graph.duration) < _FULL_CUT_EPSILON
        )
        if single_full:
            return graph.source
        # The graph trims — HyperFrames can't concatenate source segments, so
        # materialise a video-only cut first.
        cut_dir = project_dir / "tmp"
        cut_dir.mkdir(parents=True, exist_ok=True)
        cut_path = cut_dir / "base.mp4"
        video_graph = graph.model_copy(update={"overlays": [], "audio": [], "has_audio": False})
        for argv in compile_ffmpeg(video_graph, cut_path):
            run_ffmpeg(argv)
        return str(cut_path)

    def render(self, graph: RenderGraph, output: Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        project_dir = output.parent / f"{output.stem}.hf"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)

        base_video = self._resolve_base_video(graph, project_dir)
        compile_hyperframes_project(graph, project_dir, base_video=base_video)

        composed = project_dir / "rendered.mp4"
        hyperframes_render_project(
            project_dir,
            composed,
            cli_path=self.cli_path,
            timeout=self.timeout,
            fps=graph.fps,
        )

        if graph.emits_audio:
            for argv in compile_ffmpeg_mux_audio(graph, composed, output):
                run_ffmpeg(argv)
        else:
            shutil.copyfile(composed, output)

        _validate_output(output)
        logger.info("hyperframes_render_complete", job_id=graph.job_id, output=str(output))
        return output


def select_renderer(engine: EngineName = "auto", cli_path: str = "hyperframes") -> Renderer:
    """Return the renderer for ``engine`` (``auto`` prefers HyperFrames)."""
    if engine == "ffmpeg":
        return FFmpegRenderer()
    if engine == "hyperframes":
        return HyperFramesRenderer(cli_path=cli_path)
    if hyperframes_available(cli_path):
        return HyperFramesRenderer(cli_path=cli_path)
    return FFmpegRenderer()


def render_graph(
    graph: RenderGraph,
    output: Path | str,
    *,
    engine: EngineName = "auto",
    cli_path: str = "hyperframes",
    timeout: int = 600,
) -> Path:
    """Render ``graph`` to ``output`` using the selected engine.

    ``engine="auto"`` (the default) defers to the composition router: it uses
    the fast single-pass FFmpeg renderer unless the graph explicitly requests
    HyperFrames or contains a feature only HyperFrames can render (shapes,
    shader transitions, motion-styled layers). See ``composition_router``.

    ``"ffmpeg"`` forces the single-pass renderer; ``"hyperframes"`` forces the
    HTML composition renderer (falling back to FFmpeg if it fails).
    """
    if engine == "auto":
        chosen = choose_engine(
            graph,
            requested="auto",
            hyperframes_available=hyperframes_available(cli_path),
        )
        if chosen == "hyperframes":
            try:
                renderer: Renderer = HyperFramesRenderer(cli_path=cli_path, timeout=timeout)
                return renderer.render(graph, Path(output))
            except (RenderSegmentError, HyperFramesLintError) as exc:
                logger.warning(
                    "hyperframes_render_failed_fallback_ffmpeg",
                    job_id=graph.job_id,
                    error=str(exc),
                )
        return FFmpegRenderer().render(graph, Path(output))

    renderer = select_renderer(engine, cli_path=cli_path)
    if isinstance(renderer, HyperFramesRenderer):
        renderer.timeout = timeout
    return renderer.render(graph, Path(output))
