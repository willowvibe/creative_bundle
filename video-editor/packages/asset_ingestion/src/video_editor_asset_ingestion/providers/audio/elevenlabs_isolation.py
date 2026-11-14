"""ElevenLabs audio isolation / voice extraction provider.

Separates speech/voice from background noise and music via
``POST /v1/audio-isolation``. The endpoint accepts a multipart form
with the audio file and an optional ``file_format`` (``pcm_s16le_16``
or ``other``). The response is raw audio bytes (MP3/WAV depending on
the input / format), so we validate the content with
``save_audio_response`` and probe the result to record its duration.

Cost: free at the ElevenLabs creator tier (2026).
"""

from __future__ import annotations

from pathlib import Path

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
DEFAULT_FILE_FORMAT = "other"


def _reconcile_file_format(file_format: str, output_ext: str) -> str:
    """Choose an ElevenLabs ``file_format`` that matches the desired extension.

    ElevenLabs audio-isolation accepts ``pcm_s16le_16`` (raw PCM WAV) or
    ``other`` (default compressed). When the caller asks for ``.wav`` output,
    coerce the format to ``pcm_s16le_16`` so the returned bytes match the
    destination extension. For MP3/M4A and any other extension keep ``other``.
    """
    ext = output_ext.lower().lstrip(".")
    if ext in {"wav", "flac", "aiff", "pcm"}:
        return "pcm_s16le_16"
    return file_format if file_format in {"pcm_s16le_16", "other"} else "other"


class ElevenLabsIsolationProvider(GenerativeProvider):
    """Isolate voice/audio via the ElevenLabs ``/v1/audio-isolation`` endpoint."""

    provider_name = "elevenlabs_isolation"

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
        """POST an audio file to ElevenLabs and write the isolated audio."""
        ensure_voice_only(self.provider_name)
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        audio_path_str = request.params.get("audio_path")
        if not audio_path_str:
            raise ValueError("ElevenLabsIsolationProvider requires params['audio_path'].")
        audio_path = Path(audio_path_str)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        file_format = _reconcile_file_format(
            request.params.get("file_format", DEFAULT_FILE_FORMAT),
            request.output_ext or "mp3",
        )

        form_data = {"file_format": file_format}

        with open(audio_path, "rb") as audio_file:
            files = {"audio": (audio_path.name, audio_file, "application/octet-stream")}

            client = await self._get_client()
            logger.info(
                "elevenlabs_isolation_request",
                job_id=request.job_id,
                asset_id=request.asset_id,
                audio_path=str(audio_path),
                file_format=file_format,
            )

            response = await client.post(
                "/v1/audio-isolation",
                data=form_data,
                files=files,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:1000]
            raise RuntimeError(
                f"ElevenLabs audio isolation failed ({response.status_code}): {body}"
            ) from exc

        save_audio_response(response, str(output_path))

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "elevenlabs_isolation_complete",
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
            metadata={
                "provider": self.provider_name,
                "file_format": file_format,
                "bytes": len(response.content),
            },
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsIsolationProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
