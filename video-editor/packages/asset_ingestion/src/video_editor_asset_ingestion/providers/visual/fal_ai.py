"""FAL.AI provider for image (Flux) and video (Luma/Wan) generation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig, validate_provider_url
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import (
    MAX_IMAGE_BYTES,
    MediaContentError,
    save_response_bytes,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://queue.fal.run"
DEFAULT_STATUS_URL = "https://queue.fal.run/fal-ai/queue/requests"
DEFAULT_IMAGE_MODEL = "fal-ai/flux/dev"
# Bug #51: ``fal-ai/luma-dream-machine`` is deprecated and returns 500 on poll.
# The tested replacement is Bytedance's Seedance-2.0 mini endpoint. It defaults
# to ``generate_audio: true`` and can hit content-policy failures on generic
# test prompts, so the provider injects ``generate_audio: false`` by default.
DEFAULT_VIDEO_MODEL = "bytedance/seedance-2.0/mini/text-to-video"

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_POLL_TIMEOUT_SECONDS = 600.0

# Status/response endpoint blips (429/5xx) should not cause the whole FAL job
# to be re-submitted. The poll loop handles them locally and keeps polling.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# FAL model families spell the requested clip length differently. Only inject
# for families whose schema we know; an unknown model keeps its own default
# rather than receiving a key that would produce a 422 (#261).
_DEFAULT_FPS = 16.0


def _duration_payload(model: str, seconds: float) -> tuple[str, Any] | None:
    """Return the FAL duration field/value for a known model family."""
    if model.startswith("fal-ai/luma-dream-machine"):
        # Luma accepts a fixed window; 5 s for short scenes, 9 s otherwise.
        return ("duration", "9s" if seconds > 5 else "5s")
    if model.startswith("bytedance/seedance"):
        return ("duration", max(1, int(round(seconds))))
    if model.startswith("fal-ai/wan"):
        # WAN is frame-counted at 16 fps.
        return ("num_frames", max(1, int(round(seconds * _DEFAULT_FPS))))
    return None


def _validate_image_bytes(content: bytes) -> None:
    """Raise MediaContentError if ``content`` does not start with a known
    image magic signature.

    C9 regression: FAL's CDN can return a 200-OK JSON/HTML error page
    during an outage. Without a sniff check, those bytes are written to
    ``.png`` and poison the cache. VIDEO assets are later caught by
    ``probe_media``; IMAGE assets have no downstream probe, so we guard
    them here.
    """
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return
    if content[:3] == b"\xff\xd8\xff":
        return
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return
    raise MediaContentError(f"Expected PNG/JPEG/WebP image, got bytes: {content[:16]!r}")


class FalAIProvider(GenerativeProvider):
    """Generate images and videos via FAL.AI's async queue endpoints."""

    provider_name = "fal_ai"

    #: Synthesizes images from a prompt; eligible for ``"auto"`` stills.
    generates_images = True

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        # ``provider_options`` carries per-provider overrides (e.g. the
        # FAL status endpoint, the dedicated video model). Reading from
        # ``self.config.model_config`` would silently return Pydantic's
        # reserved class attribute (always empty).
        self.status_url = self.config.provider_options.get("status_url", DEFAULT_STATUS_URL)
        self.video_model = self.config.provider_options.get("video_model", "")
        # Bug #68: make FAL video poll duration configurable. Long models
        # (Luma/Wan) often exceed the old hard-coded 120s budget.
        self.poll_interval_seconds = float(
            self.config.provider_options.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
        )
        poll_timeout_seconds = float(
            self.config.provider_options.get("poll_timeout_seconds", DEFAULT_POLL_TIMEOUT_SECONDS)
        )
        self.poll_max_attempts = max(1, int(poll_timeout_seconds / self.poll_interval_seconds))
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Key {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Submit a FAL job, poll for completion, download the result."""
        if not self.api_key:
            raise RuntimeError("FAL key is not configured.")

        model = self._resolve_model(request)
        payload = self._build_payload(request, model)
        client = await self._get_client()

        logger.info(
            "fal_submit",
            job_id=request.job_id,
            asset_id=request.asset_id,
            model=model,
            asset_type=request.asset_type.value,
        )
        submit_response = await client.post(f"/{model}", json=payload)
        submit_response.raise_for_status()
        submit_data = submit_response.json()

        status_url = submit_data.get("status_url")
        response_url = submit_data.get("response_url")
        if not status_url or not response_url:
            raise RuntimeError(f"Unexpected FAL submit response: {submit_data}")

        status_url = validate_provider_url(status_url, "FAL status_url")
        response_url = validate_provider_url(response_url, "FAL response_url")

        result_data = await self._poll_until_complete(client, status_url, response_url)
        download_url = self._extract_download_url(result_data, request.asset_type)
        if not download_url:
            raise RuntimeError(f"No download URL in FAL response: {result_data}")

        media_response = await client.get(download_url)
        media_response.raise_for_status()

        if request.asset_type == AssetType.IMAGE:
            _validate_image_bytes(media_response.content)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_response_bytes(media_response, output_path, max_bytes=MAX_IMAGE_BYTES)

        metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "model": model,
        }
        duration_ms = 0
        if request.asset_type == AssetType.VIDEO:
            info = probe_media(output_path)
            duration_ms = info.duration_ms()
            metadata["duration_ms"] = duration_ms

        logger.info(
            "fal_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata=metadata,
        )

    def _resolve_model(self, request: AssetRequest) -> str:
        """Pick a default model based on asset type if none is provided.

        Bug #48 fix: ``self.config.default_model`` is typically an image
        model (e.g. ``fal-ai/flux/dev``), so a VIDEO request must not
        fall back to it. Use the dedicated ``video_model`` slot from
        ``provider_options``; if unset, fall back to
        ``DEFAULT_VIDEO_MODEL``.
        """
        preferred = request.params.get("model")
        if isinstance(preferred, str):
            return preferred
        if request.asset_type == AssetType.VIDEO:
            return self.video_model or DEFAULT_VIDEO_MODEL
        return self.config.default_model or DEFAULT_IMAGE_MODEL

    def _build_payload(self, request: AssetRequest, model: str) -> dict[str, Any]:
        """Build the FAL input payload from the request.

        Bug #51: Seedance models default to ``generate_audio: true``, which
        routinely trips FAL content-policy checks on generic prompts. Disable
        audio generation by default for Bytedance models; callers can still
        opt back in via ``model_kwargs``.
        """
        payload: dict[str, Any] = {"prompt": request.prompt}
        payload.update(request.params.get("model_kwargs", {}))
        if "model" in payload:
            del payload["model"]
        seconds = request.params.get("duration_seconds")
        if request.asset_type == AssetType.VIDEO and isinstance(seconds, (int, float)):
            duration_field = _duration_payload(model, float(seconds))
            if duration_field is not None:
                key, value = duration_field
                payload.setdefault(key, value)
        if model.startswith("bytedance/"):
            payload.setdefault("generate_audio", False)
        logger.debug("fal_payload", asset_id=request.asset_id, model=model, payload=payload)
        return payload

    async def _poll_until_complete(
        self,
        client: httpx.AsyncClient,
        status_url: str,
        response_url: str,
    ) -> dict[str, Any]:
        """Poll the FAL status URL until the job completes or fails."""
        for attempt in range(self.poll_max_attempts):
            try:
                status_response = await client.get(status_url)
                status_response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "fal_poll_status_retryable",
                        status_code=exc.response.status_code,
                        attempt=attempt,
                        max_attempts=self.poll_max_attempts,
                    )
                    continue
                raise
            status_data = status_response.json()
            status = status_data.get("status")

            logger.debug(
                "fal_poll",
                attempt=attempt,
                status=status,
                max_attempts=self.poll_max_attempts,
                interval=self.poll_interval_seconds,
            )

            if status == "COMPLETED":
                try:
                    result_response = await client.get(response_url)
                    result_response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                        logger.warning(
                            "fal_poll_response_retryable",
                            status_code=exc.response.status_code,
                            attempt=attempt,
                            max_attempts=self.poll_max_attempts,
                        )
                        continue
                    raise
                data: dict[str, Any] = result_response.json()
                return data

            if status in {"FAILED", "ERROR"}:
                raise RuntimeError(f"FAL job failed: {status_data}")

            await asyncio.sleep(self.poll_interval_seconds)

        raise RuntimeError("FAL job polling timed out")

    @staticmethod
    def _extract_download_url(data: dict[str, Any], asset_type: AssetType) -> str:
        """Find the media download URL in a completed FAL response."""
        if asset_type == AssetType.VIDEO:
            url = data.get("video_url")
            if url:
                return str(url)
            # Seedance-2.0 and some other FAL video models nest the result
            # under a ``video`` object instead of a flat ``video_url`` key.
            video_obj = data.get("video")
            if isinstance(video_obj, dict) and video_obj.get("url"):
                return str(video_obj["url"])
        images = data.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                return str(first.get("url", first))
            return str(first)
        url = data.get("url") or data.get("image_url") or data.get("output")
        if url:
            return str(url)
        return ""

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> FalAIProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
