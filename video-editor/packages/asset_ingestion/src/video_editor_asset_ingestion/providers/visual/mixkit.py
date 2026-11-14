"""Mixkit stock footage provider.

Mixkit provides free HD stock videos with no API key required.
License: free for commercial and non-commercial use, no attribution.
Website: https://mixkit.co
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

DEFAULT_BASE_URL = "https://mixkit.co/api"
DEFAULT_TIMEOUT = 60.0


class MixkitProvider(GenerativeProvider):
    """Fetch stock videos from Mixkit (no API key required)."""

    provider_name = "mixkit"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config
        self._base_url = DEFAULT_BASE_URL

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        query = request.params.get("query", "") if request.params else ""
        if not query:
            query = request.prompt or "nature"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            # Mixkit free API endpoint
            resp = await client.get(
                f"{self._base_url}/videos/",
                params={"q": query, "limit": 5},
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        if not results:
            raise RuntimeError(f"Mixkit: no results for query {query!r}")

        best = results[0]
        video_url = best.get("urls", {}).get("mp4") or best.get("download_url")
        if not video_url:
            raise RuntimeError("Mixkit: no downloadable URL in result")

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
