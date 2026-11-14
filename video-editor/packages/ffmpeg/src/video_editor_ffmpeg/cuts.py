"""Cut, concat, and assembly helpers."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

import structlog
from video_editor_state.store import ArtifactStore

from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def _ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", *args]
    logger.info("running_ffmpeg", cmd=" ".join(cmd))
    run_ffmpeg(cmd)


def cut_segment(
    input_path: Path | str,
    output_path: Path | str,
    start: float,
    end: float,
    *,
    copy: bool = False,
    render_options: RenderOptions | None = None,
) -> Path:
    """Cut a single contiguous segment from a video.

    Defaults to re-encoding so that cuts at non-keyframe boundaries do not
    produce frozen/gray frames. Pass ``copy=True`` only when the start time
    is known to align with a keyframe and speed matters more than frame
    accuracy.
    """
    output = Path(output_path)
    options = render_options or RenderOptions()
    duration = end - start
    args = [
        "-i",
        str(input_path),
        "-ss",
        str(start),
        "-t",
        str(duration),
    ]
    if copy:
        args.extend(["-c", "copy"])
    else:
        _video_encode(args, options)
        _audio_encode(args, options)
    args.append(str(output))
    _ffmpeg(args)
    return output


def cut_segments(
    input_path: Path | str,
    segments: Sequence[tuple[float, float]],
    output_dir: Path | str,
    render_options: RenderOptions | None = None,
) -> list[Path]:
    """Cut multiple segments and return segment file paths."""
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []
    for idx, (start, end) in enumerate(segments):
        segment_path = output_dir / f"segment_{idx:04d}{input_path.suffix}"
        cut_segment(input_path, segment_path, start, end, render_options=render_options)
        segment_paths.append(segment_path)
    return segment_paths


def _escape_concat_path(path: Path | str) -> str:
    """Escape a file path for FFmpeg's concat demuxer.

    The concat format is ``file 'PATH'``. Single quotes inside the path are
    escaped with the standard close/escape/open pattern ``'\\''``.
    """
    posix_path = Path(path).resolve().as_posix()
    return posix_path.replace("'", "'\\'")


def concat_segments(segment_paths: Sequence[Path | str], output_path: Path | str) -> Path:
    """Concatenate segments using the concat demuxer."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for segment in segment_paths:
            escaped = _escape_concat_path(segment)
            f.write(f"file '{escaped}'\n")
        list_path = f.name

    try:
        _ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c",
                "copy",
                str(output),
            ]
        )
    finally:
        Path(list_path).unlink(missing_ok=True)

    return output


def assemble_with_filter_complex(
    input_path: Path | str,
    segments: Sequence[tuple[float, float]],
    output_path: Path | str,
    render_options: RenderOptions | None = None,
) -> Path:
    """Assemble kept segments using a single filter_complex expression.

    This re-encodes, so use when you need exact frame boundaries or transitions.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    options = render_options or RenderOptions()
    filters = []
    selects = []
    for idx, (start, end) in enumerate(segments):
        filters.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{idx}]")
        filters.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{idx}]")
        selects.extend([f"[v{idx}]", f"[a{idx}]"])

    filter_str = ";".join(filters)
    filter_str += f";{''.join(selects)}concat=n={len(segments)}:v=1:a=1[outv][outa]"

    args = [
        "-i",
        str(input_path),
        "-filter_complex",
        filter_str,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
    ]
    _video_encode(args, options)
    _audio_encode(args, options)
    args.append(str(output))
    _ffmpeg(args)
    return output


def apply_cuts(
    input_path: Path | str,
    segments: Sequence[tuple[float, float]],
    output_path: Path | str,
    store: ArtifactStore | None = None,
    force: bool = False,
    render_options: RenderOptions | None = None,
) -> Path:
    """High-level cut helper that assembles kept segments with filter_complex.

    Re-encodes to guarantee valid output streams after cutting/concatenating.
    """
    import shutil

    output = Path(output_path)
    if store and not store.should_run("apply_cuts", output, force=force):
        return output

    if not segments:
        logger.info("no_segments_to_keep", input=str(input_path), output=str(output))
        shutil.copyfile(input_path, output)
    else:
        assemble_with_filter_complex(input_path, segments, output, render_options=render_options)

    if store:
        store.record(
            "apply_cuts",
            inputs={"input": str(input_path), "segments": segments},
            outputs={"output": str(output)},
        )
        store.manifest.stage_done("cut", {"assembly": str(output)})
    return output
