"""Openverse audio provider: the fallback index for free sound effects.

Openverse aggregates CC-licensed audio (Freesound, Wikimedia, ...). The API
filter asks for commercial + modification use, and the result's own licence is
checked again before download: CC0, Public Domain Mark and CC BY only. NC, ND
and (by default) ShareAlike are refused — the engine's videos are commercial
and the series does not want a viral licence.

Anonymous requests work; a token (`OPENVERSE_API_KEY`) raises the rate limit.
Openverse is an index, not the licensor: the manifest keeps the source page and
the licence URL for the description credits.
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
    MAX_AUDIO_BYTES,
    assert_public_https,
    new_download_client,
    stream_download,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.openverse.org"
SEARCH_PATH = "/v1/audio/"
#: The API-side filter; the result's licence is checked again below.
LICENSE_TYPE = "commercial,modification"
#: Result licences we accept. ``by-sa`` matches the API filter but is excluded.
ACCEPTED_LICENSES = frozenset({"cc0", "pdm", "by"})
MAX_RESULTS = 5
DEFAULT_MAX_DURATION = 3.0


class OpenverseSFXProvider(GenerativeProvider):
    """Search Openverse audio and download the first acceptable preview."""

    provider_name = "openverse_sfx"
    consumes_image_quota = False
    generates_images = False

    def __init__(
        self,
        config: ProviderConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        download_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self._client = client
        #: Separate, unauthenticated client for the media host (#300).
        self._download_client = download_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
                transport=httpx.AsyncHTTPTransport(retries=2),
            )
        return self._client

    async def _get_download_client(self) -> httpx.AsyncClient:
        if self._download_client is None or self._download_client.is_closed:
            self._download_client = new_download_client(self.timeout)
        return self._download_client

    async def aclose(self) -> None:
        for client in (self._client, self._download_client):
            if client is not None and not client.is_closed:
                await client.aclose()

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        query = request.prompt.strip()
        if not query:
            raise RuntimeError("Openverse needs a search prompt")

        client = await self._get_client()
        response = await client.get(
            SEARCH_PATH,
            params={
                "q": query,
                "license_type": LICENSE_TYPE,
                "page": _request_page(request),
                "page_size": MAX_RESULTS,
            },
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        chosen = _first_acceptable(results, _max_ms(request))
        if chosen is None:
            raise RuntimeError(
                f"Openverse found no CC0/PDM/CC BY sound for {query!r} (NC, ND and SA are excluded)"
            )

        audio_url = str(chosen.get("url") or "")
        if not audio_url:
            raise RuntimeError(f"Openverse result {chosen.get('id')!r} has no audio URL")
        # The token belongs to the API host only; the audio URL points at
        # whatever host Openverse indexed (#300).
        try:
            assert_public_https(audio_url)
        except ValueError as exc:
            raise RuntimeError(f"Openverse audio URL refused: {exc}") from exc
        download = await self._get_download_client()
        await stream_download(
            download,
            audio_url,
            output_path,
            max_bytes=MAX_AUDIO_BYTES,
            validate_audio=True,
        )

        duration_ms = int(chosen.get("duration") or 0)
        if duration_ms <= 0:
            try:
                duration = probe_media(output_path).duration
                duration_ms = int(round((duration or 0.0) * 1000))
            except Exception as exc:  # noqa: BLE001 - metadata must not fail the download
                logger.warning("openverse_probe_failed", path=str(output_path), error=str(exc))

        logger.info(
            "openverse_sfx_downloaded",
            job_id=request.job_id,
            asset_id=request.asset_id,
            provider=chosen.get("provider"),
            license=chosen.get("license"),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SFX,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "title": chosen.get("title", ""),
                "creator": chosen.get("creator", ""),
                "source_url": chosen.get("foreign_landing_url", ""),
                "license": _license_label(chosen),
                "license_url": chosen.get("license_url", ""),
                "provider": chosen.get("provider", ""),
            },
        )


def _first_acceptable(results: list[dict[str, Any]], max_ms: int) -> dict[str, Any] | None:
    acceptable = [
        result
        for result in results
        if str(result.get("license") or "").lower() in ACCEPTED_LICENSES
    ]
    if not acceptable:
        return None
    within = [result for result in acceptable if 0 < int(result.get("duration") or 0) <= max_ms]
    if within:
        return within[0]
    # No short match: take the shortest acceptable rather than a 30 s bed.
    return min(acceptable, key=lambda result: int(result.get("duration") or 10_000_000))


def _request_page(request: AssetRequest) -> int:
    try:
        page = int(request.params.get("page", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def _max_ms(request: AssetRequest) -> int:
    raw = request.params.get("duration_seconds", DEFAULT_MAX_DURATION)
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_MAX_DURATION
    seconds = min(max(seconds, 0.25), 10.0)
    return int(seconds * 1500)


def _license_label(result: dict[str, Any]) -> str:
    licence = str(result.get("license") or "").upper()
    version = str(result.get("license_version") or "")
    return f"{licence} {version}".strip()
