"""TTSRouter: route a TTS request by voice prefix; ElevenLabs is the default voice-over.

Routing rule (2026-10-30: ElevenLabs v4 won a blind voice trial against Sarvam and Smallest.ai):
- An explicit ``sarvam:``, ``kokoro:``, ``kitten:`` or ``moss:`` prefix → that provider.
- Anything else (``elevenlabs:<id>``, a bare voice id, or no voice) → ElevenLabs. With no voice, the
  language picks a default: a native Telugu/Tamil narrator for ``te``/``ta``, Brian otherwise.

The router implements the same ``GenerativeProvider.generate(request, output_path)``
interface so callers can drop it in for either provider without code changes.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_asset_ingestion.models import AssetRequest, AssetResult
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import (
    ElevenLabsTTSProvider,
)
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider
from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider
from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

logger = structlog.get_logger(__name__)

#: ElevenLabs default narrator per language root when the request names no voice.
DEFAULT_ELEVENLABS_VOICES = {
    "te": "elevenlabs:RoLT1qx7XJRXxWSIG5hS",  # Bharadwaj, "Profound Telugu" (won the drama voice trial)
    "ta": "elevenlabs:yrFqUM5ku2rYJCdiBKFU",  # Aarumugam, expressive Tamil narrator
}
DEFAULT_VOICE = "elevenlabs:Brian"

#: Kept for reference (and the explainer's pricing helpers); no longer used to route.
_SARVAM_LANGUAGES = frozenset(
    {"hi", "bn", "gu", "kn", "ml", "mr", "od", "pa", "ta", "te", "en-in", "hi-in"}
)

_KOKORO_LANGUAGES = frozenset({"en", "en-us", "en-gb", "ja", "zh", "es", "fr", "hi", "it", "pt"})


def _language_root(language: str) -> str:
    return (language or "").lower().strip().split("-")[0]


def _pick_provider(voice_id: str, language: str) -> str:
    if voice_id.startswith("sarvam:"):
        return "sarvam"
    if voice_id.startswith("kokoro:"):
        return "kokoro"
    if voice_id.startswith("kitten:"):
        return "kitten_tts"
    if voice_id.startswith("moss:"):
        return "moss_tts"
    # ``elevenlabs:`` or no prefix: ElevenLabs is the default voice-over for every language.
    return "elevenlabs"


def default_voice(language: str) -> str:
    """The ElevenLabs voice used when a request names none."""
    return DEFAULT_ELEVENLABS_VOICES.get(_language_root(language), DEFAULT_VOICE)


class TTSRouter(GenerativeProvider):
    """A thin router that delegates TTS to ElevenLabs (default) or an explicitly prefixed provider."""

    provider_name = "tts_router"

    def __init__(self) -> None:
        # Construct lazily so missing env vars in one provider don't break
        # the other at import time.
        self._sarvam: SarvamTTSProvider | None = None
        self._elevenlabs: ElevenLabsTTSProvider | None = None
        self._kokoro: KokoroTTSProvider | None = None
        self._kitten: KittenTTSProvider | None = None
        self._moss: MossTTSProvider | None = None

    def _get(self, name: str) -> GenerativeProvider:
        if name == "sarvam":
            if self._sarvam is None:
                self._sarvam = SarvamTTSProvider()
            return self._sarvam
        if name == "elevenlabs":
            if self._elevenlabs is None:
                self._elevenlabs = ElevenLabsTTSProvider()
            return self._elevenlabs
        if name == "kokoro":
            if self._kokoro is None:
                self._kokoro = KokoroTTSProvider()
            return self._kokoro
        if name == "kitten_tts":
            if self._kitten is None:
                self._kitten = KittenTTSProvider()
            return self._kitten
        if name == "moss_tts":
            if self._moss is None:
                self._moss = MossTTSProvider()
            return self._moss
        raise ValueError(f"unknown tts provider {name!r}")

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        language = request.params.get("language") or "en"
        voice_id = request.params.get("voice_id") or default_voice(language)
        if not request.params.get("voice_id"):
            request = request.model_copy(
                update={"params": {**request.params, "voice_id": voice_id}}
            )
        provider_name = _pick_provider(voice_id, language)
        logger.info(
            "tts_router_pick",
            provider=provider_name,
            voice_id=voice_id,
            language=language,
        )
        return await self._get(provider_name).generate(request, Path(output_path))
