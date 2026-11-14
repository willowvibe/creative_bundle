"""ElevenLabs speech-to-speech / voice changer provider.

Transforms one voice into another via
``POST /v1/speech-to-speech/{voice_id}``. The endpoint accepts a
multipart form with the source audio file, target ``voice_id``,
optional ``model_id`` (``eleven_english_sts_v2`` or
``eleven_multilingual_sts_v2``), optional JSON ``voice_settings``,
``seed``, ``remove_background_noise`` flag, and ``file_format`` hint.

The response is raw audio bytes (default MP3), so we validate the
content with ``save_audio_response`` and probe the result for its
duration.

Cost: ~$0.018 per 1k characters / proportional to audio length
(ElevenLabs creator tier, 2026).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import save_audio_response
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.elevenlabs_policy import ensure_voice_only

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_MODEL_ID = "eleven_english_sts_v2"
DEFAULT_FILE_FORMAT = "other"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


def _reconcile_output_format(output_format: str, output_ext: str) -> str:
    """Return an ElevenLabs output_format consistent with the file extension.

    ElevenLabs returns raw bytes in the format requested by ``output_format``.
    If the caller asks for a ``.wav`` file but an MP3 output_format, the saved
    file will be MP3 bytes with a WAV name. This helper picks a sane default
    based on the extension when the formats disagree.
    """
    ext = output_ext.lower().lstrip(".") if output_ext else "mp3"
    is_pcm = output_format.startswith("pcm_") or output_format.startswith("ulaw_")
    is_mp3 = output_format.startswith("mp3_")

    if ext in {"wav", "flac", "aiff", "pcm"} and not is_pcm:
        return "pcm_44100"
    if ext == "mp3" and not is_mp3:
        return DEFAULT_OUTPUT_FORMAT
    return output_format


class ElevenLabsSpeechToSpeechProvider(GenerativeProvider):
    """Convert speech from one voice to another via ElevenLabs."""

    provider_name = "elevenlabs_speech_to_speech"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Multipart upload; let httpx set the boundary.
            headers: dict[str, str] = {}
            if self.api_key:
                headers["xi-api-key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """POST source audio to ElevenLabs and write the converted audio."""
        ensure_voice_only(self.provider_name)
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        audio_path_str = request.params.get("audio_path")
        if not audio_path_str:
            raise ValueError("ElevenLabsSpeechToSpeechProvider requires params['audio_path'].")
        audio_path = Path(audio_path_str)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        voice_id = request.params.get("voice_id") or request.prompt or self.config.default_voice
        if not voice_id:
            raise ValueError(
                "ElevenLabsSpeechToSpeechProvider requires params['voice_id'] "
                "or request.prompt (the target voice id)."
            )

        model_id = request.params.get("model_id") or self.config.default_model or DEFAULT_MODEL_ID
        file_format = request.params.get("file_format", DEFAULT_FILE_FORMAT)
        output_format = _reconcile_output_format(
            request.params.get("output_format", DEFAULT_OUTPUT_FORMAT),
            request.output_ext,
        )
        remove_background_noise = bool(request.params.get("remove_background_noise", False))
        voice_settings = request.params.get("voice_settings")
        seed = request.params.get("seed")

        form_data: dict[str, str] = {
            "model_id": model_id,
            "file_format": file_format,
            "remove_background_noise": str(remove_background_noise).lower(),
        }
        if voice_settings is not None:
            form_data["voice_settings"] = json.dumps(voice_settings)
        if seed is not None:
            form_data["seed"] = str(int(seed))

        params = {"output_format": output_format}

        with open(audio_path, "rb") as audio_file:
            files = {"audio": (audio_path.name, audio_file, "application/octet-stream")}

            client = await self._get_client()
            logger.info(
                "elevenlabs_speech_to_speech_request",
                job_id=request.job_id,
                asset_id=request.asset_id,
                audio_path=str(audio_path),
                voice_id=voice_id,
                model_id=model_id,
            )

            response = await client.post(
                f"/v1/speech-to-speech/{voice_id}",
                data=form_data,
                files=files,
                params=params,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:1000]
            raise RuntimeError(
                f"ElevenLabs speech-to-speech failed ({response.status_code}): {body}"
            ) from exc

        save_audio_response(response, str(output_path))

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "voice_id": voice_id,
            "model_id": model_id,
            "output_format": output_format,
            "bytes": len(response.content),
        }
        if seed is not None:
            metadata["seed"] = int(seed)

        logger.info(
            "elevenlabs_speech_to_speech_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            voice_id=voice_id,
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SPEECH_TO_SPEECH,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata=metadata,
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsSpeechToSpeechProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
