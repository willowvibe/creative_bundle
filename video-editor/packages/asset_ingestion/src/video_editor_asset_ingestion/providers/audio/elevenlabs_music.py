"""ElevenLabs AI music generation provider.

Generates a background music track via ``/v1/music/generate``. The
endpoint accepts a natural-language prompt and an optional
``duration_seconds``. The response is raw MP3 (or whatever format
the request specifies) audio bytes — not JSON — so we set
``Accept: audio/mpeg`` to be safe and probe the file with ffprobe
to read its real duration.

Cost: ~$0.10 per 30s clip (ElevenLabs creator tier, 2026).
"""

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
from video_editor_asset_ingestion.providers.elevenlabs_policy import ensure_voice_only

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"
DEFAULT_DURATION_SECONDS = 30


class ElevenLabsMusicProvider(GenerativeProvider):
    """Generate AI music via the ElevenLabs ``/v1/music/generate`` endpoint."""

    provider_name = "elevenlabs_music"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "audio/mpeg", "Content-Type": "application/json"}
            if self.api_key:
                headers["xi-api-key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Download an AI-generated music clip to ``output_path``."""
        ensure_voice_only(self.provider_name)
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        duration = float(request.params.get("duration_seconds", DEFAULT_DURATION_SECONDS))

        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "duration_seconds": duration,
        }
        # Optional: caller may pre-attach a composition plan from
        # ``/v1/music/plan`` to give the generator section structure.
        if request.params.get("composition_plan"):
            payload["composition_plan"] = request.params["composition_plan"]

        client = await self._get_client()
        logger.info(
            "elevenlabs_music_request",
            job_id=request.job_id,
            asset_id=request.asset_id,
            duration_seconds=duration,
        )

        response = await client.post("/v1/music/generate", json=payload)
        response.raise_for_status()

        save_audio_response(response, str(output_path))

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "elevenlabs_music_complete",
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
            metadata={
                "provider": self.provider_name,
                "duration_seconds": duration,
                "bytes": len(response.content),
            },
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsMusicProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
