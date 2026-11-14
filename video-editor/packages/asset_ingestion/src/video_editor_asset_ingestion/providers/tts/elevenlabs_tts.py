"""ElevenLabs text-to-speech: the default voice-over provider (2026-10-30).

``eleven_v4`` speaks Telugu and Tamil and performs inline emotion tags such as ``[sobbing, desperate]``; it won
a blind three-way trial against Sarvam and Smallest.ai on drama lines. ElevenLabs is used for voice-over only:
music, SFX and the other audio tools must not use it (see ``providers/elevenlabs_policy.py``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import save_audio_response
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import _BREAK_RE, wrap_for_elevenlabs

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_v4"
DEFAULT_VOICE = "Xb7hH8MSUJpSbSDYk0k2"

#: Stock (premade) voices by first name, so ``elevenlabs:Brian`` style ids resolve (live list, 2026-10-30).
PREMADE_VOICES = {
    "adam": "pNInz6obpgDQGcFmaJgB",
    "alice": "Xb7hH8MSUJpSbSDYk0k2",
    "bella": "hpp4J3VqNfWAUOO0d1Us",
    "bill": "pqHfZKP75CvOlQylNhV4",
    "brian": "nPczCjzI2devNBz1zQrb",
    "callum": "N2lVS1w4EtoT3dr4eOWO",
    "charlie": "IKne3meq5aSn9XLyUdCD",
    "chris": "iP95p4xoKVk53GoZ742B",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "eric": "cjVigY5qzO86Huf0OWal",
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "harry": "SOYHLrjzK2X1ezoPC6cr",
    "jessica": "cgSgspJ2msm6clMCkdW9",
    "laura": "FGY2WhTYpPnrIDTdsKH5",
    "liam": "TX3LPaxmHKxFdv7VOQHJ",
    "lily": "pFZP5JQG7iQjIQuC4Bku",
    "matilda": "XrExE9yKIg1WjnnlVkGX",
    "river": "SAz9YHcvj6GT2YYXdXww",
    "roger": "CwhRBWXzGAHq8TQ4Fs17",
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "will": "bIHbv24MWmeRgasZH58o",
}

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{20}$")

#: Models that take inline audio tags and reject SSML ``<break>``/``<speak>``.
_TAG_MODELS = ("eleven_v3", "eleven_v4")


def resolve_voice(voice_id: str) -> str:
    """``elevenlabs:<id or stock name>`` -> a voice id. Anything unrecognised passes through unchanged, so a
    wrong voice fails loudly at the API instead of being swapped for another one."""
    voice = voice_id.split(":", 1)[1] if voice_id.startswith("elevenlabs:") else voice_id
    if _VOICE_ID_RE.match(voice):
        return voice
    known = PREMADE_VOICES.get(voice.lower())
    if known:
        return known
    logger.warning("elevenlabs_voice_not_an_id", voice=voice)
    return voice


def _text_for(model: str, text: str) -> str:
    """v3/v4 pause on "..." and have no SSML: turn ``<break>`` tags into ellipses and drop ``<speak>``."""
    if not model.startswith(_TAG_MODELS):
        return wrap_for_elevenlabs(text)
    text = _BREAK_RE.sub(" ... ", text)
    text = re.sub(r"</?speak[^>]*>", "", text)
    return re.sub(r"\s+", " ", text).strip()


#: Conventional env var(s), used when the provider is constructed without a
#: project ``ProviderConfig``. ``TTSRouter`` builds its delegates with no
#: config at all, so without this fallback every routed English request failed
#: with "ElevenLabs API key is not configured" even though the key was set.
API_KEY_ENVS = ("ELEVENLABS_API_KEY",)


class ElevenLabsTTSProvider(GenerativeProvider):
    """Generate TTS audio via the ElevenLabs ``/v1/text-to-speech/{voice_id}`` endpoint."""

    provider_name = "elevenlabs_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        # A configured ``api_key_env`` always wins; the conventional name is
        # only consulted when the config supplies none.
        self.api_key = self.config.api_key() or next(
            (key for name in API_KEY_ENVS if (key := os.getenv(name))), None
        )
        self.model = self.config.default_model or DEFAULT_MODEL
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["xi-api-key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Download TTS audio to ``output_path`` and return its metadata."""
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        voice_id = resolve_voice(
            request.params.get("voice_id") or self.config.default_voice or DEFAULT_VOICE
        )
        model = request.params.get("model") or self.model
        output_format = request.params.get("output_format", "mp3_44100_128")

        voice_settings: dict[str, Any] = {}
        for key in ("stability", "similarity_boost", "style", "use_speaker_boost", "speed"):
            if key in request.params:
                voice_settings[key] = request.params[key]

        payload: dict[str, Any] = {
            "model_id": model,
            "text": _text_for(model, request.prompt),
        }
        # "te-IN" -> "te"; without it v4 guesses the language from the script.
        language = str(request.params.get("language") or "").split("-")[0].lower()
        if language:
            payload["language_code"] = language
        if voice_settings:
            payload["voice_settings"] = voice_settings

        client = await self._get_client()
        logger.info(
            "elevenlabs_tts_request",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
            voice_id=voice_id,
        )

        response = await client.post(
            f"/v1/text-to-speech/{voice_id}",
            params={"output_format": output_format},
            json=payload,
        )
        response.raise_for_status()

        save_audio_response(response, str(output_path))

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "elevenlabs_tts_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "model": model,
                "voice_id": voice_id,
                "output_format": output_format,
            },
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsTTSProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
