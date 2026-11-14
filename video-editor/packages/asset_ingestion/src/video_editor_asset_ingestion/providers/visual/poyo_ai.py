"""PoYo.ai provider for image and video generation.

PoYo.ai exposes a unified async API for many generative models:

- Submit: ``POST /api/generate/submit``
- Poll:   ``GET /api/generate/status/{task_id}``
- Result: ``data.files[].file_url`` when ``status == "finished"``

This provider supports both ``AssetType.IMAGE`` and ``AssetType.VIDEO`` by
dispatching to the appropriate PoYo model ID and normalizing the response.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._hosting import hosted_urls
from video_editor_asset_ingestion.providers._media import (
    MAX_IMAGE_BYTES,
    MediaContentError,
    save_response_bytes,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.poyo_tasks import clear_task, pending_task, record_task
from video_editor_asset_ingestion.providers.visual.fal_ai import (
    _validate_image_bytes,
)

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.poyo.ai"
#: Cheapest good-quality still (5 credits). ``gpt-image-2`` is $0.010 at its
#: low tier but that is a quality setting, not a flat price; nano-banana's flat
#: 5 credits is the predictable default. Plans and provider config still win.
DEFAULT_IMAGE_MODEL = "nano-banana-2-lite"
DEFAULT_VIDEO_MODEL = "seedance-2-fast"
#: Video models with a fixed clip length and no duration, aspect_ratio or audio inputs (PoYo catalog).
FIXED_LENGTH_VIDEO_MODELS = frozenset({"wan2.2-image-to-video-fast", "wan2.2-text-to-video-fast"})

#: Image models that take ``aspect_ratio`` and reject gpt-image's ``size`` /
#: ``resolution`` presets (#265). Kept explicit: the bigger nano-banana ids
#: (``nano-banana-2``, ``-pro``) publish resolution tiers and do accept them,
#: so a prefix match would break them.
_ASPECT_RATIO_ONLY_IMAGE_MODELS: frozenset[str] = frozenset(
    {"nano-banana-2-lite", "nano-banana-2-1"}
)

#: Reference images (#318): these models take ``image_urls`` only through their ``-edit`` variant
#: (PoYo catalog, 2026-11-11; same price as the base model).
_REFERENCE_VARIANTS: dict[str, str] = {
    "nano-banana-2-lite": "nano-banana-2-lite-edit",
    "gpt-image-2": "gpt-image-2-edit",
}
#: Models that accept ``image_urls`` as they are, with their reference limit.
_REFERENCE_LIMITS: dict[str, int] = {
    "nano-banana-2-lite-edit": 14,
    "gpt-image-2-edit": 16,
    "nano-banana-2.1": 10,
    "qwen-image-2.1": 10,
}


def reference_model(model: str) -> str:
    """The model that serves ``model`` with reference images, or a clear error."""
    chosen = _REFERENCE_VARIANTS.get(model, model)
    if chosen not in _REFERENCE_LIMITS:
        usable = ", ".join(sorted({*_REFERENCE_VARIANTS, *_REFERENCE_LIMITS}))
        raise ValueError(
            f"PoYo model {model!r} does not take reference images; use one of {usable}"
        )
    return chosen


DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_POLL_TIMEOUT_SECONDS = 600.0

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def _sniff_media_type(content: bytes, asset_type: AssetType) -> bool:
    """Return True when ``content`` looks like the expected media type.

    IMAGE assets are validated with the same magic-byte checks used by the
    FAL provider. VIDEO assets are validated later with ``probe_media``.
    """
    if asset_type == AssetType.IMAGE:
        try:
            _validate_image_bytes(content)
            return True
        except MediaContentError:
            return False
    # For VIDEO we only reject obviously non-binary/text error pages here;
    # the real validation happens after download via ``probe_media``.
    head = content[:16]
    return bool(content) and not head.startswith(b"<") and b"<html" not in head.lower()


def _default_aspect_ratio(request: AssetRequest) -> str:
    """Pick an ``aspect_ratio`` for models that do not take ``size``.

    Uses the request's framing (``AssetRequest.aspect``: ``width``/``height``,
    then a plan's ``aspect_ratio``), reduced to the smallest ratio; otherwise
    falls back to ``16:9`` to match the old payload default.
    """
    aspect = request.aspect()
    if aspect is None:
        return "16:9"
    return f"{aspect[0]}:{aspect[1]}"


class PoyoAIProvider(GenerativeProvider):
    """Generate images and videos via PoYo.ai's unified async API."""

    provider_name = "poyo_ai"

    #: Synthesizes images from a prompt; eligible for ``"auto"`` stills.
    generates_images = True

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self.default_model = self.config.default_model or DEFAULT_IMAGE_MODEL
        self.video_model = self.config.provider_options.get("video_model", "")
        self.poll_interval_seconds = float(
            self.config.provider_options.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
        )
        poll_timeout_seconds = float(
            self.config.provider_options.get("poll_timeout_seconds", DEFAULT_POLL_TIMEOUT_SECONDS)
        )
        self.poll_max_attempts = max(1, int(poll_timeout_seconds / self.poll_interval_seconds))
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Submit a PoYo task, poll for completion, download the result."""
        if not self.api_key:
            raise RuntimeError("PoYo API key is not configured.")

        model = self._resolve_model(request)
        references = list(request.params.get("reference_images") or [])
        image_urls: list[str] = []
        if references and request.asset_type == AssetType.IMAGE:
            image_urls = await hosted_urls(references)
        payload = self._build_payload(request, model, image_urls=image_urls)
        model = str(payload["model"])
        client = await self._get_client()

        output_path = Path(output_path)
        task_id = pending_task(output_path, payload)
        if task_id is None:
            logger.info(
                "poyo_submit",
                job_id=request.job_id,
                asset_id=request.asset_id,
                model=model,
                asset_type=request.asset_type.value,
            )
            submit_response = await client.post("/api/generate/submit", json=payload)
            submit_response.raise_for_status()
            submit_data = self._unwrap_response(submit_response.json())

            task_id = submit_data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Unexpected PoYo submit response: {submit_data}")
            record_task(output_path, payload, task_id, model=model)

        try:
            result_data = await self._poll_until_complete(client, task_id)
        except RuntimeError as exc:
            if "timed out" not in str(exc):
                clear_task(output_path)  # PoYo reported failure: nothing left to resume
            raise
        download_url = self._extract_download_url(result_data, request.asset_type)
        if not download_url:
            raise RuntimeError(f"No download URL in PoYo response: {result_data}")

        media_response = await client.get(download_url)
        media_response.raise_for_status()

        if request.asset_type == AssetType.IMAGE:
            _validate_image_bytes(media_response.content)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        save_response_bytes(media_response, output_path, max_bytes=MAX_IMAGE_BYTES)
        clear_task(output_path)

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
            "poyo_complete",
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
        """Pick the right PoYo model for the request.

        Precedence:
        1. ``request.params["model"]`` (explicit per-request override).
        2. For VIDEO: ``provider_options.video_model``.
        3. For IMAGE: ``config.default_model``.
        4. Built-in defaults.
        """
        preferred = request.params.get("model")
        if isinstance(preferred, str):
            return preferred
        if request.asset_type == AssetType.VIDEO:
            return self.video_model or DEFAULT_VIDEO_MODEL
        return self.default_model or DEFAULT_IMAGE_MODEL

    def _build_payload(
        self, request: AssetRequest, model: str, *, image_urls: list[str] | None = None
    ) -> dict[str, Any]:
        """Build the PoYo ``/api/generate/submit`` payload.

        With ``image_urls`` (hosted reference images, #318) an image request switches to the model's
        reference variant and sends them; the framing stays the base model's (``size`` on the variant).
        """
        model_kwargs: dict[str, Any] = dict(request.params.get("model_kwargs", {}))
        # ``model`` is handled at the top level; remove any accidental duplicate.
        model_kwargs.pop("model", None)

        input_data: dict[str, Any] = {"prompt": request.prompt, **model_kwargs}

        if request.asset_type == AssetType.VIDEO and model in FIXED_LENGTH_VIDEO_MODELS:
            # Wan 2.2 Fast takes only prompt, image_urls, resolution and seed (#319).
            input_data.setdefault("resolution", "480p")
        elif request.asset_type == AssetType.VIDEO:
            # Map scene duration if the caller passed ``duration_seconds``.
            if "duration" not in input_data and "duration_seconds" in request.params:
                input_data["duration"] = int(request.params["duration_seconds"])
            # Provide safe defaults so the request works without extra kwargs.
            input_data.setdefault("aspect_ratio", "16:9")
            input_data.setdefault("resolution", "720p")
            input_data.setdefault("generate_audio", False)
        elif request.asset_type == AssetType.IMAGE:
            # Two families, two framings. gpt-image takes ``size`` +
            # ``resolution`` presets; nano-banana (and friends) take
            # ``aspect_ratio`` and reject both of the others — a nano-banana
            # request carrying ``size`` fails with a misleading "resolution is
            # not supported" 400 (#265). A plan written for gpt-image carries
            # ``size`` (a preset) and ``resolution``, so for those models the
            # preset becomes the ratio and the resolution tier is dropped.
            if model in _ASPECT_RATIO_ONLY_IMAGE_MODELS:
                aspect = input_data.pop("aspect_ratio", None)
                if aspect is None:
                    aspect = input_data.pop("size", None)
                input_data.pop("resolution", None)
                input_data["aspect_ratio"] = aspect or _default_aspect_ratio(request)
            elif "aspect_ratio" not in input_data:
                input_data.setdefault("size", "16:9")
                input_data.setdefault("resolution", "1K")
            if image_urls:
                base = model
                model = reference_model(model)
                limit = _REFERENCE_LIMITS[model]
                if len(image_urls) > limit:
                    raise ValueError(
                        f"{model} takes at most {limit} reference images, got {len(image_urls)}"
                    )
                if base in _ASPECT_RATIO_ONLY_IMAGE_MODELS and model.endswith("-edit"):
                    input_data["size"] = input_data.pop(
                        "aspect_ratio"
                    )  # the edit variant documents `size`
                input_data["image_urls"] = list(image_urls)

        return {"model": model, "input": input_data}

    async def _poll_until_complete(
        self,
        client: httpx.AsyncClient,
        task_id: str,
    ) -> dict[str, Any]:
        """Poll the PoYo status endpoint until the task finishes or fails."""
        status_path = f"/api/generate/status/{task_id}"
        for attempt in range(self.poll_max_attempts):
            try:
                status_response = await client.get(status_path)
                status_response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in _RETRYABLE_STATUS_CODES:
                    logger.warning(
                        "poyo_poll_status_retryable",
                        status_code=exc.response.status_code,
                        attempt=attempt,
                        max_attempts=self.poll_max_attempts,
                    )
                    continue
                raise

            response = self._unwrap_response(status_response.json())
            status = response.get("status")
            progress = response.get("progress")

            logger.debug(
                "poyo_poll",
                attempt=attempt,
                status=status,
                progress=progress,
                max_attempts=self.poll_max_attempts,
                interval=self.poll_interval_seconds,
            )

            if status == "finished":
                return response

            if status == "failed":
                error_message = response.get("error_message") or "unknown error"
                raise RuntimeError(f"PoYo task failed: {error_message}")

            await asyncio.sleep(self.poll_interval_seconds)

        raise RuntimeError(f"PoYo task polling timed out ({task_id})")

    @staticmethod
    def _unwrap_response(data: dict[str, Any]) -> dict[str, Any]:
        """PoYo wraps every response in ``{"code": 200, "data": {...}}``.

        Raise a clear error when the top-level ``code`` is non-success.
        """
        if isinstance(data, dict):
            code = data.get("code")
            if code is not None and code != 200:
                error = data.get("error", {}).get("message") or str(data)
                raise RuntimeError(f"PoYo API error ({code}): {error}")
            if "data" in data:
                return cast(dict[str, Any], data.get("data", {}))
        return data

    @staticmethod
    def _extract_download_url(data: dict[str, Any], asset_type: AssetType) -> str:
        """Find the first matching media URL in a completed PoYo response."""
        expected_type = asset_type.value
        files = data.get("files", [])
        if isinstance(files, list):
            for file_obj in files:
                if isinstance(file_obj, dict) and file_obj.get("file_type") == expected_type:
                    return str(file_obj["file_url"])
                # Some responses may omit ``file_type``; fall back to first URL.
                if isinstance(file_obj, dict) and "file_url" in file_obj:
                    return str(file_obj["file_url"])
        # Flat response variants (defensive).
        url = data.get("file_url") or data.get("url")
        if url:
            return str(url)
        return ""

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> PoyoAIProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
