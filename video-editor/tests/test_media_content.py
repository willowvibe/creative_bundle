"""Regression tests for ``save_audio_response`` content validation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.providers._media import (
    MediaContentError,
    save_audio_response,
)


def _mp3_frame() -> bytes:
    """Return a minimal valid MP3 frame header."""
    return b"\xff\xfb\x90\x00" + b"\x00" * 36


def test_save_audio_response_rejects_empty_audio_content_type(
    tmp_path: Path,
) -> None:
    """Bug #171: an empty body with an audio content-type must be rejected."""
    response = httpx.Response(
        200,
        content=b"",
        headers={"content-type": "audio/mpeg"},
    )
    output = tmp_path / "out.mp3"
    with pytest.raises(MediaContentError, match="non-empty audio response"):
        save_audio_response(response, str(output))
    assert not output.exists()


def test_save_audio_response_accepts_octet_stream_with_audio_signature(
    tmp_path: Path,
) -> None:
    """Bug #172: a generic content type is accepted when the body is audio."""
    body = _mp3_frame()
    response = httpx.Response(
        200,
        content=body,
        headers={"content-type": "application/octet-stream"},
    )
    output = tmp_path / "out.mp3"
    save_audio_response(response, str(output))
    assert output.read_bytes() == body


def test_save_audio_response_rejects_octet_stream_without_audio_signature(
    tmp_path: Path,
) -> None:
    """A generic content type with non-audio bytes is still rejected."""
    response = httpx.Response(
        200,
        content=b"not an audio file",
        headers={"content-type": "application/octet-stream"},
    )
    output = tmp_path / "out.mp3"
    with pytest.raises(MediaContentError, match="Expected audio response"):
        save_audio_response(response, str(output))


def test_save_audio_response_accepts_audio_content_type(
    tmp_path: Path,
) -> None:
    """A declared audio/* content type with a body is accepted."""
    body = _mp3_frame()
    response = httpx.Response(
        200,
        content=body,
        headers={"content-type": "audio/mpeg"},
    )
    output = tmp_path / "out.mp3"
    save_audio_response(response, str(output))
    assert output.read_bytes() == body
