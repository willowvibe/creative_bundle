"""MOSS-TTS-Nano local text-to-speech provider.

MOSS-TTS-Nano is a 0.1B-parameter TTS model that runs on CPU (4 cores),
supports 20 languages, voice cloning, and 48 kHz stereo output.
Apache 2.0 licensed.

Install: ``pip install moss-tts``
"""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_VOICE = "default"
DEFAULT_SAMPLE_RATE = 48000

# MOSS-TTS-Nano supported language codes
_SUPPORTED_LANGUAGES = frozenset(
    {
        "en",
        "zh",
        "ja",
        "ko",
        "de",
        "fr",
        "ru",
        "pt",
        "es",
        "it",
        "ar",
        "hi",
        "bn",
        "ta",
        "te",
        "mr",
        "gu",
        "kn",
        "ml",
        "pa",
    }
)


class MossTTSProvider(GenerativeProvider):
    """Generate speech locally via MOSS-TTS-Nano inference."""

    provider_name = "moss_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig()
        self._voice = self._config.provider_options.get("voice", DEFAULT_VOICE)
        self._speed = float(self._config.provider_options.get("speed", 1.0))

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        text = request.prompt
        voice = request.params.get("voice_id") or self._voice
        language = request.params.get("language") or "en"

        if has_break_tags(request.prompt):
            return await synthesize_with_breaks(self, request, output_path)

        try:
            from moss_tts import TTS  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "moss-tts package not installed. Install with: pip install moss-tts"
            ) from exc

        tts = TTS(language=language)
        audio = tts.synthesize(text, voice=voice, speed=self._speed)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

        duration_ms = 0
        try:
            info = probe_media(output_path)
            duration_ms = info.duration_ms()
        except Exception:  # noqa: BLE001, S110 - optional voice metadata probe
            pass

        logger.info(
            "moss_tts_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
            voice=voice,
            language=language,
        )

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            scene_id=request.scene_id,
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "voice": voice,
                "language": language,
                "text_length": len(text),
            },
        )
