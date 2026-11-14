"""Tests for the ElevenLabs voice clone provider."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.tts.elevenlabs_clone import (
    ElevenLabsVoiceCloneError,
    ElevenLabsVoiceCloneProvider,
)

from tests.conftest import _require_ffmpeg


@pytest.fixture
def clone_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="clone-el-1",
        job_id="job-el",
        scene_id="scene-el",
        asset_type=AssetType.TTS,
        prompt="Test Clone",
        provider="elevenlabs_clone",
        params={"samples": [str(tmp_path / "sample.wav")]},
    )


def _make_sample(tmp_path: Path) -> Path:
    """Create a tiny real WAV file for multipart upload validation."""
    _require_ffmpeg()
    sample = tmp_path / "sample.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "22050",
            str(sample),
        ],
        check=True,
        capture_output=True,
    )
    return sample


@pytest.mark.asyncio
async def test_clone_voice_returns_voice_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clone_voice should POST samples and return the voice_id from the API."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsVoiceCloneProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"
    sample = _make_sample(tmp_path)

    async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"voice_id": "cloned-123"}, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    voice_id = await provider.clone_voice(samples=[sample], name="Test Clone")
    assert voice_id == "cloned-123"


@pytest.mark.asyncio
async def test_list_clones_filters_premade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_clones should return only non-premade voices."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsVoiceCloneProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "premade-1", "name": "Alice", "category": "premade"},
                    {"voice_id": "cloned-1", "name": "Bob", "category": "cloned"},
                    {"voice_id": "generated-1", "name": "Carol", "category": "generated"},
                ],
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "get", fake_get)

    clones = await provider.list_clones()
    assert len(clones) == 2
    assert all(c["voice_id"] != "premade-1" for c in clones)


@pytest.mark.asyncio
async def test_generate_dispatches_to_clone_and_persists_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate should clone a voice and write the project registry."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsVoiceCloneProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"
    sample = _make_sample(tmp_path)

    async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"voice_id": "cloned-456"}, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="clone-gen-1",
        job_id="job-gen",
        scene_id="scene-gen",
        asset_type=AssetType.TTS,
        prompt="My Clone",
        provider="elevenlabs_clone",
        params={
            "samples": [str(sample)],
            "work_dir": str(tmp_path),
            "description": "A test clone",
        },
    )

    result = await provider.generate(request, tmp_path / "ignored")
    assert result.metadata.get("voice_id") == "cloned-456"
    assert result.metadata.get("voice_name") == "My Clone"

    registry_path = tmp_path / "voices" / "voice_registry.json"
    assert registry_path.exists()
    import json

    data = json.loads(registry_path.read_text())
    assert len(data["voices"]) == 1
    assert data["voices"][0]["voice_id"] == "cloned-456"


@pytest.mark.asyncio
async def test_generate_requires_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate should raise when no samples are provided."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsVoiceCloneProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    request = AssetRequest(
        asset_id="clone-bad",
        job_id="job-bad",
        scene_id="scene-bad",
        asset_type=AssetType.TTS,
        prompt="Bad Clone",
        provider="elevenlabs_clone",
        params={},
    )
    with pytest.raises(ValueError, match=r"params\['samples'\]"):
        await provider.generate(request, tmp_path / "ignored")


@pytest.mark.asyncio
async def test_clone_voice_raises_on_api_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clone_voice should wrap HTTP errors in ElevenLabsVoiceCloneError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsVoiceCloneProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"
    sample = _make_sample(tmp_path)

    async def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            400,
            json={"detail": "Invalid audio file"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    with pytest.raises(ElevenLabsVoiceCloneError, match="Invalid audio file"):
        await provider.clone_voice(samples=[sample], name="Bad Clone")


def test_registry_resolution() -> None:
    """The provider registry can resolve elevenlabs_clone by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_clone")
    assert cls is ElevenLabsVoiceCloneProvider
