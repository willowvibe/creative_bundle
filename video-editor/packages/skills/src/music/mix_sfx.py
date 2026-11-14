"""One call for the three audio buses: voice, ducked music, timed SFX.

The engine renders pictures and muxes audio separately. This helper owns the
audio side of a short: replace the rendered track with the narration, duck a
looped music bed under it, then place the SFX cues on top. The caller normalises
loudness afterwards (the pipelines own `normalize_loudness`).

It deliberately composes the two tested passes rather than growing one large
filtergraph: a bug in the ducking or the SFX mix stays isolated, and the video
stream is copied through both. The cost is one extra audio encode per video.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import structlog
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.voiceover import mux_voiceover

from music.mix import mix_background_music

logger = structlog.get_logger(__name__)

DEFAULT_MUSIC_GAIN_DB = -14.0
DEFAULT_SFX_GAIN_DB = -8.0
DEFAULT_DUCK_THRESHOLD = 0.1
DEFAULT_DUCK_RATIO = 15.0


class MixError(RuntimeError):
    """A mix input is missing or unusable."""


def mix_voice_music_sfx(
    video: Path | str,
    voice: Path | str,
    music: Path | str,
    sfx_timeline: Sequence[tuple[Path | str, float]],
    output: Path | str,
    *,
    music_gain_db: float = DEFAULT_MUSIC_GAIN_DB,
    sfx_gain_db: float = DEFAULT_SFX_GAIN_DB,
    duck_threshold: float = DEFAULT_DUCK_THRESHOLD,
    duck_ratio: float = DEFAULT_DUCK_RATIO,
    render_options: RenderOptions | None = None,
) -> Path:
    """Mux ``voice``, duck ``music`` under it, then mix ``sfx_timeline``.

    ``sfx_timeline`` is ``[(clip, start_seconds), ...]``. An empty timeline
    still produces the voice + music mix. The result is **not** loudness
    normalised; the caller runs that pass.
    """
    video_path = Path(video)
    voice_path = Path(voice)
    music_path = Path(music)
    output_path = Path(output)
    for label, path in (("video", video_path), ("voice", voice_path), ("music", music_path)):
        if not path.is_file():
            raise MixError(f"{label} file not found: {path}")
    for clip, _start in sfx_timeline:
        if not Path(clip).is_file():
            raise MixError(f"SFX clip not found: {clip}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    voiced = output_path.with_name(f".{output_path.name}.voiced.mp4")
    with_music = output_path.with_name(f".{output_path.name}.music.mp4")
    try:
        mux_voiceover(video_path, voice_path, voiced, render_options=render_options)
        mix_background_music(
            voiced,
            music_path,
            with_music,
            music_gain_db=music_gain_db,
            duck_threshold=duck_threshold,
            duck_ratio=duck_ratio,
            render_options=render_options,
        )
        mix_sfx_into_video(
            with_music,
            sfx_timeline,
            output_path,
            sfx_gain_db=sfx_gain_db,
            render_options=render_options,
        )
    finally:
        for temporary in (voiced, with_music):
            temporary.unlink(missing_ok=True)

    logger.info(
        "voice_music_sfx_mixed",
        video=str(video_path),
        output=str(output_path),
        sfx_count=len(sfx_timeline),
    )
    return output_path
