"""Audio normalization helpers."""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def normalize_audio(
    input_path: Path | str,
    output_path: Path | str,
    render_options: RenderOptions | None = None,
) -> Path:
    """Apply EBU R128 loudnorm filter."""
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v",
        "copy",
    ]
    _audio_encode(cmd, options)
    cmd.append(str(output))
    logger.info("normalizing_audio", input=str(input_path), output=str(output))
    run_ffmpeg(cmd)
    return output
