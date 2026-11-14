"""OpenAI text-to-speech provider."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import save_audio_response
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"


class OpenAITTSProvider(GenerativeProvider):
    """Generate TTS audio via the OpenAI ``/v1/audio/speech`` endpoint."""

    provider_name = "openai_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.model = self.config.default_model or DEFAULT_MODEL
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Download TTS audio to ``output_path`` and return its metadata."""
        if not self.api_key:
            raise RuntimeError("OpenAI API key is not configured.")

        if has_break_tags(request.prompt):
            return await synthesize_with_breaks(self, request, output_path)

        voice = request.params.get("voice") or DEFAULT_VOICE
        model = request.params.get("model") or self.model
        response_format = request.params.get("response_format", "mp3")

        payload: dict[str, Any] = {
            "model": model,
            "voice": voice,
            "input": request.prompt,
            "response_format": response_format,
        }

        client = await self._get_client()
        logger.info(
            "openai_tts_request",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
            voice=voice,
        )
        response = await client.post("/v1/audio/speech", json=payload)
        response.raise_for_status()

        save_audio_response(response, str(output_path))

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "openai_tts_complete",
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
            metadata={"model": model, "voice": voice, "provider": self.provider_name},
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> OpenAITTSProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
