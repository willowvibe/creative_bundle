"""Tests for the OpenRouter generation provider (image + video + music)."""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider
from video_editor_asset_ingestion.providers.visual.openrouter import OpenRouterProvider

from tests.conftest import _require_ffmpeg

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _image_request() -> AssetRequest:
    return AssetRequest(
        asset_id="or-img-1",
        job_id="job-or",
        scene_id="scene-or",
        asset_type=AssetType.IMAGE,
        prompt="A futuristic cityscape.",
        provider="openrouter",
        output_ext="png",
    )


def _video_request() -> AssetRequest:
    return AssetRequest(
        asset_id="or-vid-1",
        job_id="job-or",
        scene_id="scene-or",
        asset_type=AssetType.VIDEO,
        prompt="A futuristic cityscape in motion.",
        provider="openrouter",
        output_ext="mp4",
        params={"duration_seconds": 4},
    )


def _music_request() -> AssetRequest:
    return AssetRequest(
        asset_id="or-mus-1",
        job_id="job-or",
        scene_id="job",
        asset_type=AssetType.MUSIC,
        prompt="calm ambient background music",
        provider="openrouter",
        output_ext="mp3",
        params={"duration_seconds": 20},
    )


def _provider(monkeypatch: pytest.MonkeyPatch) -> OpenRouterProvider:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return OpenRouterProvider(config=ProviderConfig(api_key_env="OPENROUTER_API_KEY"))


def test_openrouter_is_registered() -> None:
    assert get_provider("openrouter") is OpenRouterProvider


@pytest.mark.asyncio
async def test_openrouter_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"b64_json": base64.b64encode(FAKE_PNG).decode(), "media_type": "image/png"}
                ],
                "usage": {"cost": 0.13},
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.png"
    result = await provider.generate(_image_request(), output_path)

    assert output_path.read_bytes() == FAKE_PNG
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata["provider"] == "openrouter"
    assert result.metadata["model"] == "openai/gpt-image-2"
    assert result.metadata["cost"] == 0.13


@pytest.mark.asyncio
async def test_openrouter_image_rejects_non_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_asset_ingestion.providers._media import MediaContentError

    provider = _provider(monkeypatch)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(b"<html>oops</html>").decode()}]},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    with pytest.raises(MediaContentError):
        await provider.generate(_image_request(), tmp_path / "out.png")


@pytest.mark.asyncio
async def test_openrouter_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _require_ffmpeg()
    provider = _provider(monkeypatch)

    real_mp4 = tmp_path / "real.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(real_mp4),
        ],
        check=True,
        capture_output=True,
    )
    mp4_bytes = real_mp4.read_bytes()
    job_id = "vid-123"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            202,
            json={
                "id": job_id,
                "polling_url": f"https://openrouter.ai/api/v1/videos/{job_id}",
                "status": "pending",
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url.endswith("/content") or "/content" in url:
            return httpx.Response(200, content=mp4_bytes, request=request)
        return httpx.Response(
            200,
            json={
                "id": job_id,
                "status": "completed",
                "unsigned_urls": [f"https://openrouter.ai/api/v1/videos/{job_id}/content"],
                "usage": {"cost": 0.48},
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(_video_request(), output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.duration_ms > 0
    assert result.metadata["model"] == "google/veo-3.1-fast"
    assert result.metadata["cost"] == 0.48


@pytest.mark.asyncio
async def test_openrouter_music(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _provider(monkeypatch)
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 20

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "audio": {
                                "data": base64.b64encode(audio_bytes).decode(),
                                "format": "wav",
                            }
                        }
                    }
                ],
                "usage": {"cost": 0.02},
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "music.mp3"
    result = await provider.generate(_music_request(), output_path)

    assert output_path.read_bytes() == audio_bytes
    assert result.asset_type == AssetType.MUSIC
    assert result.metadata["experimental"] is True
    assert result.metadata["model"] == "google/lyria-3-pro-preview"


@pytest.mark.asyncio
async def test_openrouter_unsupported_asset_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider(monkeypatch)
    request = AssetRequest(
        asset_id="x",
        job_id="job-or",
        scene_id="s",
        asset_type=AssetType.TTS,
        prompt="hi",
        provider="openrouter",
        output_ext="mp3",
    )
    with pytest.raises(RuntimeError, match="does not support asset type"):
        await provider.generate(request, tmp_path / "x.mp3")


@pytest.mark.asyncio
async def test_openrouter_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    provider = OpenRouterProvider(config=ProviderConfig(api_key_env="OPENROUTER_API_KEY"))
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await provider.generate(_image_request(), tmp_path / "out.png")
