"""Stub TTS provider for tests and offline demos."""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_DURATION_SECONDS = 1.0


class StubTTSProvider(GenerativeProvider):
    """Generate a placeholder sine-wave audio file without external APIs."""

    provider_name = "stub_tts"

    def __init__(self, duration_seconds: float = DEFAULT_DURATION_SECONDS):
        self.duration_seconds = duration_seconds

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Render a short sine tone as a stand-in for TTS audio."""
        duration = float(request.params.get("duration", self.duration_seconds))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Choose a codec compatible with the requested container.
        ext = output_path.suffix.lower()
        codec = "aac" if ext in {".m4a", ".mp4"} else "libmp3lame"
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}",
                "-c:a",
                codec,
                "-b:a",
                "192k",
                str(output_path),
            ],
            check=True,
            timeout=RENDER_TIMEOUT_SECONDS,
        )

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "stub_tts_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={"provider": self.provider_name, "synthetic": True},
        )
