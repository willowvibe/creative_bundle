"""Sarvam AI Bulbul TTS provider.

Calls ``POST https://api.sarvam.ai/text-to-speech`` with header
``api-subscription-key: $SARVAM_API``. The response carries base64-encoded
audio in ``audios[0]``; we decode it to the output path.

Supports the Indian-language set:
  bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN.

Defaults to ``bulbul:v4-flash`` (latest, INR 20/10K chars) and falls back to
``bulbul:v3`` (stable API default, INR 30/10K chars) when v4-flash returns
``model_unavailable``. ``bulbul:v2`` remains available as a legacy override.
Costs: ``bulbul:v4-flash`` ~INR 20/10K chars (~$0.023/1K chars);
``bulbul:v3`` ~INR 30/10K chars (~$0.034/1K chars).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import cast

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import run_ffmpeg

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.sarvam.ai"
DEFAULT_MODEL = "bulbul:v4-flash"
DEFAULT_FALLBACK_MODEL = "bulbul:v3"
# Historically the key was documented (and configured) under the misspelling
# ``SARAVAM_API``; most users' .env files use the product name ``SARVAM_API``.
# Both are accepted; the configured name wins when both are set.
API_KEY_ENVS = ("SARVAM_API", "SARAVAM_API")
#: Voice-id prefix that routes to Instant Voice Cloning (#312).
CLONE_PREFIX = "sarvam_clone"
#: The one-shot clone endpoint documents a 1,000-character limit.
MAX_CLONE_CHARS = 1_000

_LANGUAGE_CODE_MAP = {
    "hi": "hi-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "od": "od-IN",
    "pa": "pa-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "en": "en-IN",
    "en-in": "en-IN",
    "hi-in": "hi-IN",
}


def _to_target_language_code(language: str) -> str:
    if not language:
        return "en-IN"
    key = language.lower().strip()
    if key in _LANGUAGE_CODE_MAP:
        return _LANGUAGE_CODE_MAP[key]
    # Already a BCP-47 code? Return uppercase the way Sarvam expects.
    if "-" in language:
        return language
    return "en-IN"


def _parse_voice_id(voice_id: str) -> tuple[str, str]:
    """Parse 'sarvam:shubh' into ('sarvam', 'shubh'); default prefix to 'sarvam'."""
    if ":" in voice_id:
        prefix, speaker = voice_id.split(":", 1)
        return prefix, speaker
    return "sarvam", voice_id


class SarvamTTSProvider(GenerativeProvider):
    """Generate speech via Sarvam's Bulbul v3 TTS endpoint."""

    provider_name = "sarvam_tts"

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig(api_key_env="SARVAM_API")
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key() or next(
            (key for name in API_KEY_ENVS if (key := os.getenv(name))), None
        )
        if not self.api_key:
            raise RuntimeError(
                "SARVAM_API is not set in the environment. "
                "Add it to the repo-root .env (the legacy spelling SARAVAM_API is also accepted)."
            )
        self.timeout = self.config.timeout
        self.model = self.config.provider_options.get("tts_model") or DEFAULT_MODEL
        self.fallback_model = (
            self.config.provider_options.get("fallback_model") or DEFAULT_FALLBACK_MODEL
        )

    async def _call(
        self, text: str, speaker: str, language: str, model: str, pace: float | None = None
    ) -> str:
        payload: dict[str, object] = {
            "text": text,
            "target_language_code": _to_target_language_code(language),
            "model": model,
            "speaker": speaker,
        }
        if pace is not None:
            # 0.5-2.0 on bulbul:v3 and v4-flash; omitted means the API default 1.0.
            payload["pace"] = pace
        headers = {
            "api-subscription-key": self.api_key or "",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post("/text-to-speech", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            audios = data.get("audios") or []
            if not audios:
                raise RuntimeError(f"Sarvam returned no audio for model={model!r}")
            return cast("str", audios[0])

    async def _call_clone(self, text: str, voice_id: str, language: str) -> str:
        """Generate speech with a saved cloned voice (multipart, #312)."""
        payload = {"text": text, "voice_id": voice_id}
        if language:
            payload["language_code"] = _to_target_language_code(language)
        # The endpoint takes multipart form fields (-F in the docs), not JSON.
        files = {key: (None, value) for key, value in payload.items()}
        headers = {"api-subscription-key": self.api_key or ""}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.post("/voices/clone", files=files, headers=headers)
            resp.raise_for_status()
            data = resp.json() or {}
            audio = data.get("audio_b64") or data.get("audio")
            if not audio:
                raise RuntimeError(
                    f"Sarvam voice clone returned no audio for voice_id={voice_id!r}"
                )
            return cast("str", audio)

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        if request.asset_type != AssetType.TTS:
            raise ValueError(
                f"SarvamTTSProvider only handles AssetType.TTS, got {request.asset_type!r}"
            )
        voice_id = request.params.get("voice_id") or "sarvam:shubh"
        language = request.params.get("language") or "en"
        model = request.params.get("model") or self.model
        raw_pace = request.params.get("pace")
        pace = float(raw_pace) if raw_pace is not None else None
        prefix, speaker = _parse_voice_id(voice_id)
        if prefix == CLONE_PREFIX:
            if has_break_tags(request.prompt):
                raise RuntimeError("Sarvam voice cloning does not support <break> tags")
            if len(request.prompt) > MAX_CLONE_CHARS:
                raise RuntimeError(
                    f"Sarvam voice cloning accepts at most {MAX_CLONE_CHARS} characters "
                    f"per call; this line has {len(request.prompt)}. Split it on a "
                    "sentence boundary."
                )
            audio_b64 = await self._call_clone(request.prompt, speaker, language)
            model_used = "voice-clone"
        else:
            if has_break_tags(request.prompt):
                return await synthesize_with_breaks(self, request, output_path)
            try:
                audio_b64 = await self._call(request.prompt, speaker, language, model, pace)
                model_used = model
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in (400, 503)
                    and self.fallback_model
                    and self.fallback_model != model
                ):
                    logger.warning(
                        "sarvam_tts_falling_back",
                        primary=model,
                        fallback=self.fallback_model,
                        status=exc.response.status_code,
                    )
                    audio_b64 = await self._call(
                        request.prompt, speaker, language, self.fallback_model, pace
                    )
                    model_used = self.fallback_model
                else:
                    raise
        write_audio(base64.b64decode(audio_b64), Path(output_path))
        # Bug I1: probe the rendered file so Sarvam scenes carry a real
        # ``duration_ms`` (mirrors elevenlabs_tts.py). Without this, FableCut
        # desyncs Sarvam-scene audio from the TTS track. Fall back to 0 with
        # a warning if ffprobe is unavailable or the file is malformed.
        duration_ms = 0
        try:
            duration_ms = probe_media(output_path).duration_ms()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sarvam_tts_probe_failed",
                path=str(output_path),
                error=str(exc),
            )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "model": model_used,
                "voice_id": voice_id,
                "language": _to_target_language_code(language),
            },
        )


def write_audio(data: bytes, output_path: Path) -> None:
    """Write Sarvam's audio in the format ``output_path`` names (#321).

    Sarvam returns WAV. Written as-is under a ``.mp3`` name, the file's contents
    and extension disagree, which breaks tools that trust the extension. WAV
    bytes going to any other extension are converted with ffmpeg; anything else
    (or a ``.wav`` target) is written unchanged.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not data.startswith(b"RIFF") or output_path.suffix.lower() == ".wav":
        output_path.write_bytes(data)
        return
    source = output_path.with_name(f"{output_path.stem}.sarvam-src.wav")
    source.write_bytes(data)
    try:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-b:a",
                "192k",
                str(output_path),
            ]
        )
    finally:
        source.unlink(missing_ok=True)
