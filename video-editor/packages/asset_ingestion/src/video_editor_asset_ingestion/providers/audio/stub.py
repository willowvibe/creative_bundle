"""Stub music + SFX providers for tests and offline demos.

Mirrors the ``StubTTSProvider`` pattern: synthesize a short audio
clip via FFmpeg's ``lavfi`` source filters and return an
``AssetResult``. Music is a soft multi-tone chord, SFX is a brief
noise burst — both are obviously synthetic but valid MP3s that
the rest of the pipeline can mix.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_MUSIC_DURATION_SECONDS = 30.0
DEFAULT_SFX_DURATION_SECONDS = 5.0


def _audio_codec_from_ext(ext: str) -> str:
    """Choose a sensible FFmpeg audio codec for a file extension."""
    return {
        ".m4a": "aac",
        ".mp4": "aac",
        ".wav": "pcm_s16le",
        ".flac": "flac",
        ".ogg": "libvorbis",
        ".mp3": "libmp3lame",
    }.get(ext, "libmp3lame")


def _build_transcode_cmd(input_path: Path, output_path: Path) -> list[str]:
    """Build an FFmpeg command that transcodes audio to ``output_path``'s extension."""
    ext = output_path.suffix.lower()
    codec = _audio_codec_from_ext(ext)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:a",
        codec,
    ]
    if codec == "libmp3lame" or codec == "aac":
        cmd.extend(["-b:a", "128k"])
    cmd.append(str(output_path))
    return cmd


class StubMusicProvider(GenerativeProvider):
    """Generate a placeholder chord progression for offline music mixing tests."""

    provider_name = "stub_music"

    def __init__(self, duration_seconds: float = DEFAULT_MUSIC_DURATION_SECONDS):
        self.duration_seconds = duration_seconds

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a soft chord as a stand-in for AI music."""
        duration = float(request.params.get("duration_seconds", self.duration_seconds))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ext = output_path.suffix.lower()
        codec = "aac" if ext in {".m4a", ".mp4"} else "libmp3lame"
        # Two-tone chord (C + E) at low volume (-18 dB) so it sounds
        # music-like, not just a single sine wave.
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=261.63:duration={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=329.63:duration={duration}",
                "-filter_complex",
                "[0:a][1:a]amix=inputs=2:duration=longest,volume=-18dB",
                "-c:a",
                codec,
                "-b:a",
                "128k",
                str(output_path),
            ],
            check=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "stub_music_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.MUSIC,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"provider": self.provider_name, "synthetic": True},
        )


class StubSFXProvider(GenerativeProvider):
    """Generate a placeholder noise burst for offline SFX tests."""

    provider_name = "stub_sfx"

    def __init__(self, duration_seconds: float = DEFAULT_SFX_DURATION_SECONDS):
        self.duration_seconds = duration_seconds

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a brief white-noise burst as a stand-in for AI SFX."""
        duration = float(request.params.get("duration_seconds", self.duration_seconds))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ext = output_path.suffix.lower()
        codec = "aac" if ext in {".m4a", ".mp4"} else "libmp3lame"
        # Pink noise burst with a short fade in/out so it sounds SFX-like,
        # not a steady hiss.
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anoisesrc=color=pink:duration={duration}:amplitude=0.15",
                "-af",
                f"afade=t=in:st=0:d=0.1,afade=t=out:st={max(0, duration - 0.2)}:d=0.2",
                "-c:a",
                codec,
                "-b:a",
                "128k",
                str(output_path),
            ],
            check=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "stub_sfx_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SFX,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"provider": self.provider_name, "synthetic": True},
        )


class StubIsolationProvider(GenerativeProvider):
    """Stub audio isolation for offline tests.

    Copies the input audio file referenced in ``params['audio_path']`` to
    ``output_path`` so downstream stages still get a real audio file, and
    returns its probed duration.
    """

    provider_name = "stub_isolation"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Copy the input audio to ``output_path``."""
        audio_path_str = request.params.get("audio_path")
        if not audio_path_str:
            raise ValueError("StubIsolationProvider requires params['audio_path'].")
        audio_path = Path(audio_path_str)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == audio_path.suffix.lower():
            # Same container as input: fast byte copy.
            shutil.copyfile(audio_path, output_path)
        else:
            run_ffmpeg(
                _build_transcode_cmd(audio_path, output_path),
                check=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )

        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "stub_isolation_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.ISOLATION,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"provider": self.provider_name, "synthetic": True},
        )


class StubSpeechToSpeechProvider(GenerativeProvider):
    """Stub speech-to-speech for offline tests.

    Copies the input audio file referenced in ``params['audio_path']`` to
    ``output_path`` (optionally applying a small pitch shift when
    ``params['shift_semitones']`` is set) and returns its probed duration.
    """

    provider_name = "stub_speech_to_speech"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Copy or lightly transform the input audio to ``output_path``."""
        audio_path_str = request.params.get("audio_path")
        if not audio_path_str:
            raise ValueError("StubSpeechToSpeechProvider requires params['audio_path'].")
        audio_path = Path(audio_path_str)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shift = request.params.get("shift_semitones")
        ext = output_path.suffix.lower()

        if shift:
            # Pitch-shift by the requested semitones so tests can verify
            # the output is not a verbatim copy. rubberband's pitch option
            # expects a ratio (1.0 = no change), so convert semitones to
            # ratio with 2^(n/12).
            ratio = 2 ** (float(shift) / 12.0)
            codec = _audio_codec_from_ext(ext)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-af",
                f"rubberband=pitch={ratio}",
                "-c:a",
                codec,
            ]
            if codec == "libmp3lame" or codec == "aac":
                cmd.extend(["-b:a", "128k"])
            cmd.append(str(output_path))
            run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
        elif ext == audio_path.suffix.lower():
            # Same container as input: fast byte copy.
            shutil.copyfile(audio_path, output_path)
        else:
            run_ffmpeg(
                _build_transcode_cmd(audio_path, output_path),
                check=True,
                timeout=RENDER_TIMEOUT_SECONDS,
            )

        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        logger.info(
            "stub_speech_to_speech_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SPEECH_TO_SPEECH,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"provider": self.provider_name, "synthetic": True},
        )
