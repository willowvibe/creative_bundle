"""Media download ceilings and atomic writes (#305)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.providers import _media
from video_editor_asset_ingestion.providers._media import (
    MediaContentError,
    MediaTooLargeError,
    save_media_bytes,
    stream_download,
)


def test_stream_download_aborts_over_the_cap_and_leaves_no_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"12345", headers={"content-type": "audio/mpeg"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> None:
        await stream_download(
            client,
            "https://example.test/a.mp3",
            tmp_path / "a.mp3",
            max_bytes=4,
            validate_audio=True,
        )

    with pytest.raises(MediaTooLargeError):
        asyncio.run(run())
    assert not (tmp_path / "a.mp3").exists()
    assert not (tmp_path / "a.mp3.part").exists()


def test_stream_download_writes_atomically(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    output = tmp_path / "sfx.mp3"
    written = asyncio.run(
        stream_download(
            client,
            "https://example.test/a.mp3",
            output,
            max_bytes=64,
            validate_audio=True,
        )
    )

    assert written == 4
    assert output.read_bytes() == b"\xff\xfb\x90\x00"
    assert not output.with_suffix(".mp3.part").exists()


def test_stream_download_rejects_non_audio(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>oops</html>", headers={"content-type": "text/html"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(MediaContentError, match="Expected audio"):
        asyncio.run(
            stream_download(
                client,
                "https://example.test/a.mp3",
                tmp_path / "a.mp3",
                max_bytes=64,
                validate_audio=True,
            )
        )


def test_save_media_bytes_refuses_an_oversize_body(tmp_path: Path) -> None:
    with pytest.raises(MediaTooLargeError):
        save_media_bytes(b"12345", tmp_path / "x.png", max_bytes=4)
    assert not (tmp_path / "x.png").exists()


def test_save_audio_response_refuses_an_oversize_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_media, "MAX_AUDIO_BYTES", 4)
    response = httpx.Response(
        200, content=b"\xff\xfb\x90\x00\x00", headers={"content-type": "audio/mpeg"}
    )
    with pytest.raises(MediaTooLargeError):
        _media.save_audio_response(response, str(tmp_path / "a.mp3"))
    assert not (tmp_path / "a.mp3").exists()


def test_the_redirect_guard_rejects_a_private_target(tmp_path: Path) -> None:
    """A public host must not redirect the download to loopback (SSRF gap)."""
    from video_editor_asset_ingestion.providers._media import guard_redirect

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
        return httpx.Response(200, content=b"secret")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [guard_redirect]},
    )
    with pytest.raises(ValueError, match="non-public address"):
        asyncio.run(
            stream_download(
                client,
                "https://93.184.216.34/a.mp3",
                tmp_path / "a.mp3",
                max_bytes=64,
            )
        )
    assert not (tmp_path / "a.mp3").exists()


def test_the_redirect_guard_allows_a_public_target(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers._media import guard_redirect

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"location": "https://93.184.216.35/final.mp3"})
        return httpx.Response(
            200, content=b"\xff\xfb\x90\x00", headers={"content-type": "audio/mpeg"}
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [guard_redirect]},
    )
    written = asyncio.run(
        stream_download(
            client,
            "https://93.184.216.34/a.mp3",
            tmp_path / "a.mp3",
            max_bytes=64,
            validate_audio=True,
        )
    )
    assert written == 4
    assert (tmp_path / "a.mp3").read_bytes() == b"\xff\xfb\x90\x00"
