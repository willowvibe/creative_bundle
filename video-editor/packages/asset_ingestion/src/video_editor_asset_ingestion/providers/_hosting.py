"""Public URLs for local reference images (#318, P4.8).

PoYo fetches reference images by URL. Local files (a character sheet, a style frame, the still being edited)
are uploaded once to fal's CDN storage, the host the drama episode scripts already used, and the URL is
cached by content hash so a re-run never uploads the same bytes twice. ``http(s)`` references pass through.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger(__name__)

FAL_UPLOAD_URL = "https://rest.alpha.fal.ai/storage/upload/initiate?storage_type=fal-cdn-v3"
#: Re-upload after this long, in case the CDN has expired the file.
CACHE_SECONDS = 7 * 24 * 3600
_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class HostingError(RuntimeError):
    """A reference image is missing, of an unsupported type, or could not be uploaded."""


def _cache_file() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "video-editor" / "hosted_refs.json"


def _load_cache() -> dict[str, dict[str, object]]:
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache: dict[str, dict[str, object]]) -> None:
    path = _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def _fal_key() -> str:
    key = os.environ.get("FAL_KEY", "").strip().strip("'\"")
    if not key:
        raise HostingError(
            "reference images need FAL_KEY (fal storage hosts them for PoYo); set it in .env"
        )
    return key


async def _upload(path: Path, content_type: str) -> str:
    async with httpx.AsyncClient(timeout=180) as client:
        init = await client.post(
            FAL_UPLOAD_URL,
            headers={"Authorization": f"Key {_fal_key()}"},
            json={"content_type": content_type, "file_name": path.name},
        )
        init.raise_for_status()
        data = init.json()
        put = await client.put(
            data["upload_url"], content=path.read_bytes(), headers={"Content-Type": content_type}
        )
        put.raise_for_status()
        return str(data["file_url"])


async def hosted_urls(references: Sequence[str]) -> list[str]:
    """One fetchable URL per reference: URLs pass through, local images are uploaded (cached)."""
    cache = _load_cache()
    urls: list[str] = []
    dirty = False
    for reference in references:
        if reference.startswith(("http://", "https://")):
            urls.append(reference)
            continue
        path = Path(reference).expanduser()
        if not path.is_file():
            raise HostingError(f"reference image {reference!r} does not exist")
        content_type = _TYPES.get(path.suffix.lower())
        if content_type is None:
            raise HostingError(f"reference image {reference!r} must be PNG, JPEG or WebP")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        hit = cache.get(digest)
        if hit and time.time() - float(str(hit.get("at", 0))) < CACHE_SECONDS:
            urls.append(str(hit["url"]))
            continue
        url = await _upload(path, content_type)
        logger.info("reference_image_hosted", path=str(path), url=url)
        cache[digest] = {"url": url, "at": time.time()}
        dirty = True
        urls.append(url)
    if dirty:
        _save_cache(cache)
    return urls
