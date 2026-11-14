"""Tests for the ElevenLabs sound-effects provider."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.audio.elevenlabs_sfx import ElevenLabsSFXProvider

from tests.conftest import _require_ffmpeg

# These providers are disabled in real use (ElevenLabs is voice-over only); test their HTTP code anyway.
pytestmark = pytest.mark.usefixtures("allow_elevenlabs_non_voice")


@pytest.fixture
def sfx_request_fixture(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="sfx-1",
        job_id="job-sfx",
        scene_id="scene-sfx",
        asset_type=AssetType.SFX,
        prompt="A cinematic whoosh with reverb.",
        provider="elevenlabs_sfx",
        params={"duration_seconds": 5.0},
        output_ext="mp3",
    )


def _make_real_mp3(path: Path, seconds: float = 0.5) -> bytes:
    _require_ffmpeg()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:duration={seconds}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


@pytest.mark.asyncio
async def test_elevenlabs_sfx_with_mock(
    tmp_path: Path,
    sfx_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the ElevenLabs SFX endpoint and verify the result shape."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSFXProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.5)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    result = await provider.generate(sfx_request_fixture, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.SFX
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "elevenlabs_sfx"
    assert result.metadata.get("duration_seconds") == 5.0


@pytest.mark.asyncio
async def test_elevenlabs_sfx_prompt_influence_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-request ``prompt_influence`` overrides the provider default."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSFXProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.3)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="sfx-2",
        job_id="job-sfx",
        scene_id="scene-sfx",
        asset_type=AssetType.SFX,
        prompt="A subtle wind chime",
        provider="elevenlabs_sfx",
        params={"duration_seconds": 3.0, "prompt_influence": 0.85},
        output_ext="mp3",
    )
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)
    assert result.metadata.get("prompt_influence") == 0.85


@pytest.mark.asyncio
async def test_elevenlabs_sfx_missing_api_key(
    tmp_path: Path,
    sfx_request_fixture: AssetRequest,
) -> None:
    """Missing API key raises a clear RuntimeError."""
    provider = ElevenLabsSFXProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = None
    output_path = tmp_path / "out.mp3"
    with pytest.raises(RuntimeError, match="ElevenLabs API key is not configured"):
        await provider.generate(sfx_request_fixture, output_path)


def test_elevenlabs_sfx_registry_resolution() -> None:
    """The registry can resolve the new SFX provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_sfx")
    assert cls is ElevenLabsSFXProvider


@pytest.mark.asyncio
async def test_elevenlabs_sfx_rejects_json_error_body(
    tmp_path: Path,
    sfx_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #69 regression: a JSON error body must not be saved as .mp3."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSFXProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"detail": "prompt too long"},
            headers={"content-type": "application/json"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    with pytest.raises(MediaContentError, match="Expected audio response"):
        await provider.generate(sfx_request_fixture, output_path)
    assert not output_path.exists()
