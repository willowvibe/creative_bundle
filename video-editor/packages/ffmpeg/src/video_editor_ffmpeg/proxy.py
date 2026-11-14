"""Proxy / preview generation helpers."""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def make_proxy(
    input_path: Path | str,
    output_path: Path | str,
    width: int = 1280,
    render_options: RenderOptions | None = None,
) -> Path:
    """Create a low-bitrate proxy for fast preview."""
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        f"scale={width}:-2",
    ]
    _video_encode(cmd, options)
    _audio_encode(cmd, options)
    cmd.append(str(output))
    logger.info("making_proxy", input=str(input_path), output=str(output))
    run_ffmpeg(cmd)
    return output


def make_preview(input_path: Path | str, output_path: Path | str) -> Path:
    """Create a low-bitrate preview MP4."""
    return make_proxy(input_path, output_path, width=960)
