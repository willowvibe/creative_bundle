"""Pexels stock photo and video provider.

Pexels provides a free REST API for searching curated stock photos and videos.
This provider turns a scene's visual description into a search query, picks the
first matching result, downloads the media file, and returns it as a local asset.

API documentation: https://www.pexels.com/api/documentation/

Supported asset types:
- ``AssetType.IMAGE`` → ``GET https://api.pexels.com/v1/search``
- ``AssetType.VIDEO`` → ``GET https://api.pexels.com/videos/search``

Search parameters can be supplied in three ways, with later sources overriding
earlier ones:
1. Provider-level ``provider_options`` in ``config.yaml`` (defaults).
2. Per-request ``params`` from the scene plan / asset request.
3. Per-request ``params["model_kwargs"]`` (highest priority).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import (
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    save_response_bytes,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.visual._selection import (
    DEFAULT_MIN_SHORT_SIDE,
    choose_by_duration,
    pick_sharp_enough,
    requested_duration,
)
from video_editor_asset_ingestion.providers.visual.fal_ai import _validate_image_bytes

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.pexels.com"
DEFAULT_PER_PAGE = 5
DEFAULT_TIMEOUT = 60.0

# Pexels API supports these orientations for both photos and videos.
_VALID_ORIENTATIONS = {"landscape", "portrait", "square"}

# Image size aliases map to Pexels ``src`` keys.
_IMAGE_SIZE_KEYS = {
    "large": "large2x",  # best available large photo
    "medium": "medium",
    "small": "small",
    "original": "original",
}

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class PexelsSearchError(RuntimeError):
    """Raised when the Pexels search returns no usable results."""


class PexelsProvider(GenerativeProvider):
    """Fetch stock images and videos from the Pexels API."""

    provider_name = "pexels"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout or DEFAULT_TIMEOUT
        self.provider_options = dict(self.config.provider_options)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = str(self.api_key)
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Search Pexels for media matching ``request.prompt`` and download it."""
        if not self.api_key:
            raise RuntimeError("Pexels API key is not configured.")

        query = request.prompt.strip()
        if not query:
            raise ValueError("Pexels search requires a non-empty prompt/query.")

        params = self._build_search_params(request)
        client = await self._get_client()

        logger.info(
            "pexels_search",
            job_id=request.job_id,
            asset_id=request.asset_id,
            asset_type=request.asset_type.value,
            query=query,
            params=params,
        )

        search_url = self._search_url(request.asset_type)
        response = await client.get(search_url, params=params)
        response.raise_for_status()
        data = response.json()

        media_info = self._select_result(
            data,
            request.asset_type,
            target_duration=requested_duration(request),
        )
        if media_info is None:
            raise PexelsSearchError(f"Pexels returned no usable results for query {query!r}")

        download_url, attribution, metadata = media_info
        logger.info(
            "pexels_download",
            job_id=request.job_id,
            asset_id=request.asset_id,
            url=download_url,
            attribution=attribution,
        )

        media_response = await client.get(download_url)
        media_response.raise_for_status()

        if request.asset_type == AssetType.IMAGE:
            _validate_image_bytes(media_response.content)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ceiling = MAX_VIDEO_BYTES if request.asset_type == AssetType.VIDEO else MAX_IMAGE_BYTES
        save_response_bytes(media_response, output_path, max_bytes=ceiling)

        duration_ms = 0
        if request.asset_type == AssetType.VIDEO:
            try:
                info = probe_media(output_path)
                duration_ms = info.duration_ms()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "pexels_probe_failed",
                    job_id=request.job_id,
                    asset_id=request.asset_id,
                    path=str(output_path),
                    error=str(exc),
                )

        result_metadata: dict[str, Any] = {
            "provider": self.provider_name,
            "query": query,
            "search_params": params,
            "download_url": download_url,
            "attribution": attribution,
            **metadata,
        }
        if duration_ms:
            result_metadata["duration_ms"] = duration_ms

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata=result_metadata,
        )

    def _search_url(self, asset_type: AssetType) -> str:
        if asset_type == AssetType.IMAGE:
            return "/v1/search"
        if asset_type == AssetType.VIDEO:
            return "/videos/search"
        raise ValueError(f"Pexels does not support asset type {asset_type.value!r}")

    _SUPPORTED_SEARCH_KEYS = ("orientation", "per_page", "page", "size", "color", "locale")

    def _build_search_params(self, request: AssetRequest) -> dict[str, Any]:
        """Merge config defaults, request params, and model_kwargs into search params."""
        params: dict[str, Any] = {"query": request.prompt, "per_page": DEFAULT_PER_PAGE}

        sources = [
            self.provider_options,
            request.params,
            request.params.get("model_kwargs", {}),
        ]
        for source in sources:
            for key in self._SUPPORTED_SEARCH_KEYS:
                if key in source:
                    params[key] = source[key]

        # Normalize values.
        per_page = params.get("per_page")
        if per_page is not None:
            params["per_page"] = max(1, min(int(per_page), 80))

        orientation = params.get("orientation")
        if orientation is not None and orientation not in _VALID_ORIENTATIONS:
            logger.warning(
                "pexels_invalid_orientation",
                orientation=orientation,
                valid=list(_VALID_ORIENTATIONS),
            )
            del params["orientation"]

        return params

    def _select_result(
        self,
        data: dict[str, Any],
        asset_type: AssetType,
        *,
        target_duration: float | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        """Pick the best media URL and attribution from a Pexels response.

        For VIDEO, ``target_duration`` (the scene's length) selects the
        shortest clip that covers it instead of the first search hit.

        Returns ``(download_url, attribution_dict, extra_metadata)`` or ``None``
        when the response contains no usable results.
        """
        if asset_type == AssetType.IMAGE:
            photos = data.get("photos", [])
            if not photos:
                return None
            photo = photos[0]
            src = photo.get("src", {})
            size_key = _IMAGE_SIZE_KEYS.get(self.provider_options.get("size", "large"), "large2x")
            download_url = src.get(size_key) or src.get("original") or src.get("large")
            if not download_url:
                return None
            attribution = {
                "photographer": photo.get("photographer", ""),
                "photographer_url": photo.get("photographer_url", ""),
                "photo_url": photo.get("url", ""),
                "photo_id": photo.get("id"),
            }
            metadata = {
                "width": photo.get("width"),
                "height": photo.get("height"),
                "avg_color": photo.get("avg_color"),
            }
            return str(download_url), attribution, metadata

        if asset_type == AssetType.VIDEO:
            videos = data.get("videos", [])
            if not videos:
                return None
            video = choose_by_duration(videos, target=target_duration) or videos[0]
            files = video.get("video_files", [])
            mp4_files = [
                f for f in files if isinstance(f, dict) and "mp4" in f.get("file_type", "")
            ]
            if not mp4_files:
                return None

            min_short_side = int(
                self.provider_options.get("min_short_side", DEFAULT_MIN_SHORT_SIDE)
            )
            best_file = pick_sharp_enough(mp4_files, min_short_side) or mp4_files[0]
            download_url = best_file.get("link")
            if not download_url:
                return None
            attribution = {
                "videographer": video.get("user", {}).get("name", ""),
                "videographer_url": video.get("user", {}).get("url", ""),
                "video_url": video.get("url", ""),
                "video_id": video.get("id"),
            }
            metadata = {
                "width": video.get("width"),
                "height": video.get("height"),
                "duration": video.get("duration"),
                "quality": best_file.get("quality"),
                "file_width": best_file.get("width"),
                "file_height": best_file.get("height"),
            }
            return str(download_url), attribution, metadata

        return None

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> PexelsProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
