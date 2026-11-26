"""Burn HTML overlays onto a finished video without putting the video in Chrome.

A HyperFrames page that embeds the whole video (``<video id="base_video">``)
makes Chrome decode it frame by frame on the CPU: a 600 MB, 560 s explainer took
112 min for its captions and timed out. The overlays do not need the picture, so
this module renders them on their own:

1. the overlay layers become a **transparent** HyperFrames page with no base
   video (:func:`~video_editor_core.hyperframes_project.compile_hyperframes_project`
   with ``overlay_only=True``);
2. HyperFrames renders it to an RGBA PNG sequence (``--format png-sequence``,
   NVIDIA profile applied by :func:`~video_editor_core.hyperframes_cli.render_project`);
3. FFmpeg lays frame *N* of the sequence on frame *N* of the video and encodes
   (``h264_nvenc`` when available, else ``libx264``), copying the audio.

Chrome's cost per frame grows with the number of clips on the page (305 caption
lines: 300 frames in 170 s; the six lines of the same window: 11 s), so the
timeline is rendered in chunks, each page holding only its own layers. A chunk
boundary never cuts a layer in two, so each layer's entrance and karaoke
animation start exactly where they would on one page.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from functools import cache
from pathlib import Path

import structlog
from video_editor_ffmpeg.run import FFmpegError, run_ffmpeg

from video_editor_core.chunk_cache import (
    OverlayCache,
    link_or_copy,
    overlay_render_settings_key,
    project_hash,
)
from video_editor_core.composition import quantise
from video_editor_core.hyperframes_cli import (
    RenderSegmentError,
    _gpu_render_enabled,
    render_project,
)
from video_editor_core.hyperframes_project import compile_hyperframes_project
from video_editor_core.render_graph import Cut, Layer, RenderGraph, Timing

logger = structlog.get_logger(__name__)

#: Target length of one overlay page. Longer pages cost more per frame (every clip
#: is on the page); shorter ones pay Chrome's start-up (~5 s) more often.
OVERLAY_CHUNK_SECONDS = 30.0
#: Blank frames after the last one, so the sequence never ends before the video.
TAIL_PAD_FRAMES = 2

_CACHE_OFF = {"0", "off", "false", "no"}

ProgressFn = Callable[[int, int], None]


def overlay_cache_enabled() -> bool:
    """Whether ``VE_OVERLAY_CACHE`` permits reusing rendered caption chunks."""
    return os.environ.get("VE_OVERLAY_CACHE", "").strip().lower() not in _CACHE_OFF


class OverlayBurnError(RuntimeError):
    """The overlay frames or the FFmpeg composite could not be produced."""


def _layer_frames(layer: Layer, fps: int, total: int) -> tuple[int, int]:
    """The frame range ``[start, end)`` a layer is on screen, as the page quantises it."""
    start = round(quantise(layer.timing.start, fps) * fps)
    end = total if layer.timing.end is None else round(quantise(layer.timing.end, fps) * fps)
    start = min(max(0, start), total)
    return start, min(total, max(end, start + 1))


def plan_chunks(graph: RenderGraph, target_seconds: float | None = None) -> list[tuple[int, int]]:
    """Frame ranges ``[start, end)`` that cover the timeline and cut no layer in two.

    Each range is about ``target_seconds`` long: its end is the first frame at or
    after the target where no layer is mid-way (the next caption line starts as
    the previous one ends). Layers that span everything leave one big chunk.
    """
    fps = graph.fps
    if target_seconds is None:
        target_seconds = OVERLAY_CHUNK_SECONDS
    total = round(quantise(graph.duration, fps) * fps)
    spans = [_layer_frames(layer, fps, total) for layer in graph.overlays]
    candidates = sorted({edge for span in spans for edge in span if 0 < edge < total})
    target = max(1, round(target_seconds * fps))

    def straddled(frame: int) -> bool:
        return any(start < frame < end for start, end in spans)

    chunks: list[tuple[int, int]] = []
    cursor = 0
    while cursor < total:
        end = total
        for edge in candidates:
            if edge >= cursor + target and not straddled(edge):
                end = edge
                break
        chunks.append((cursor, end))
        cursor = end
    return chunks


def chunk_graph(graph: RenderGraph, start: int, end: int) -> RenderGraph:
    """The sub-graph for frames ``[start, end)``: its overlays, shifted to start at 0."""
    fps = graph.fps
    total = round(quantise(graph.duration, fps) * fps)
    offset = start / fps
    layers: list[Layer] = []
    for layer in graph.overlays:
        first, last = _layer_frames(layer, fps, total)
        if last <= start or first >= end:
            continue
        timing = Timing(
            start=max(0.0, layer.timing.start - offset),
            end=None if layer.timing.end is None else max(0.0, layer.timing.end - offset),
        )
        layers.append(layer.model_copy(update={"timing": timing}))
    length = (end - start) / fps
    return graph.model_copy(
        update={
            "cuts": [
                Cut(source_start=0.0, source_end=length, timeline_start=0.0, timeline_end=length)
            ],
            "overlays": layers,
            "audio": [],
            "has_audio": False,
        }
    )


def _blank_frame(path: Path, width: int, height: int) -> None:
    run_ffmpeg(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black@0.0:s={width}x{height}:r=1,format=rgba",
            "-frames:v", "1", str(path),
        ],
        timeout=60,
    )  # fmt: skip


def frame_name(number: int) -> str:
    """Global, 1-based frame file name in the assembled sequence."""
    return f"{number:06d}.png"


def render_overlay_frames(
    graph: RenderGraph,
    work_dir: Path,
    *,
    cli_path: str = "hyperframes",
    timeout: int | None = None,
    chunk_seconds: float | None = None,
    progress: ProgressFn | None = None,
    cache_dir: Path | None = None,
) -> tuple[Path, int]:
    """Render ``graph``'s overlays to ``work_dir/frames/%06d.png``; return (dir, frame count).

    Chunks with no overlay are not rendered: their frames are hard links to one
    blank PNG, so the sequence stays gapless and frame *N* is timeline frame *N*.

    When ``cache_dir`` is given (and ``VE_OVERLAY_CACHE`` is not off), each
    non-blank chunk is keyed by its compiled project and reused from the cache
    instead of being rendered again.
    """
    from video_editor_core.renderer import hyperframes_timeout

    fps = graph.fps
    total = round(quantise(graph.duration, fps) * fps)
    frames_dir = work_dir / "frames"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    frames_dir.mkdir(parents=True)
    blank = work_dir / "blank.png"
    _blank_frame(blank, graph.width, graph.height)

    cache = None
    settings = ""
    if cache_dir is not None and overlay_cache_enabled():
        cache = OverlayCache(cache_dir)
        settings = overlay_render_settings_key(graph.width, graph.height, fps, cli_path)
    used_keys: set[str] = set()
    rendered = 0
    reused = 0

    chunks = plan_chunks(graph, chunk_seconds)
    logger.info("overlay_burn_chunks", job_id=graph.job_id, chunks=len(chunks), frames=total)
    written = 0
    for index, (start, end) in enumerate(chunks):
        sub = chunk_graph(graph, start, end)
        expected = end - start
        cached = False
        key: str | None = None
        if sub.overlays:
            project = work_dir / f"chunk_{index:03d}.hf"
            compile_hyperframes_project(sub, project, overlay_only=True)
            out_dir = work_dir / f"chunk_{index:03d}_png"
            if cache is not None:
                key = project_hash(project, settings)
                used_keys.add(key)
                hit = cache.lookup(key, expected)
                if hit is not None:
                    cached = True
                    reused += 1
                    for offset, png in enumerate(sorted(hit.glob("*.png"))):
                        link_or_copy(png, frames_dir / frame_name(start + offset + 1))
                    shutil.rmtree(project, ignore_errors=True)
            if not cached:
                budget = timeout if timeout is not None else hyperframes_timeout(sub.duration)
                render_project(
                    project,
                    out_dir,
                    cli_path=cli_path,
                    timeout=budget,
                    fps=fps,
                    output_format="png-sequence",
                )
                produced = sorted(out_dir.glob("*.png"))
                if len(produced) < expected:
                    raise RenderSegmentError(
                        f"overlay chunk {index} rendered {len(produced)} frames, expected {expected}"
                    )
                if cache is not None and key is not None:
                    cache.store(key, out_dir, frames=expected, start=start, end=end, fps=fps)
                for offset, png in enumerate(produced[:expected]):
                    os.replace(png, frames_dir / frame_name(start + offset + 1))
                shutil.rmtree(out_dir, ignore_errors=True)
                shutil.rmtree(project, ignore_errors=True)
                rendered += 1
        else:
            for number in range(start + 1, end + 1):
                os.link(blank, frames_dir / frame_name(number))
        logger.info(
            "overlay_burn_chunk",
            job_id=graph.job_id,
            index=index,
            start=start,
            end=end,
            frames=expected,
            cached=cached,
            key=key,
            blank=not sub.overlays,
        )
        written = end
        if progress is not None:
            progress(written, total)
    for number in range(total + 1, total + TAIL_PAD_FRAMES + 1):
        os.link(blank, frames_dir / frame_name(number))
    if cache is not None:
        removed = cache.prune(used_keys)
        logger.info("overlay_burn_cache_prune", job_id=graph.job_id, removed=len(removed))
    logger.info(
        "overlay_burn_summary",
        job_id=graph.job_id,
        rendered=rendered,
        reused=reused,
    )
    return frames_dir, total


@cache
def _ffmpeg_has_nvenc() -> bool:
    try:
        done = run_ffmpeg(["ffmpeg", "-hide_banner", "-encoders"], timeout=30, check=False)
    except (OSError, FFmpegError):
        return False
    return "h264_nvenc" in (done.stdout or "")


def pick_encoder() -> str:
    """``h264_nvenc`` when FFmpeg has it and an NVIDIA GPU is usable, else ``libx264``."""
    return "h264_nvenc" if _ffmpeg_has_nvenc() and _gpu_render_enabled() else "libx264"


def _encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
            "-rc", "vbr", "-cq", "18", "-b:v", "0", "-profile:v", "high",
        ]  # fmt: skip
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-profile:v", "high"]


def overlay_command(
    base_video: Path | str,
    frames_dir: Path | str,
    output: Path | str,
    *,
    fps: int,
    encoder: str,
) -> list[str]:
    """The FFmpeg command that lays the PNG sequence over ``base_video``.

    Frame *N* of the sequence (read at exactly ``fps``) lands on frame *N* of
    the video; the RGBA frames are converted with the video's BT.709 limited
    range matrix so colours match what Chrome drew. Audio is stream-copied.
    """
    graph = (
        "[1:v]format=rgba,scale=out_color_matrix=bt709:out_range=tv,format=yuva420p[ov];"
        "[0:v][ov]overlay=x=0:y=0:eof_action=pass:shortest=1:format=yuv420[v]"
    )
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(base_video),
        "-framerate", str(fps), "-start_number", "1",
        "-i", str(Path(frames_dir) / "%06d.png"),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        *_encoder_args(encoder),
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-r", str(fps),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output),
    ]  # fmt: skip


def composite_overlay(
    base_video: Path,
    frames_dir: Path,
    output: Path,
    *,
    fps: int,
    duration: float,
) -> Path:
    """Run :func:`overlay_command`; fall back to ``libx264`` if NVENC fails."""
    timeout = max(900.0, duration * 4.0)
    encoder = pick_encoder()
    try:
        run_ffmpeg(
            overlay_command(base_video, frames_dir, output, fps=fps, encoder=encoder),
            timeout=timeout,
        )
    except FFmpegError as exc:
        if encoder == "libx264":
            raise OverlayBurnError(f"overlay composite failed: {exc}") from exc
        logger.warning("overlay_burn_nvenc_failed_using_libx264", error=str(exc)[-400:])
        run_ffmpeg(
            overlay_command(base_video, frames_dir, output, fps=fps, encoder="libx264"),
            timeout=timeout,
        )
    return output


def render_overlay_burn(
    graph: RenderGraph,
    output: Path | str,
    *,
    cli_path: str = "hyperframes",
    timeout: int | None = None,
    chunk_seconds: float | None = None,
    keep_work: bool = False,
    progress: ProgressFn | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Overlay ``graph``'s layers on ``graph.source`` and write ``output``.

    ``graph.source`` is the finished video (picture and mixed audio); the graph's
    overlays are rendered on a transparent page, then composited with FFmpeg.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    work_dir = output.parent / f"{output.stem}.overlay"
    frames_dir, _ = render_overlay_frames(
        graph,
        work_dir,
        cli_path=cli_path,
        timeout=timeout,
        chunk_seconds=chunk_seconds,
        progress=progress,
        cache_dir=cache_dir,
    )
    composite_overlay(
        Path(graph.source), frames_dir, output, fps=graph.fps, duration=graph.duration
    )
    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    if not output.exists() or output.stat().st_size == 0:
        raise OverlayBurnError(f"overlay burn produced no output: {output}")
    logger.info("overlay_burn_complete", job_id=graph.job_id, output=str(output))
    return output
