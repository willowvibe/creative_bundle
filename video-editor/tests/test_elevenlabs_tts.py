"""Tests for the ElevenLabs TTS provider."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider

from tests.conftest import _require_ffmpeg


@pytest.fixture
def elevenlabs_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="tts-el-1",
        job_id="job-el",
        scene_id="scene-el",
        asset_type=AssetType.TTS,
        prompt="Hello from ElevenLabs.",
        provider="elevenlabs_tts",
        params={"voice_id": "test-voice", "model": "eleven_turbo_v2_5"},
        output_ext="mp3",
    )


@pytest.mark.asyncio
async def test_elevenlabs_tts_with_mock(
    tmp_path: Path,
    elevenlabs_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the ElevenLabs endpoint and verify the result shape."""
    _require_ffmpeg()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsTTSProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(real_mp3),
        ],
        check=True,
        capture_output=True,
    )
    mp3_bytes = real_mp3.read_bytes()

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    result = await provider.generate(elevenlabs_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.TTS
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "elevenlabs_tts"
    assert result.metadata.get("voice_id") == "test-voice"
    assert result.metadata.get("model") == "eleven_turbo_v2_5"


def test_elevenlabs_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_tts")
    assert cls is ElevenLabsTTSProvider


def test_a_bare_provider_reads_the_conventional_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TTSRouter`` builds this provider with no config, so it must still
    find the key under its documented name."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert ElevenLabsTTSProvider().api_key == "env-key"


def test_a_configured_key_env_wins_over_the_conventional_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    monkeypatch.setenv("CUSTOM_ELEVENLABS_KEY", "custom-key")
    provider = ElevenLabsTTSProvider(ProviderConfig(api_key_env="CUSTOM_ELEVENLABS_KEY"))
    assert provider.api_key == "custom-key"


@pytest.mark.asyncio
async def test_elevenlabs_tts_rejects_json_error_body(
    tmp_path: Path,
    elevenlabs_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #69 regression: a JSON error body must not be saved as .mp3."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsTTSProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"detail": "voice not found"},
            headers={"content-type": "application/json"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    with pytest.raises(MediaContentError, match="Expected audio response"):
        await provider.generate(elevenlabs_request, output_path)
    assert not output_path.exists()
