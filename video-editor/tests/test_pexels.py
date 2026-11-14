"""Tests for the Pexels stock photo/video provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider
from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider

from tests.conftest import _require_ffmpeg


@pytest.fixture
def pexels_image_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="pexels-img-1",
        job_id="job-pexels",
        scene_id="scene-pexels",
        asset_type=AssetType.IMAGE,
        prompt="oil refinery sunset industrial",
        provider="pexels",
        output_ext="png",
    )


@pytest.fixture
def pexels_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="pexels-vid-1",
        job_id="job-pexels",
        scene_id="scene-pexels",
        asset_type=AssetType.VIDEO,
        prompt="aerial city traffic timelapse",
        provider="pexels",
        output_ext="mp4",
        params={"duration_seconds": 5.0},
    )


@pytest.mark.asyncio
async def test_pexels_image_with_mock(
    tmp_path: Path,
    pexels_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Pexels image search and download."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    provider = PexelsProvider(config=ProviderConfig(api_key_env="PEXELS_API_KEY"))
    provider.api_key = "test-key"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        parsed = httpx.URL(url)
        if parsed.path == "/v1/search":
            return httpx.Response(
                200,
                json={
                    "photos": [
                        {
                            "id": 12345,
                            "width": 1920,
                            "height": 1080,
                            "url": "https://www.pexels.com/photo/12345",
                            "photographer": "Test Photographer",
                            "photographer_url": "https://www.pexels.com/@test",
                            "avg_color": "#1a2b3c",
                            "src": {
                                "original": "https://images.pexels.com/original.jpg",
                                "large2x": "https://images.pexels.com/large2x.jpg",
                                "large": "https://images.pexels.com/large.jpg",
                                "medium": "https://images.pexels.com/medium.jpg",
                                "small": "https://images.pexels.com/small.jpg",
                            },
                        }
                    ]
                },
                request=request,
            )
        # Download URL.
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(pexels_image_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata.get("provider") == "pexels"
    assert result.metadata.get("query") == "oil refinery sunset industrial"
    attribution = result.metadata.get("attribution", {})
    assert attribution.get("photographer") == "Test Photographer"
    assert result.metadata.get("download_url") == "https://images.pexels.com/large2x.jpg"


@pytest.mark.asyncio
async def test_pexels_video_with_mock(
    tmp_path: Path,
    pexels_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Pexels video search and download, then probe duration."""
    _require_ffmpeg()
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    provider = PexelsProvider(config=ProviderConfig(api_key_env="PEXELS_API_KEY"))
    provider.api_key = "test-key"

    import subprocess

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

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        parsed = httpx.URL(url)
        if parsed.path == "/videos/search":
            return httpx.Response(
                200,
                json={
                    "videos": [
                        {
                            "id": 67890,
                            "width": 1920,
                            "height": 1080,
                            "url": "https://www.pexels.com/video/67890",
                            "duration": 15,
                            "user": {
                                "name": "Test Videographer",
                                "url": "https://www.pexels.com/@videotest",
                            },
                            "video_files": [
                                {
                                    "id": 1,
                                    "quality": "sd",
                                    "file_type": "video/mp4",
                                    "width": 640,
                                    "height": 360,
                                    "link": "https://videos.pexels.com/sd.mp4",
                                },
                                {
                                    "id": 2,
                                    "quality": "hd",
                                    "file_type": "video/mp4",
                                    "width": 1920,
                                    "height": 1080,
                                    "link": "https://videos.pexels.com/hd.mp4",
                                },
                            ],
                        }
                    ]
                },
                request=request,
            )
        # Download URL.
        return httpx.Response(200, content=mp4_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(pexels_video_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "pexels"
    attribution = result.metadata.get("attribution", {})
    assert attribution.get("videographer") == "Test Videographer"
    assert result.metadata.get("download_url") == "https://videos.pexels.com/hd.mp4"


@pytest.mark.asyncio
async def test_pexels_missing_api_key(
    tmp_path: Path,
    pexels_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises when the API key is missing."""
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    provider = PexelsProvider(config=ProviderConfig(api_key_env="PEXELS_API_KEY"))
    with pytest.raises(RuntimeError, match="Pexels API key is not configured"):
        await provider.generate(pexels_image_request, tmp_path / "out.png")


@pytest.mark.asyncio
async def test_pexels_no_results(
    tmp_path: Path,
    pexels_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider raises a clear error when Pexels returns no results."""
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    provider = PexelsProvider(config=ProviderConfig(api_key_env="PEXELS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"photos": []}, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "get", fake_get)

    with pytest.raises(RuntimeError, match="no usable results"):
        await provider.generate(pexels_image_request, tmp_path / "out.png")


def test_pexels_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    cls = get_provider("pexels")
    assert cls is PexelsProvider


# --- #259: pick a clip that fits the scene, not the first hit ------------


def _video_hit(video_id: int, duration: int, width: int = 1920, height: int = 1080) -> dict:
    return {
        "id": video_id,
        "duration": duration,
        "width": width,
        "height": height,
        "user": {"name": "u", "url": ""},
        "video_files": [
            {
                "quality": "hd",
                "file_type": "video/mp4",
                "width": width,
                "height": height,
                "link": f"https://videos.pexels.com/{video_id}.mp4",
            }
        ],
    }


def test_video_selection_prefers_the_shortest_clip_covering_the_scene() -> None:
    provider = PexelsProvider()
    data = {"videos": [_video_hit(1, 61), _video_hit(2, 8), _video_hit(3, 20)]}

    selected = provider._select_result(data, AssetType.VIDEO, target_duration=5.0)

    assert selected is not None
    assert selected[0] == "https://videos.pexels.com/2.mp4"
    assert selected[2]["duration"] == 8


def test_video_selection_uses_the_longest_clip_when_none_covers() -> None:
    provider = PexelsProvider()
    data = {"videos": [_video_hit(1, 2), _video_hit(2, 3)]}

    selected = provider._select_result(data, AssetType.VIDEO, target_duration=10.0)

    assert selected is not None
    assert selected[2]["duration"] == 3


def test_video_selection_without_a_target_keeps_the_first_hit() -> None:
    """Backward compatibility: callers that pass no target get today's order."""
    provider = PexelsProvider()
    data = {"videos": [_video_hit(1, 61), _video_hit(2, 8)]}

    selected = provider._select_result(data, AssetType.VIDEO)

    assert selected is not None
    assert selected[2]["duration"] == 61


# --- Video size fixes found by chai-stall-pilot-1 --------------------------


def _pexels_video_data(files: list[tuple[int, int]]) -> dict:
    return {
        "videos": [
            {
                "id": 9,
                "duration": 10,
                "user": {"name": "u", "url": "https://www.pexels.com/@u"},
                "url": "https://www.pexels.com/video/9/",
                "video_files": [
                    {
                        "link": f"https://videos.pexels.com/{w}x{h}.mp4",
                        "file_type": "video/mp4",
                        "width": w,
                        "height": h,
                        "quality": "hd",
                    }
                    for w, h in files
                ],
            }
        ]
    }


def test_pexels_picks_the_smallest_file_that_is_still_1080p() -> None:
    from video_editor_asset_ingestion.models import AssetType
    from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider

    data = _pexels_video_data([(3840, 2160), (1920, 1080), (1280, 720)])
    selected = PexelsProvider()._select_result(data, AssetType.VIDEO)

    assert selected is not None
    assert selected[0] == "https://videos.pexels.com/1920x1080.mp4"


@pytest.mark.asyncio
async def test_pexels_video_downloads_use_the_video_size_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from video_editor_asset_ingestion.config import ProviderConfig
    from video_editor_asset_ingestion.models import AssetRequest, AssetType
    from video_editor_asset_ingestion.providers.visual import pexels

    monkeypatch.setattr(pexels, "MAX_IMAGE_BYTES", 10)
    body = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64

    async def fake_get(self: object, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "api.pexels.com" in str(url) or str(url).startswith("/"):
            return httpx.Response(200, json=_pexels_video_data([(1920, 1080)]), request=request)
        return httpx.Response(200, content=body, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    provider = pexels.PexelsProvider(config=ProviderConfig())
    provider.api_key = "k"
    request = AssetRequest(
        asset_id="v",
        job_id="j",
        scene_id="s",
        asset_type=AssetType.VIDEO,
        prompt="milk",
        provider="pexels",
        output_ext="mp4",
    )
    out = tmp_path / "v.mp4"
    await provider.generate(request, out)

    assert out.read_bytes() == body
