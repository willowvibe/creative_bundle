"""Tests for the Mixkit stock footage provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider
from video_editor_asset_ingestion.providers.visual.mixkit import MixkitProvider


@pytest.fixture
def mixkit_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="mixkit-vid-1",
        job_id="job-mixkit",
        scene_id="scene-mixkit",
        asset_type=AssetType.VIDEO,
        prompt="aerial city traffic timelapse",
        provider="mixkit",
        output_ext="mp4",
    )


@pytest.mark.asyncio
async def test_mixkit_video_with_mock(
    tmp_path: Path,
    mixkit_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Mixkit video search and download."""
    import subprocess

    provider = MixkitProvider()

    real_mp4 = tmp_path / "real.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=30",
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

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        parsed = httpx.URL(url)
        if parsed.path == "/api/videos/":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Aerial City Traffic",
                            "urls": {
                                "mp4": "https://assets.mixkit.co/videos/mixkit-aerial-city-traffic.mp4",
                            },
                        }
                    ]
                },
                request=request,
            )
        # Download URL.
        return httpx.Response(200, content=mp4_bytes, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(mixkit_video_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.metadata.get("provider") == "mixkit"
    assert result.metadata.get("query") == "aerial city traffic timelapse"
    assert result.metadata.get("title") == "Aerial City Traffic"
    assert "mixkit-aerial-city-traffic" in result.metadata.get("source_url", "")


@pytest.mark.asyncio
async def test_mixkit_no_results(
    tmp_path: Path,
    mixkit_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises a clear error when Mixkit returns no results."""
    provider = MixkitProvider()

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"results": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="no results"):
        await provider.generate(mixkit_video_request, tmp_path / "out.mp4")


@pytest.mark.asyncio
async def test_mixkit_no_downloadable_url(
    tmp_path: Path,
    mixkit_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises when the result has no downloadable URL."""
    provider = MixkitProvider()

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"results": [{"title": "No URL Video", "urls": {}}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="no downloadable URL"):
        await provider.generate(mixkit_video_request, tmp_path / "out.mp4")


@pytest.mark.asyncio
async def test_mixkit_fallback_to_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider falls back to request.prompt when params has no query."""
    provider = MixkitProvider()

    request = AssetRequest(
        asset_id="mixkit-fallback",
        job_id="job-mixkit",
        scene_id="scene-mixkit",
        asset_type=AssetType.VIDEO,
        prompt="sunset beach waves",
        provider="mixkit",
        output_ext="mp4",
    )

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request_obj = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Sunset Beach",
                        "urls": {"mp4": "https://assets.mixkit.co/sunset.mp4"},
                    }
                ]
            },
            request=request_obj,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(request, output_path)
    assert result.metadata.get("query") == "sunset beach waves"


def test_mixkit_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    cls = get_provider("mixkit")
    assert cls is MixkitProvider
