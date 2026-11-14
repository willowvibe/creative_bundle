"""OpenRouter provider for image, video, and music generation.

OpenRouter exposes dedicated generation APIs for each modality on top of its
LLM gateway:

- **Image**:  ``POST /api/v1/images`` -> base64 images (synchronous).
  ``data[i].b64_json`` holds the bytes; ``usage.cost`` the USD cost.
- **Video**:  ``POST /api/v1/videos`` -> ``{id, polling_url, status}``; poll
  ``GET /api/v1/videos/{id}`` until ``status == "completed"``; download from
  ``unsigned_urls[0]`` (requires the bearer token).
- **Music/audio**: chat completions with ``modalities=["text","audio"]``
  (e.g. ``google/lyria-3-pro-preview``). The generated audio is returned as
  base64 on ``choices[0].message.audio`` — **experimental**, since OpenRouter
  does not yet document a dedicated music endpoint or publish Lyria pricing.

This provider implements the :class:`GenerativeProvider` surface (``generate``)
so it works through :class:`AssetIngestionManager` for all three asset types.
Pricing lives in ``models.yaml`` under ``providers.openrouter.models``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    MediaContentError,
    save_media_bytes,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.visual.fal_ai import _validate_image_bytes

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_IMAGE_MODEL = "openai/gpt-image-2"
DEFAULT_VIDEO_MODEL = "google/veo-3.1-fast"
DEFAULT_MUSIC_MODEL = "google/lyria-3-pro-preview"

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_POLL_TIMEOUT_SECONDS = 900.0

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _decode_b64(value: str) -> bytes:
    """Decode a (possibly data-URL prefixed) base64 payload to bytes."""
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    try:
        return base64.b64decode(value)
    except (binascii.Error, ValueError) as exc:  # pragma: no cover - defensive
        raise MediaContentError(f"OpenRouter returned invalid base64 media: {exc}") from exc


class OpenRouterProvider(GenerativeProvider):
    """Generate images, videos, and music via OpenRouter's generation APIs."""

    provider_name = "openrouter"

    #: Synthesizes images from a prompt; eligible for ``"auto"`` stills.
    generates_images = True

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self.default_model = self.config.default_model or DEFAULT_IMAGE_MODEL

        opts = self.config.provider_options
        self.video_model = opts.get("video_model", "")
        self.music_model = opts.get("music_model", "")
        self.default_aspect_ratio = opts.get("aspect_ratio", "")
        self.default_resolution = opts.get("resolution", "")
        self.default_quality = opts.get("quality", "")
        # Optional ``HTTP-Referer`` / ``X-Title`` attribution headers OpenRouter
        # uses for app rankings; both are optional.
        self.referer = opts.get("referer", "")
        self.app_title = opts.get("app_title", "")

        self.poll_interval_seconds = float(
            opts.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
        )
        poll_timeout_seconds = float(opts.get("poll_timeout_seconds", DEFAULT_POLL_TIMEOUT_SECONDS))
        self.poll_max_attempts = max(1, int(poll_timeout_seconds / self.poll_interval_seconds))
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if self.referer:
                headers["HTTP-Referer"] = self.referer
            if self.app_title:
                headers["X-Title"] = self.app_title
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Dispatch to the image, video, or music generation path."""
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if request.asset_type == AssetType.IMAGE:
            return await self._generate_image(request, output_path)
        if request.asset_type == AssetType.VIDEO:
            return await self._generate_video(request, output_path)
        if request.asset_type == AssetType.MUSIC:
            return await self._generate_music(request, output_path)
        raise RuntimeError(
            f"OpenRouter provider does not support asset type {request.asset_type.value!r}"
        )

    # ------------------------------------------------------------------ image

    def _image_model(self, request: AssetRequest) -> str:
        preferred = request.params.get("model")
        if isinstance(preferred, str) and preferred:
            return preferred
        return self.config.default_model or DEFAULT_IMAGE_MODEL

    def _image_payload(self, request: AssetRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "prompt": request.prompt}
        aspect = request.params.get("aspect_ratio") or self.default_aspect_ratio
        if aspect:
            payload["aspect_ratio"] = aspect
        resolution = request.params.get("resolution") or self.default_resolution
        if resolution:
            payload["resolution"] = resolution
        quality = request.params.get("quality") or self.default_quality
        if quality:
            payload["quality"] = quality
        # Allow explicit extra request fields (n, seed, output_format, ...).
        for key, value in (request.params.get("model_kwargs") or {}).items():
            payload.setdefault(key, value)
        return payload

    async def _generate_image(self, request: AssetRequest, output_path: Path) -> AssetResult:
        model = self._image_model(request)
        payload = self._image_payload(request, model)
        client = await self._get_client()

        logger.info(
            "openrouter_image_submit",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
        )
        response = await client.post("/images", json=payload)
        response.raise_for_status()
        data = response.json()

        images = data.get("data") or []
        if not images:
            raise MediaContentError(f"OpenRouter image response had no data: {data}")
        b64 = images[0].get("b64_json") or images[0].get("b64")
        if not b64:
            raise MediaContentError(
                f"OpenRouter image response missing b64_json: {list(images[0])}"
            )
        content = _decode_b64(str(b64))
        _validate_image_bytes(content)
        save_media_bytes(content, output_path, max_bytes=MAX_IMAGE_BYTES)

        metadata: dict[str, Any] = {"provider": self.provider_name, "model": model}
        cost = (data.get("usage") or {}).get("cost")
        if cost is not None:
            metadata["cost"] = float(cost)
        logger.info(
            "openrouter_image_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            cost=cost,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.IMAGE,
            scene_id=request.scene_id,
            local_path=str(output_path),
            metadata=metadata,
        )

    # ------------------------------------------------------------------ video

    def _video_model(self, request: AssetRequest) -> str:
        preferred = request.params.get("model")
        if isinstance(preferred, str) and preferred:
            return preferred
        return self.video_model or DEFAULT_VIDEO_MODEL

    def _video_payload(self, request: AssetRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "prompt": request.prompt}
        duration = request.params.get("duration_seconds")
        if duration:
            payload["duration"] = int(float(duration))
        aspect = request.params.get("aspect_ratio") or self.default_aspect_ratio
        if aspect:
            payload["aspect_ratio"] = aspect
        resolution = request.params.get("resolution") or self.default_resolution
        if resolution:
            payload["resolution"] = resolution
        for key, value in (request.params.get("model_kwargs") or {}).items():
            payload.setdefault(key, value)
        return payload

    async def _generate_video(self, request: AssetRequest, output_path: Path) -> AssetResult:
        model = self._video_model(request)
        payload = self._video_payload(request, model)
        client = await self._get_client()

        logger.info(
            "openrouter_video_submit",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
        )
        submit = await client.post("/videos", json=payload)
        submit.raise_for_status()
        submit_data = submit.json()
        job_id = submit_data.get("id")
        if not job_id:
            raise RuntimeError(f"Unexpected OpenRouter video submit response: {submit_data}")

        result_data = await self._poll_video(client, str(job_id))
        urls = result_data.get("unsigned_urls") or []
        if not urls:
            raise RuntimeError(f"OpenRouter video {job_id} completed with no outputs")
        download_url = str(urls[0])

        media = await client.get(download_url)
        media.raise_for_status()
        save_media_bytes(media.content, output_path, max_bytes=MAX_VIDEO_BYTES)

        info = probe_media(output_path)
        duration_ms = info.duration_ms()
        metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "model": model,
            "duration_ms": duration_ms,
        }
        cost = (result_data.get("usage") or {}).get("cost")
        if cost is not None:
            metadata["cost"] = float(cost)
        logger.info(
            "openrouter_video_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
            cost=cost,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata=metadata,
        )

    async def _poll_video(self, client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        """Poll ``/videos/{id}`` until the job completes or fails."""
        for attempt in range(self.poll_max_attempts):
            try:
                response = await client.get(f"/videos/{job_id}")
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "openrouter_video_poll_retryable",
                        status_code=exc.response.status_code,
                        attempt=attempt,
                    )
                    await asyncio.sleep(self.poll_interval_seconds)
                    continue
                raise
            data = response.json()
            status = data.get("status")
            if status == "completed":
                return dict(data)
            if status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"OpenRouter video job {job_id} {status}: {data.get('error')}")
            logger.debug(
                "openrouter_video_poll",
                attempt=attempt,
                status=status,
                max_attempts=self.poll_max_attempts,
            )
            await asyncio.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"OpenRouter video job {job_id} did not complete within the poll budget")

    # ------------------------------------------------------------------ music

    def _music_model(self, request: AssetRequest) -> str:
        preferred = request.params.get("model")
        if isinstance(preferred, str) and preferred:
            return preferred
        return self.music_model or DEFAULT_MUSIC_MODEL

    async def _generate_music(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Generate music/audio via chat completions with the ``audio`` modality.

        OpenRouter has no documented music endpoint yet; Lyria-class models
        emit audio through chat completions. The response is parsed
        defensively because the exact shape may change while this is in
        preview.
        """
        model = self._music_model(request)
        client = await self._get_client()
        payload: dict[str, Any] = {
            "model": model,
            "modalities": ["text", "audio"],
            "messages": [{"role": "user", "content": request.prompt}],
        }
        for key, value in (request.params.get("model_kwargs") or {}).items():
            payload.setdefault(key, value)

        logger.info(
            "openrouter_music_submit",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
        )
        response = await client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()

        content = await self._extract_audio(client, data)
        save_media_bytes(content, output_path, max_bytes=MAX_AUDIO_BYTES)

        metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "model": model,
            "experimental": True,
        }
        cost = (data.get("usage") or {}).get("cost")
        if cost is not None:
            metadata["cost"] = float(cost)
        logger.info(
            "openrouter_music_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            cost=cost,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.MUSIC,
            scene_id=request.scene_id,
            local_path=str(output_path),
            metadata=metadata,
        )

    async def _extract_audio(self, client: httpx.AsyncClient, data: dict[str, Any]) -> bytes:
        """Pull audio bytes out of a chat-completions audio response."""
        choices = data.get("choices") or []
        if not choices:
            raise MediaContentError(f"OpenRouter audio response had no choices: {data}")
        message = choices[0].get("message") or {}
        audio = message.get("audio")

        if isinstance(audio, dict):
            if audio.get("data"):
                return _decode_b64(str(audio["data"]))
            if audio.get("url"):
                media = await client.get(str(audio["url"]))
                media.raise_for_status()
                return media.content

        # Some providers nest audio parts in a list-valued ``content``.
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_audio = part.get("audio")
                if isinstance(part_audio, dict) and part_audio.get("data"):
                    return _decode_b64(str(part_audio["data"]))
                if part.get("data") and str(part.get("type", "")).startswith("audio"):
                    return _decode_b64(str(part["data"]))

        raise MediaContentError("OpenRouter audio response had no decodable audio payload")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> OpenRouterProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
