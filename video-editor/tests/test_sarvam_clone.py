"""Sarvam voice-clone management (create/list/delete) (#312)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.providers.tts.sarvam_clone import (
    SarvamCloneError,
    create_voice,
    delete_voice,
    list_voices,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.sarvam.ai"
    )


@pytest.mark.asyncio
async def test_create_voice_posts_the_reference_as_multipart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    audio = tmp_path / "reference.wav"
    audio.write_bytes(b"RIFF....WAVE")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"data": {"voice_id": "svc-1", "reference_text": "hello"}})

    created = await create_voice(audio, name="Creator", language="en-IN", client=_client(handler))

    assert created["voice_id"] == "svc-1"
    assert created["reference_text"] == "hello"
    assert seen["path"] == "/voices/create"
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="name"' in body and b"Creator" in body
    assert b'name="language"' in body and b"en-IN" in body
    assert b'name="file"' in body and b"reference.wav" in body


@pytest.mark.asyncio
async def test_list_and_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "svc-1", "name": "Creator"}]})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    voices = await list_voices(client=client)
    assert voices == [{"id": "svc-1", "name": "Creator"}]
    await delete_voice("svc-1", client=client)
    assert seen == [("GET", "/voices"), ("DELETE", "/voices/delete/svc-1")]


@pytest.mark.asyncio
async def test_create_without_a_key_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SARVAM_API", raising=False)
    monkeypatch.delenv("SARAVAM_API", raising=False)
    audio = tmp_path / "reference.wav"
    audio.write_bytes(b"RIFF....WAVE")

    with pytest.raises(SarvamCloneError, match="SARVAM_API"):
        await create_voice(audio, name="Creator", client=_client(lambda r: httpx.Response(500)))
