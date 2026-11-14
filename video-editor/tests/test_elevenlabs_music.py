"""Tests for the ElevenLabs music generation provider."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.audio.elevenlabs_music import ElevenLabsMusicProvider

from tests.conftest import _require_ffmpeg

# These providers are disabled in real use (ElevenLabs is voice-over only); test their HTTP code anyway.
pytestmark = pytest.mark.usefixtures("allow_elevenlabs_non_voice")


@pytest.fixture
def music_request_fixture(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="music-1",
        job_id="job-music",
        scene_id="job",
        asset_type=AssetType.MUSIC,
        prompt="calm lo-fi for documentary narration, 30s, no vocals",
        provider="elevenlabs_music",
        params={"duration_seconds": 30},
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
            f"sine=frequency=220:duration={seconds}",
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
async def test_elevenlabs_music_with_mock(
    tmp_path: Path,
    music_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the ElevenLabs music endpoint and verify the result shape."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsMusicProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.5)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    result = await provider.generate(music_request_fixture, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.MUSIC
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "elevenlabs_music"
    assert result.metadata.get("duration_seconds") == 30


@pytest.mark.asyncio
async def test_elevenlabs_music_duration_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-request ``duration_seconds`` overrides the provider default."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsMusicProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.3)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="music-2",
        job_id="job-music",
        scene_id="job",
        asset_type=AssetType.MUSIC,
        prompt="epic orchestral sting, 5s",
        provider="elevenlabs_music",
        params={"duration_seconds": 5},
        output_ext="mp3",
    )
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)
    assert result.metadata.get("duration_seconds") == 5


@pytest.mark.asyncio
async def test_elevenlabs_music_missing_api_key(
    tmp_path: Path,
    music_request_fixture: AssetRequest,
) -> None:
    """Missing API key raises a clear RuntimeError (not a vague HTTP error)."""
    provider = ElevenLabsMusicProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    # Deliberately leave api_key unset.
    provider.api_key = None
    output_path = tmp_path / "out.mp3"
    with pytest.raises(RuntimeError, match="ElevenLabs API key is not configured"):
        await provider.generate(music_request_fixture, output_path)


def test_elevenlabs_music_registry_resolution() -> None:
    """The registry can resolve the new music provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_music")
    assert cls is ElevenLabsMusicProvider


@pytest.mark.asyncio
async def test_elevenlabs_music_preserves_float_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C18 regression: a float ``duration_seconds`` must not be silently
    truncated to int (``5.7`` became ``5`` before the fix)."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsMusicProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.3)

    captured: dict[str, Any] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["json"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="music-float",
        job_id="job-music",
        scene_id="job",
        asset_type=AssetType.MUSIC,
        prompt="sting",
        provider="elevenlabs_music",
        params={"duration_seconds": 5.7},
        output_ext="mp3",
    )
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)

    assert captured["json"]["duration_seconds"] == 5.7
    assert result.metadata.get("duration_seconds") == 5.7


@pytest.mark.asyncio
async def test_elevenlabs_music_rejects_html_error_body(
    tmp_path: Path,
    music_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #69 regression: an HTML error page must not be saved as .mp3."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsMusicProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            text="<html><body>Internal Server Error</body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    with pytest.raises(MediaContentError, match="Expected audio response"):
        await provider.generate(music_request_fixture, output_path)
    assert not output_path.exists()
