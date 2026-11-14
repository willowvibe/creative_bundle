"""Coverr stock footage provider.

Coverr provides free cinematic HD stock footage via a REST API.
API docs: https://api.coverr.co/docs
No API key required for the free tier.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers._media import MAX_VIDEO_BYTES, save_response_bytes
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.coverr.co"
DEFAULT_TIMEOUT = 60.0


class CoverrProvider(GenerativeProvider):
    """Fetch stock videos from the Coverr API."""

    provider_name = "coverr"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._base_url = DEFAULT_BASE_URL
        self._api_key: str | None = None
        if config:
            self._api_key = config.api_key()
            self._base_url = getattr(config, "base_url", None) or DEFAULT_BASE_URL

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        query = request.params.get("query", "") if request.params else ""
        if not query:
            query = request.prompt or "nature"

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(
                f"{self._base_url}/videos/search",
                params={"query": query, "per_page": 5},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        hits = data.get("hits", [])
        if not hits:
            raise RuntimeError(f"Coverr: no results for query {query!r}")

        # Pick the first result
        best = hits[0]
        video_url = best.get("urls", {}).get("mp4") or best.get("url")
        if not video_url:
            raise RuntimeError("Coverr: no downloadable URL in result")

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            dl_resp = await client.get(video_url)
            dl_resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            save_response_bytes(dl_resp, output_path, max_bytes=MAX_VIDEO_BYTES)

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.VIDEO,
            scene_id=request.scene_id,
            local_path=str(output_path),
            metadata={
                "provider": self.provider_name,
                "query": query,
                "source_url": video_url,
                "title": best.get("title", ""),
            },
        )
