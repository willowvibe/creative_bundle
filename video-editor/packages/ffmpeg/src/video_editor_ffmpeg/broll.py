"""B-roll insertion helpers."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

import structlog

from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def insert_broll(
    base_video: Path | str,
    broll_clips: Sequence[tuple[Path | str, float, float]],
    output_path: Path | str,
    render_options: RenderOptions | None = None,
) -> Path:
    """Overlay b-roll clips on top of a base video.

    broll_clips: list of (clip_path, start_time, duration)
    """
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)

    inputs = ["-i", str(base_video)]
    for clip, _, _ in broll_clips:
        inputs.extend(["-i", str(clip)])

    overlays = []
    for idx, (_, start, duration) in enumerate(broll_clips, start=1):
        # Offset the overlay's PTS so it aligns with the base timeline at
        # ``start``. Without ``setpts`` the overlay stream ends before the
        # enable window turns on, causing a frozen last frame (C31).
        overlays.append(
            f"[{idx}:v]trim=duration={duration},setpts=PTS-STARTPTS+{start}/TB,"
            f"format=yuva420p,fade=t=out:st={duration - 0.5}:d=0.5[ov{idx}];"
            f"[base][ov{idx}]overlay=0:0:enable='between(t,{start},{start + duration})'[base]"
        )

    filter_str = "[0:v]copy[base];" + ";".join(overlays)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_str,
        "-map",
        "[base]",
        "-map",
        "0:a",
    ]
    _video_encode(cmd, options)
    cmd.extend(["-c:a", "copy"])
    cmd.append(str(output))
    logger.info("inserting_broll", base=str(base_video), clips=len(broll_clips))
    run_ffmpeg(cmd)
    return output


def generate_placeholder(
    output_path: Path | str,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    label: str = "B-roll Placeholder",
    render_options: RenderOptions | None = None,
) -> Path:
    """Generate a colored placeholder video with centered text."""
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)

    # Write the label to a temp file and use ``textfile=`` so FFmpeg reads it
    # directly. This avoids all drawtext/filtergraph escaping problems with
    # apostrophes, percent signs, braces, and backslashes (C33).
    text_file = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
            tf.write(label)
            text_file = tf.name

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=darkblue:s={width}x{height}:d={duration}",
            "-vf",
            f"drawtext=textfile={text_file}:fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            "fontsize=72:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
            "-pix_fmt",
            "yuv420p",
        ]
        _video_encode(cmd, options)
        cmd.append(str(output))
        logger.info("generating_placeholder", output=str(output), duration=duration, label=label)
        run_ffmpeg(cmd)
    finally:
        if text_file:
            with contextlib.suppress(OSError):
                os.remove(text_file)
    return output


def mix_sfx_into_video(
    base_video: Path | str,
    sfx_timeline: Sequence[tuple[Path | str, float]],
    output_path: Path | str,
    sfx_gain_db: float = -6.0,
    render_options: RenderOptions | None = None,
) -> Path:
    """Mix per-scene SFX clips into the audio track of a base video.

    Args:
        base_video: Path to the base video whose existing audio track
            is preserved (and mixed on top of).
        sfx_timeline: Sequence of ``(sfx_path, start_time_seconds)``
            tuples. ``start_time`` is when the SFX should begin
            playing relative to the start of the base video.
        output_path: Destination video path.
        sfx_gain_db: Gain applied to each SFX stream before mixing,
            in dB. Defaults to -6.0 so SFX sits under the voiceover
            without ducking. Use a more negative value (e.g. -12.0)
            for busier voiceover mixes.
        render_options: Optional codec/bitrate/fps settings for the
            audio encode step. Video is copied through unchanged.

    Returns:
        Path to the mixed output video.

    Notes:
        Uses FFmpeg's ``amix`` filter. An empty ``sfx_timeline``
        falls back to a direct copy of the base video (so callers can
        invoke this unconditionally without a SFX map present).
    """
    base_video = Path(base_video)
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)

    if not sfx_timeline:
        # No SFX to mix — copy the base audio/video through unchanged.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(base_video),
            "-c:v",
            "copy",
        ]
        _audio_encode(cmd, options)
        cmd.append(str(output))
        logger.info("mixing_sfx_skipped", base=str(base_video), reason="empty_timeline")
        run_ffmpeg(cmd)
        return output

    # Build the filter graph: base audio [0:a] + N SFX streams,
    # each delayed + volume-attenuated, all mixed via amix.
    # Use ``duration=first:dropout_transition=0`` so the mix matches
    # the base video's length and each SFX fades cleanly at the
    # end of the base — the ``apad+duration=longest+dropout_transition=2``
    # combination hangs on some ffmpeg builds.
    inputs = ["-i", str(base_video)]
    mix_parts: list[str] = []
    for idx, (sfx_path, start_time) in enumerate(sfx_timeline, start=1):
        inputs.extend(["-i", str(sfx_path)])
        delay_ms = max(0, int(start_time * 1000))
        # adelay pads the SFX with silence up to start_time, then
        # volume applies the gain.
        mix_parts.append(f"[{idx}:a]adelay={delay_ms}:all=1,volume={sfx_gain_db}dB[a{idx}]")

    amix_inputs = "[0:a]" + "".join(f"[a{idx}]" for idx in range(1, len(sfx_timeline) + 1))
    # Use ``normalize=0`` so the base voiceover is not attenuated by
    # 1/(N+1) every time an SFX clip is active (C32). Per-SFX level is
    # already controlled by ``volume={sfx_gain_db}dB``.
    filter_complex = (
        ";".join(mix_parts)
        + f";{amix_inputs}amix=inputs={len(sfx_timeline) + 1}:duration=first:dropout_transition=0:normalize=0[outa]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[outa]",
        "-c:v",
        "copy",
    ]
    _audio_encode(cmd, options)
    cmd.append(str(output))
    logger.info(
        "mixing_sfx",
        base=str(base_video),
        sfx_count=len(sfx_timeline),
        gain_db=sfx_gain_db,
    )
    run_ffmpeg(cmd)
    return output
