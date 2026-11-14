"""Freesound sound-effect provider.

Searches Freesound's APIv2 for CC0 or CC BY sounds and downloads the HQ MP3
preview. Previews need no OAuth2, which is what keeps this tier free and
simple; original-quality downloads would require a user token. The search
caps results at the request's duration (a cue is short) and maps the returned
Creative Commons URL to a readable label for the credits.

The API is rate-limited (60 requests/minute, 2,000/day) and is an index of
user-uploaded sounds: every result carries its own licence, so the caller
records ``license``/``url``/``username`` in the job manifest and emits
attribution for CC BY.
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
    new_download_client,
    stream_download,
)
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://freesound.org"
SEARCH_PATH = "/apiv2/search/text/"
#: CC0 and CC-BY only; CC-BY-NC is excluded because these videos are commercial.
LICENSE_FILTER = 'license:("Creative Commons 0" OR "Attribution")'
FIELDS = "id,name,username,license,previews,url,duration"
MAX_RESULTS = 5
DEFAULT_MAX_DURATION = 3.0

#: Freesound returns the licence as a Creative Commons URL; the manifest
#: wants a readable label plus the URL. Only these URLs may ship: CC0, Public
#: Domain Mark and CC BY 3.0/4.0, both schemes (#301). NC, ND and SA are out.
_LICENSE_URLS: frozenset[str] = frozenset(
    {
        "https://creativecommons.org/publicdomain/zero/1.0/",
        "http://creativecommons.org/publicdomain/zero/1.0/",
        "https://creativecommons.org/publicdomain/mark/1.0/",
        "http://creativecommons.org/publicdomain/mark/1.0/",
        "https://creativecommons.org/licenses/by/3.0/",
        "http://creativecommons.org/licenses/by/3.0/",
        "https://creativecommons.org/licenses/by/4.0/",
        "http://creativecommons.org/licenses/by/4.0/",
    }
)
_LICENSE_LABELS: dict[str, str] = {
    "https://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
    "http://creativecommons.org/publicdomain/zero/1.0/": "CC0 1.0",
    "https://creativecommons.org/publicdomain/mark/1.0/": "Public Domain Mark 1.0",
    "http://creativecommons.org/publicdomain/mark/1.0/": "Public Domain Mark 1.0",
    "https://creativecommons.org/licenses/by/3.0/": "CC BY 3.0",
    "http://creativecommons.org/licenses/by/3.0/": "CC BY 3.0",
    "https://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
    "http://creativecommons.org/licenses/by/4.0/": "CC BY 4.0",
}


class FreesoundSFXProvider(GenerativeProvider):
    """Search and download a CC0/CC-BY preview as the SFX clip."""

    provider_name = "freesound_sfx"
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
        #: Separate client for the preview CDN so the API token header never
        #: reaches it (#301).
        self._download_client = download_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
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
        """Find one CC0/CC-BY sound for ``request.prompt`` and save its preview."""
        if not self.api_key:
            raise RuntimeError(
                "Freesound API key is not configured; set FREESOUND_API_KEY "
                "or the provider's api_key_env"
            )
        query = request.prompt.strip()
        if not query:
            raise RuntimeError("Freesound needs a search prompt")

        duration = _request_duration(request)
        client = await self._get_client()
        response = await client.get(
            SEARCH_PATH,
            # Header auth keeps the key out of URLs and INFO logs (#301).
            headers={"Authorization": f"Token {self.api_key}"},
            params={
                "query": query,
                "filter": _filter_for(duration),
                "fields": FIELDS,
                "page": _request_page(request),
                "page_size": MAX_RESULTS,
            },
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise RuntimeError(f"Freesound found no CC0/CC BY sound for {query!r}")

        match = _first_acceptable(results, int(duration * 1000))
        if match is None:
            raise RuntimeError(
                f"Freesound returned no CC0/CC BY sound within {duration:g}s "
                f"for {query!r} (NC, ND and SA are excluded)"
            )
        chosen, preview_url = match

        download = await self._get_download_client()
        await stream_download(
            download,
            preview_url,
            output_path,
            max_bytes=MAX_AUDIO_BYTES,
            validate_audio=True,
        )

        duration_ms = int(round(float(chosen.get("duration") or 0.0) * 1000))
        if duration_ms <= 0:
            try:
                probed = probe_media(output_path).duration
                duration_ms = int(round((probed or 0.0) * 1000))
            except Exception as exc:  # noqa: BLE001 - metadata must not fail the download
                logger.warning("freesound_probe_failed", path=str(output_path), error=str(exc))

        logger.info(
            "freesound_sfx_downloaded",
            job_id=request.job_id,
            asset_id=request.asset_id,
            sound_id=chosen.get("id"),
            license=chosen.get("license"),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SFX,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "title": chosen.get("name", ""),
                "creator": chosen.get("username", ""),
                "source_url": chosen.get("url", ""),
                "license": _license_label(str(chosen.get("license") or "")),
                "license_url": chosen.get("license", ""),
            },
        )


def _first_acceptable(
    results: list[dict[str, Any]], max_ms: int
) -> tuple[dict[str, Any], str] | None:
    """Return the first result whose licence and duration both fit (#301).

    The server-side Solr filter is only a hint: re-check the licence URL and
    duration here, and keep looking instead of trusting ``results[0]``.
    """
    for result in results:
        license_url = str(result.get("license") or "").strip()
        if license_url not in _LICENSE_URLS:
            continue
        try:
            duration_ms = int(round(float(result.get("duration") or 0.0) * 1000))
        except (TypeError, ValueError):
            continue
        if duration_ms <= 0 or duration_ms > max_ms:
            continue
        previews = result.get("previews") or {}
        preview_url = (
            previews.get("preview-hq-mp3")
            or previews.get("preview-lq-mp3")
            or previews.get("preview-hq-ogg")
        )
        if preview_url:
            return result, str(preview_url)
    return None


def _request_page(request: AssetRequest) -> int:
    try:
        page = int(request.params.get("page", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def _request_duration(request: AssetRequest) -> float:
    raw = request.params.get("duration_seconds", DEFAULT_MAX_DURATION)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_DURATION
    return min(max(value, 0.25), 10.0)


def _filter_for(duration_seconds: float) -> str:
    # Freesound's Solr filter uses whitespace as AND; the OR stays grouped.
    return f"{LICENSE_FILTER} duration:[0 TO {duration_seconds:g}]"


def _license_label(value: str) -> str:
    return _LICENSE_LABELS.get(value, value)
