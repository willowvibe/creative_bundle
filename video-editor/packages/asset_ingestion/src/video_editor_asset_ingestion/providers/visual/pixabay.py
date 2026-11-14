"""Pixabay stock photo and video provider.

Pixabay provides a free REST API for searching curated stock photos and videos.
This provider turns a scene's visual description into a search query, picks the
first matching result, downloads the media file, and returns it as a local asset.

API documentation: https://pixabay.com/api/docs/

Supported asset types:
- ``AssetType.IMAGE`` → ``GET https://pixabay.com/api/``
- ``AssetType.VIDEO`` → ``GET https://pixabay.com/api/videos/``
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import structlog

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

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://pixabay.com/api"
DEFAULT_PER_PAGE = 5
DEFAULT_TIMEOUT = 60.0

#: The docs accept 3..200; anything else is an HTTP 400.
MIN_PER_PAGE = 3
MAX_PER_PAGE = 200
#: Pixabay's size names do not fix a resolution (``large`` is 4K on new
#: uploads, 1080p on older ones, where ``medium`` is 720p), so variants are
#: chosen by their dimensions (``pick_sharp_enough``).
VIDEO_VARIANTS = ("large", "medium", "small", "tiny")
#: On HTTP 429 the docs give ``X-RateLimit-Reset`` (seconds to the next window,
#: which is 60 s long). Wait that long once, never longer than a window.
MAX_RATE_LIMIT_WAIT = 60.0


class PixabaySearchError(RuntimeError):
    """Raised when the Pixabay search returns no usable results."""


class PixabayProvider(GenerativeProvider):
    """Fetch stock images and videos from the Pixabay API."""

    provider_name = "pixabay"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout or DEFAULT_TIMEOUT
        self.provider_options = dict(self.config.provider_options)
        self._client: httpx.AsyncClient | None = None
        # CDN downloads go through their own client so the API key, a query
        # parameter on the search client, is not sent with them.
        self._download_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                params={"key": self.api_key} if self.api_key else None,
                timeout=self.timeout,
            )
        return self._client

    async def _get_download_client(self) -> httpx.AsyncClient:
        if self._download_client is None or self._download_client.is_closed:
            self._download_client = httpx.AsyncClient(timeout=self.timeout)
        return self._download_client

    async def _search(self, url: str, params: dict[str, Any]) -> httpx.Response:
        """GET a search URL, waiting out one HTTP 429 as the docs describe."""
        client = await self._get_client()
        response = await client.get(url, params=params)
        if response.status_code == 429:
            try:
                wait = float(response.headers.get("X-RateLimit-Reset", MAX_RATE_LIMIT_WAIT))
            except ValueError:
                wait = MAX_RATE_LIMIT_WAIT
            wait = max(0.0, min(wait, MAX_RATE_LIMIT_WAIT))
            logger.warning("pixabay_rate_limited", wait_seconds=wait)
            await asyncio.sleep(wait)
            response = await client.get(url, params=params)
        response.raise_for_status()
        return response

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Search Pixabay for media matching ``request.prompt`` and download it."""
        if not self.api_key:
            raise RuntimeError("Pixabay API key is not configured.")

        query = request.prompt.strip()
        if not query:
            raise ValueError("Pixabay search requires a non-empty prompt/query.")

        params = self._build_search_params(request)

        logger.info(
            "pixabay_search",
            job_id=request.job_id,
            asset_id=request.asset_id,
            asset_type=request.asset_type.value,
            query=query,
            params=params,
        )

        search_url = self._search_url(request.asset_type)
        data = (await self._search(search_url, params)).json()

        media_info = self._select_result(
            data,
            request.asset_type,
            target_duration=requested_duration(request),
        )
        if media_info is None:
            raise PixabaySearchError(f"Pixabay returned no usable results for query {query!r}")

        download_url, attribution, metadata = media_info
        logger.info(
            "pixabay_download",
            job_id=request.job_id,
            asset_id=request.asset_id,
            url=download_url,
            attribution=attribution,
        )

        downloader = await self._get_download_client()
        media_response = await downloader.get(download_url)
        media_response.raise_for_status()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        ceiling = MAX_VIDEO_BYTES if request.asset_type == AssetType.VIDEO else MAX_IMAGE_BYTES
        save_response_bytes(media_response, output_path, max_bytes=ceiling)

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=request.asset_type,
            scene_id=request.scene_id,
            local_path=str(output_path),
            metadata={
                "provider": self.provider_name,
                "query": query,
                "search_params": params,
                "download_url": download_url,
                "attribution": attribution,
                **metadata,
            },
        )

    def _search_url(self, asset_type: AssetType) -> str:
        if asset_type == AssetType.IMAGE:
            return "/"
        if asset_type == AssetType.VIDEO:
            return "/videos/"
        raise ValueError(f"Pixabay does not support asset type {asset_type.value!r}")

    _SUPPORTED_SEARCH_KEYS = (
        "per_page",
        "orientation",
        "category",
        "min_width",
        "min_height",
        "video_type",
        "image_type",
        "safesearch",
    )

    def _build_search_params(self, request: AssetRequest) -> dict[str, Any]:
        """Merge config defaults, request params, and model_kwargs into search params."""
        params: dict[str, Any] = {"q": request.prompt, "per_page": DEFAULT_PER_PAGE}

        sources = [
            self.provider_options,
            request.params,
            request.params.get("model_kwargs", {}),
        ]
        for source in sources:
            for key in self._SUPPORTED_SEARCH_KEYS:
                if key in source:
                    params[key] = source[key]

        per_page = params.get("per_page")
        if per_page is not None:
            params["per_page"] = max(MIN_PER_PAGE, min(int(per_page), MAX_PER_PAGE))

        orientation = params.get("orientation")
        if orientation is not None and orientation not in {"horizontal", "vertical"}:
            logger.warning(
                "pixabay_invalid_orientation",
                orientation=orientation,
                valid=["horizontal", "vertical"],
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
        """Pick the best media URL and attribution from a Pixabay response.

        For VIDEO, ``target_duration`` (the scene's length) selects the
        shortest clip that covers it instead of the first search hit.

        Returns ``(download_url, attribution_dict, extra_metadata)`` or ``None``
        when the response contains no usable results.
        """
        if asset_type == AssetType.IMAGE:
            photos = data.get("hits", [])
            if not photos:
                return None
            photo = photos[0]
            download_url = (
                photo.get("largeImageURL") or photo.get("webformatURL") or photo.get("imageURL")
            )
            if not download_url:
                return None
            attribution = {
                "photographer": photo.get("user", ""),
                "photographer_url": _profile_url(photo),
                "photo_url": photo.get("pageURL", ""),
                "photo_id": photo.get("id"),
            }
            metadata = {
                "width": photo.get("imageWidth"),
                "height": photo.get("imageHeight"),
                "tags": photo.get("tags", ""),
            }
            return str(download_url), attribution, metadata

        if asset_type == AssetType.VIDEO:
            videos = data.get("hits", [])
            if not videos:
                return None
            video = choose_by_duration(videos, target=target_duration) or videos[0]
            min_short_side = int(
                self.provider_options.get("min_short_side", DEFAULT_MIN_SHORT_SIDE)
            )
            variant = _pick_variant(video.get("videos", {}), min_short_side)
            if variant is None:
                return None
            download_url = variant["url"]
            attribution = {
                "videographer": video.get("user", ""),
                "videographer_url": _profile_url(video),
                "video_url": video.get("pageURL", ""),
                "video_id": video.get("id"),
            }
            metadata = {
                "width": variant.get("width"),
                "height": variant.get("height"),
                "duration": video.get("duration"),
                "tags": video.get("tags", ""),
            }
            return str(download_url), attribution, metadata

        return None

    async def close(self) -> None:
        for client in (self._client, self._download_client):
            if client and not client.is_closed:
                await client.aclose()

    async def __aenter__(self) -> PixabayProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def _profile_url(hit: dict[str, Any]) -> str:
    """The author's Pixabay profile page (``userImageURL`` is their avatar)."""
    user, user_id = hit.get("user"), hit.get("user_id")
    if not user or not user_id:
        return ""
    return f"https://pixabay.com/users/{user}-{user_id}/"


def _pick_variant(files: dict[str, Any], min_short_side: int) -> dict[str, Any] | None:
    """The best Pixabay size variant; a missing ``large`` has an empty url, so skip it."""
    usable = [v for key in VIDEO_VARIANTS if isinstance(v := files.get(key), dict) and v.get("url")]
    return pick_sharp_enough(usable, min_short_side)
