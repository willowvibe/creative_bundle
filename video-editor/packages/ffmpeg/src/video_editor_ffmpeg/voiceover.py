"""External voiceover muxing (Issue A).

The engine's ``mix_background_music`` assumes the speech is already in the
source video's audio track. For any narration-over-existing-video workflow
the voiceover is a separate file (e.g. TTS output), so there needs to be a
pipeline step that muxes it onto the source before the rest of the pipeline
runs. ``mux_voiceover`` replaces the source's audio track with the voiceover
file; the result is dropped into ``ve run`` and the existing music/QA stages
work unchanged (the voiceover is now the speech track).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


def mux_voiceover(
    source_video: Path | str,
    voiceover_path: Path | str,
    output_path: Path | str,
    voiceover_gain_db: float = 0.0,
    shortest: bool = False,
    copy: bool = False,
    render_options: RenderOptions | None = None,
) -> Path:
    """Replace a source video's audio track with an external voiceover file.

    Args:
        source_video: Path to the source video whose visual track is
            preserved and whose audio is discarded.
        voiceover_path: Path to the external voiceover file (any FFmpeg-
            decodable audio: mp3, wav, m4a, ...).
        output_path: Destination video path.
        voiceover_gain_db: Gain applied to the voiceover before muxing, in dB.
            Defaults to 0.0 (unity). Use a positive value if the TTS output is
            quiet; the music stage will duck against this level.
        shortest: If True, terminate the output at the shorter of the video
            and voiceover durations (legacy behaviour). Defaults to False so
            the video is preserved and a duration mismatch is logged as a
            warning rather than silently truncated.
        copy: If True, copy the video stream instead of re-encoding. Defaults
            to False because copying can produce frozen/gray frames when the
            source does not start on a keyframe (Bug #34).
        render_options: Optional codec/bitrate/fps settings for re-encoded
            streams.

    Returns:
        Path to the muxed output video.

    Notes:
        The visual track is re-encoded by default so the configured
        ``RenderOptions`` codec/bitrate/fps are honored. Pass ``copy=True``
        to preserve the original video stream when speed matters and the
        source is known to be keyframe-aligned.
    """
    source_video = Path(source_video)
    voiceover_path = Path(voiceover_path)
    output = Path(output_path)
    options = render_options or RenderOptions()
    output.parent.mkdir(parents=True, exist_ok=True)

    if not voiceover_path.exists():
        raise FileNotFoundError(
            f"Voiceover file not found: {voiceover_path}. "
            f"Pass a valid --voiceover path (an audio file produced by TTS or "
            f"recorded narration)."
        )

    # Build the audio chain: optional gain + resample to a common rate so
    # downstream stages (music mix, QA loudness) see a consistent signal.
    vo_chain = "aresample=44100"
    if voiceover_gain_db:
        vo_chain = f"volume={voiceover_gain_db}dB,aresample=44100"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video),
        "-i",
        str(voiceover_path),
        "-filter_complex",
        f"[1:a]{vo_chain}[vo]",
        "-map",
        "0:v",
        "-map",
        "[vo]",
    ]
    if copy:
        cmd.extend(["-c:v", "copy"])
    else:
        _video_encode(cmd, options)
    _audio_encode(cmd, options)
    if shortest:
        cmd.append("-shortest")
    cmd.append(str(output))

    from video_editor_ffmpeg.probe import probe_media

    source_info = probe_media(source_video)
    vo_info = probe_media(voiceover_path)
    if (
        source_info.duration is not None
        and vo_info.duration is not None
        and abs(source_info.duration - vo_info.duration) > 0.5
    ):
        logger.warning(
            "mux_voiceover_duration_mismatch",
            source=str(source_video),
            voiceover=str(voiceover_path),
            source_duration=source_info.duration,
            voiceover_duration=vo_info.duration,
            shortest=shortest,
        )

    logger.info(
        "muxing_voiceover",
        source=str(source_video),
        voiceover=str(voiceover_path),
        output=str(output),
        gain_db=voiceover_gain_db,
        shortest=shortest,
    )
    run_ffmpeg(cmd)
    return output
