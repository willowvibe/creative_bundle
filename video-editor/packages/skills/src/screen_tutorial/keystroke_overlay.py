"""Keystroke overlay rendering for screen tutorials.

Renders a small, semi-transparent "keycap" badge (e.g. ``Ctrl+S``) near the
top of the frame whenever the script/transcript calls for an emphasized
keyboard action, or when configured explicitly.

Because keystroke detection from a plain video is not reliable, the skill
accepts an explicit keystroke timeline (from a metadata parser or the user)
and renders it.  The renderer builds an FFmpeg ``drawtext`` filter chain that
shows each key for its configured window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEFAULT_FONT_SIZE = 40
DEFAULT_KEY_DURATION = 1.2


@dataclass(frozen=True)
class Keystroke:
    """A single keystroke/combo overlay window."""

    text: str
    start: float
    end: float


def render_keystrokes(
    input_path: Path,
    output_path: Path,
    keystrokes: list[Keystroke],
    *,
    font: str = DEFAULT_FONT,
    font_size: int = DEFAULT_FONT_SIZE,
    y_offset: int = 60,
    render_options: RenderOptions | None = None,
) -> Path:
    """Burn keystroke badges into ``input_path``.

    Uses one ``drawtext`` filter per keystroke, each enabled only during its
    window.  The badge background is a rounded dark box drawn with ``box=1``.
    """
    options = render_options or RenderOptions()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not keystrokes:
        # No overlays — passthrough copy.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    filters: list[str] = []
    for key in keystrokes:
        start = max(0.0, key.start)
        end = max(start + 0.1, key.end)
        label = key.text.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")
        filters.append(
            f"drawtext=fontfile={font}:"
            f"text='{label}':"
            f"fontsize={font_size}:"
            f"fontcolor=white:"
            f"box=1:boxcolor=black@0.6:boxborderw=12:"
            f"x=(w-text_w)/2:"
            f"y={y_offset}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        ",".join(filters),
        "-c:a",
        "copy",
    ]
    # Encode with the configured video settings.
    encode: list[str] = []
    encode.extend(["-c:v", options.video_codec])
    encode.extend(["-preset", options.preset])
    if options.crf is not None:
        encode.extend(["-crf", str(options.crf)])
    cmd.extend(encode)
    cmd.append(str(output_path))

    logger.info(
        "rendering_keystrokes",
        input=str(input_path),
        count=len(keystrokes),
        output=str(output_path),
    )
    run_ffmpeg(cmd)
    return output_path
