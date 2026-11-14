"""Tests for the Coverr stock footage provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider
from video_editor_asset_ingestion.providers.visual.coverr import CoverrProvider


@pytest.fixture
def coverr_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="coverr-vid-1",
        job_id="job-coverr",
        scene_id="scene-coverr",
        asset_type=AssetType.VIDEO,
        prompt="aerial city traffic timelapse",
        provider="coverr",
        output_ext="mp4",
    )


@pytest.mark.asyncio
async def test_coverr_video_with_mock(
    tmp_path: Path,
    coverr_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Coverr video search and download."""
    import subprocess

    provider = CoverrProvider()

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
        if parsed.path == "/videos/search":
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "title": "Aerial City Traffic",
                            "urls": {
                                "mp4": "https://cdn.coverr.co/videos/coverr-aerial-city-traffic.mp4",
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
    result = await provider.generate(coverr_video_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.metadata.get("provider") == "coverr"
    assert result.metadata.get("query") == "aerial city traffic timelapse"
    assert result.metadata.get("title") == "Aerial City Traffic"
    assert "coverr-aerial-city-traffic" in result.metadata.get("source_url", "")


@pytest.mark.asyncio
async def test_coverr_no_results(
    tmp_path: Path,
    coverr_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises a clear error when Coverr returns no results."""
    provider = CoverrProvider()

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"hits": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="no results"):
        await provider.generate(coverr_video_request, tmp_path / "out.mp4")


@pytest.mark.asyncio
async def test_coverr_no_downloadable_url(
    tmp_path: Path,
    coverr_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises when the result has no downloadable URL."""
    provider = CoverrProvider()

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"hits": [{"title": "No URL Video", "urls": {}}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with pytest.raises(RuntimeError, match="no downloadable URL"):
        await provider.generate(coverr_video_request, tmp_path / "out.mp4")


@pytest.mark.asyncio
async def test_coverr_fallback_to_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider falls back to request.prompt when params has no query."""
    provider = CoverrProvider()

    request = AssetRequest(
        asset_id="coverr-fallback",
        job_id="job-coverr",
        scene_id="scene-coverr",
        asset_type=AssetType.VIDEO,
        prompt="sunset beach waves",
        provider="coverr",
        output_ext="mp4",
    )

    async def fake_get(self, url: str, **kwargs: object) -> httpx.Response:
        request_obj = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "title": "Sunset Beach",
                        "urls": {"mp4": "https://cdn.coverr.co/sunset.mp4"},
                    }
                ]
            },
            request=request_obj,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(request, output_path)
    assert result.metadata.get("query") == "sunset beach waves"


def test_coverr_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    cls = get_provider("coverr")
    assert cls is CoverrProvider
