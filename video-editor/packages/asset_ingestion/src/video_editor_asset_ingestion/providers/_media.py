"""Shared media-download validation helpers."""

from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx


def new_download_client(timeout: float) -> httpx.AsyncClient:
    """An unauthenticated client for fetching media from a third-party host.

    Never attach provider credentials to this client: the URL comes from an
    index or search result, so its host is not the API host (#300). Redirects
    are followed because CDNs commonly redirect to a signed URL, and every
    redirect target is re-checked so a public host cannot bounce the request to
    a private address.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        transport=httpx.AsyncHTTPTransport(retries=2),
        event_hooks={"request": [guard_redirect]},
    )


async def guard_redirect(request: httpx.Request) -> None:
    """Validate every outgoing hop, including each redirect target.

    Only the first URL is checked by the caller; without this hook a public
    HTTPS host could redirect to loopback, a private range or a metadata
    endpoint. httpx runs request hooks for the initial request and for every
    redirect, and awaits them.
    """
    assert_public_https(str(request.url))


def assert_public_https(url: str) -> None:
    """Refuse a download URL that is not public HTTPS (#300).

    Blocks plain HTTP, loopback, private, link-local and reserved addresses.
    Every address a hostname resolves to is checked. DNS rebinding between this
    check and the request is out of scope; the media host is still an index
    result, not attacker-authored input.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"media URL must use https, got {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise ValueError("media URL has no host")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as exc:
        raise ValueError(f"media host {host!r} cannot be resolved: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(f"media host {host!r} resolves to a non-public address ({address})")


class MediaContentError(RuntimeError):
    """Raised when a generative-media endpoint returns non-media content."""


def _is_audio_head(head: bytes, content_type: str) -> bool:
    """Return True when ``head`` (first bytes) looks like audio for ``content_type``."""
    if content_type and content_type.startswith("audio/"):
        return True

    # Also sniff bodies served with generic or missing content types.
    if content_type and content_type not in ("application/octet-stream", "binary/octet-stream"):
        return False

    if not head:
        return False

    audio_signatures = (
        b"ID3",
        b"\xff\xfb",
        b"\xff\xf3",
        b"\xff\xf2",
        b"RIFF",
        b"OggS",
        b"fLaC",
        b"FORM",
    )
    if any(head.startswith(sig) for sig in audio_signatures):
        return True
    # MP4/M4A stores the box size in the first 4 bytes and the ``ftyp``
    # signature at offset 4, so ``head.startswith(b"ftyp")`` would reject
    # every real MP4 audio file. Check the brand box region instead.
    return b"ftyp" in head[4:16]


def _is_audio_content(response: httpx.Response) -> bool:
    raw_content_type: str = response.headers.get("content-type", "")
    content_type = raw_content_type.lower().split(";")[0].strip()
    return _is_audio_head(response.content[:16], content_type)


#: Per-asset ceilings. A provider that returns more than this is refused rather
#: than buffered/written whole (#305).
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 500 * 1024 * 1024


class MediaTooLargeError(MediaContentError):
    """A media response exceeded its per-asset byte ceiling."""


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_media_bytes(data: bytes, output_path: str | Path, *, max_bytes: int) -> None:
    """Write generated media atomically, refusing a body above ``max_bytes``."""
    if len(data) > max_bytes:
        raise MediaTooLargeError(
            f"media body is {len(data)} bytes, above the {max_bytes}-byte ceiling"
        )
    _atomic_write(Path(output_path), data)


def save_response_bytes(
    response: httpx.Response, output_path: str | Path, *, max_bytes: int
) -> None:
    """``save_media_bytes`` for an already-buffered HTTP response (#305)."""
    save_media_bytes(response.content, output_path, max_bytes=max_bytes)


def save_audio_response(response: httpx.Response, output_path: str) -> None:
    """Save an audio HTTP response to disk after validating its content.

    Bug #69 regression: audio endpoints sometimes return JSON or HTML
    error pages (e.g. during outages) with HTTP 200. Writing those bytes
    to ``.mp3`` produces an opaque ffmpeg failure later. This helper raises
    ``MediaContentError`` with a content snippet so the real provider
    message reaches the operator. Oversized bodies are refused (#305).
    """
    if not response.content:
        raise MediaContentError(
            f"Expected non-empty audio response, got content-type "
            f"'{response.headers.get('content-type', 'unknown')}' with empty body"
        )
    if len(response.content) > MAX_AUDIO_BYTES:
        raise MediaTooLargeError(
            f"Audio response is {len(response.content)} bytes, above the "
            f"{MAX_AUDIO_BYTES}-byte ceiling"
        )

    if not _is_audio_content(response):
        snippet = response.text[:500] or response.content[:500].decode("utf-8", errors="replace")
        raise MediaContentError(
            f"Expected audio response, got content-type "
            f"'{response.headers.get('content-type', 'unknown')}' "
            f"with body snippet: {snippet!r}"
        )

    _atomic_write(Path(output_path), response.content)


async def stream_download(
    client: httpx.AsyncClient,
    url: str,
    output_path: str | Path,
    *,
    max_bytes: int,
    validate_audio: bool = False,
) -> int:
    """Stream ``url`` to ``output_path`` with a byte ceiling, then ``os.replace``.

    The body is never held whole in memory. A partial file is removed on any
    failure, so a caller never sees a truncated asset (#305).
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    total = 0
    content_type = ""
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower().split(";")[0].strip()
            with tmp.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaTooLargeError(
                            f"{url} is above the {max_bytes}-byte ceiling for this asset type"
                        )
                    handle.write(chunk)
        if total == 0:
            raise MediaContentError(
                f"Expected non-empty media response from {url}, got content-type "
                f"'{content_type or 'unknown'}' with empty body"
            )
        if validate_audio:
            with tmp.open("rb") as handle:
                head = handle.read(16)
            if not _is_audio_head(head, content_type):
                raise MediaContentError(
                    f"Expected audio from {url}, got content-type '{content_type or 'unknown'}'"
                )
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return total
