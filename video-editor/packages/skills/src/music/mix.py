"""FFmpeg background music mixing with speech ducking."""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

logger = structlog.get_logger(__name__)


def _video_duration(input_video: Path) -> float:
    """Return the duration of the input video in seconds."""
    duration = probe_media(input_video).duration
    if duration is None:
        raise RuntimeError(f"Could not determine duration for {input_video}")
    return duration


def _build_filter_complex(
    duration: float,
    music_gain_db: float = -14.0,
    speech_gain_db: float = 0.0,
    duck_threshold: float = 0.1,
    duck_ratio: float = 15.0,
    attack_ms: int = 50,
    release_ms: int = 300,
    knee_db: float = 3.0,
    makeup: float = 1.0,
    speech_makeup: float = 2.0,
    limiter_ceiling: float = 0.95,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
) -> str:
    """Build the FFmpeg ``-filter_complex`` string for ``mix_background_music``.

    Exposed separately so tests can inspect the production filter graph without
    invoking FFmpeg.
    """
    music_filters: list[str] = [f"volume={music_gain_db}dB"]
    if fade_in > 0:
        music_filters.append(f"afade=t=in:ss=0:d={fade_in}")
    if fade_out > 0 and duration > fade_out:
        music_filters.append(f"afade=t=out:st={duration - fade_out}:d={fade_out}")
    music_chain = ",".join(music_filters)

    if speech_gain_db:
        speech_split = f"[0:a]asplit=2[spk_raw][ref];[spk_raw]volume={speech_gain_db}dB[spk]"
    else:
        speech_split = "[0:a]asplit=2[spk][ref]"

    return (
        f"[1:a]{music_chain}[bg];"
        f"{speech_split};"
        f"[bg][ref]sidechaincompress="
        f"threshold={duck_threshold}:ratio={duck_ratio}:"
        f"attack={attack_ms}:release={release_ms}:"
        f"knee={knee_db}:makeup={makeup}[ducked];"
        f"[spk]volume={speech_makeup},alimiter=limit={limiter_ceiling}[spk_out];"
        f"[spk_out][ducked]amix=inputs=2:weights=2 1:"
        f"duration=first:dropout_transition=3[outa]"
    )


def mix_background_music(
    input_video: Path | str,
    music_path: Path | str,
    output: Path | str,
    music_gain_db: float = -14.0,
    speech_gain_db: float = 0.0,
    duck_threshold: float = 0.1,
    duck_ratio: float = 15.0,
    attack_ms: int = 50,
    release_ms: int = 300,
    knee_db: float = 3.0,
    makeup: float = 1.0,
    speech_makeup: float = 2.0,
    limiter_ceiling: float = 0.95,
    loop: bool = True,
    fade_in: float = 0.0,
    fade_out: float = 0.0,
    render_options: RenderOptions | None = None,
) -> Path:
    """Mix a background music track under the input video with speech ducking.

    Args:
        input_video: Path to the video whose audio will be preserved.
        music_path: Path to the background music file.
        output: Destination video path.
        music_gain_db: Gain applied to the music before ducking (dB).
        speech_gain_db: Gain applied to the speech audio (dB).
        duck_threshold: Sidechain threshold as a **linear amplitude** in the
            range ``[0.000976563, 1]`` (FFmpeg ``sidechaincompress threshold``).
            Lower values mean compression kicks in on quieter speech. FFmpeg's
            own default is 0.125; the value ``0.1`` here corresponds to
            roughly -20 dBFS.
        duck_ratio: Ducking compression ratio.
        attack_ms: Sidechain attack time in milliseconds.
        release_ms: Sidechain release time in milliseconds.
        knee_db: Knee width in dB.
        makeup: Makeup amplification factor after compression (range 1-64).
        speech_makeup: Linear makeup gain applied to the speech branch before
            the final mix. ``amix`` normalises by the sum of its weights, so
            with ``weights=2 1`` the speech is scaled by 2/3 (-3.5 dB); a
            ``speech_makeup`` of 2.0 lifts the speech back to ~+2.5 dB relative
            to its input so the voiceover stays audible above the ducked bed.
            Set to 1.0 to disable the boost.
        limiter_ceiling: Hard ceiling (linear, 0-1) applied via ``alimiter`` on
            the boosted speech branch to prevent clipping when the makeup gain
            pushes hot speech past full scale. 0.95 ≈ -0.45 dBFS.
        loop: Whether to loop the music if it is shorter than the video.
        fade_in: Music fade-in duration in seconds.
        fade_out: Music fade-out duration in seconds.

    Returns:
        Path to the mixed output video.
    """
    input_video = Path(input_video)
    music_path = Path(music_path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    duration = _video_duration(input_video)
    filter_complex = _build_filter_complex(
        duration=duration,
        music_gain_db=music_gain_db,
        speech_gain_db=speech_gain_db,
        duck_threshold=duck_threshold,
        duck_ratio=duck_ratio,
        attack_ms=attack_ms,
        release_ms=release_ms,
        knee_db=knee_db,
        makeup=makeup,
        speech_makeup=speech_makeup,
        limiter_ceiling=limiter_ceiling,
        fade_in=fade_in,
        fade_out=fade_out,
    )

    options = render_options or RenderOptions()
    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
    ]
    if loop:
        cmd.extend(["-stream_loop", "-1"])
    cmd.extend(
        [
            "-i",
            str(music_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[outa]",
            "-c:v",
            "copy",
        ]
    )
    cmd = _audio_encode(cmd, options)
    cmd.extend(["-shortest", str(output)])

    logger.info(
        "mixing_background_music",
        input_video=str(input_video),
        music=str(music_path),
        output=str(output),
        duration=duration,
        loop=loop,
    )
    run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
    return output
