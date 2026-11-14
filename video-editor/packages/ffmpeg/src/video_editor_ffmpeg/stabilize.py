"""Two-pass vidstab stabilisation.

``vidstabdetect`` writes motion vectors, ``vidstabtransform`` applies the
smoothed camera path. libvidstab is a build option, so callers should check
:func:`vidstab_available` before offering stabilisation in a UI or pipeline.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_ffmpeg.run import (
    PROBE_TIMEOUT_SECONDS,
    RENDER_TIMEOUT_SECONDS,
    FFmpegError,
    run_ffmpeg,
)

logger = structlog.get_logger(__name__)


def vidstab_available(timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """Return True when this FFmpeg build has both vidstab filters."""
    result = run_ffmpeg(
        ["ffmpeg", "-hide_banner", "-filters"],
        check=False,
        timeout=timeout,
    )
    listing = (result.stdout or "") + (result.stderr or "")
    return "vidstabdetect" in listing and "vidstabtransform" in listing


def stabilize_video(
    source: Path | str,
    output: Path | str,
    *,
    shakiness: int = 5,
    accuracy: int = 9,
    smoothing: int = 10,
    zoom: int = 0,
    crf: int = 18,
    preset: str = "medium",
    trf_path: Path | str | None = None,
) -> Path:
    """Stabilise ``source`` into ``output`` and return the written path.

    ``shakiness`` (1-10) is how shaky the input is judged to be; ``accuracy``
    (1-15) trades detection quality for time; ``smoothing`` is the number of
    frames the camera path is averaged over; ``zoom`` (0-10) crops in to hide
    the edges the transform exposes.

    The motion-vector file is temporary unless ``trf_path`` is given. Audio is
    stream-copied.
    """
    source_path = Path(source)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not vidstab_available():
        raise FFmpegError(
            "this FFmpeg build has no vidstab support; rebuild with --enable-libvidstab",
            ["ffmpeg", "-filters"],
        )

    temporary_trf = trf_path is None
    trf = Path(trf_path) if trf_path is not None else output_path.with_suffix(".trf")

    detect_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"vidstabdetect=shakiness={shakiness}:accuracy={accuracy}:result={trf}",
        "-f",
        "null",
        "-",
    ]
    run_ffmpeg(detect_cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)

    transform_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        (
            f"vidstabtransform=input={trf}:smoothing={smoothing}:zoom={zoom}:interpol=bicubic,"
            "unsharp=5:5:0.8:3:3:0.4"
        ),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-c:a",
        "copy",
        str(output_path),
    ]
    try:
        run_ffmpeg(transform_cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
    finally:
        if temporary_trf:
            trf.unlink(missing_ok=True)

    logger.info(
        "video_stabilized",
        source=str(source_path),
        output=str(output_path),
        shakiness=shakiness,
        smoothing=smoothing,
    )
    return output_path
