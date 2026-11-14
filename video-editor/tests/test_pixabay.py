"""Tests for the Pixabay stock photo/video provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.registry import get_provider
from video_editor_asset_ingestion.providers.visual.pixabay import (
    PixabayProvider,
    PixabaySearchError,
)


@pytest.fixture
def pixabay_image_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="pixabay-img-1",
        job_id="job-pixabay",
        scene_id="scene-pixabay",
        asset_type=AssetType.IMAGE,
        prompt="sunset over the mountains",
        provider="pixabay",
        output_ext="png",
    )


@pytest.fixture
def pixabay_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="pixabay-vid-1",
        job_id="job-pixabay",
        scene_id="scene-pixabay",
        asset_type=AssetType.VIDEO,
        prompt="aerial city traffic timelapse",
        provider="pixabay",
        output_ext="mp4",
        params={"duration_seconds": 5.0},
    )


@pytest.mark.asyncio
async def test_pixabay_image_with_mock(
    tmp_path: Path,
    pixabay_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Pixabay image search and download."""
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    provider = PixabayProvider(config=ProviderConfig(api_key_env="PIXABAY_API_KEY"))
    provider.api_key = "test-key"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    async def fake_get(self: object, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        parsed = httpx.URL(url)
        if parsed.path == "/":
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "id": 1001,
                            "pageURL": "https://pixabay.com/photos/sunset-1001/",
                            "largeImageURL": "https://cdn.pixabay.com/large.jpg",
                            "webformatURL": "https://cdn.pixabay.com/web.jpg",
                            "imageWidth": 1920,
                            "imageHeight": 1280,
                            "user": "photo_user",
                            "tags": "sunset, mountain",
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(200, content=fake_png, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(pixabay_image_request, output_path)

    assert result.asset_type == AssetType.IMAGE
    assert output_path.exists()
    assert output_path.read_bytes() == fake_png
    assert result.metadata.get("provider") == "pixabay"
    assert result.metadata.get("download_url") == "https://cdn.pixabay.com/large.jpg"
    assert result.metadata.get("attribution", {}).get("photographer") == "photo_user"


@pytest.mark.asyncio
async def test_pixabay_video_with_mock(
    tmp_path: Path,
    pixabay_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock Pixabay video search and download."""
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    provider = PixabayProvider(config=ProviderConfig(api_key_env="PIXABAY_API_KEY"))
    provider.api_key = "test-key"

    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64

    async def fake_get(self: object, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        parsed = httpx.URL(url)
        if parsed.path == "/videos/":
            return httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "id": 2002,
                            "pageURL": "https://pixabay.com/videos/aerial-2002/",
                            "width": 1920,
                            "height": 1080,
                            "duration": 8,
                            "user": "video_user",
                            "videos": {
                                "large": {
                                    "url": "https://cdn.pixabay.com/videos/large.mp4",
                                    "width": 1920,
                                    "height": 1080,
                                },
                                "medium": {
                                    "url": "https://cdn.pixabay.com/videos/medium.mp4",
                                    "width": 1280,
                                    "height": 720,
                                },
                            },
                        }
                    ]
                },
                request=request,
            )
        return httpx.Response(200, content=fake_mp4, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(pixabay_video_request, output_path)

    assert result.asset_type == AssetType.VIDEO
    assert output_path.exists()
    assert output_path.read_bytes() == fake_mp4
    assert result.metadata.get("download_url") == "https://cdn.pixabay.com/videos/large.mp4"
    assert result.metadata.get("attribution", {}).get("videographer") == "video_user"


@pytest.mark.asyncio
async def test_pixabay_missing_api_key(
    tmp_path: Path,
    pixabay_image_request: AssetRequest,
) -> None:
    """A missing API key raises a clear error."""
    provider = PixabayProvider(config=ProviderConfig())
    provider.api_key = None
    with pytest.raises(RuntimeError, match="API key is not configured"):
        await provider.generate(pixabay_image_request, tmp_path / "out.png")


@pytest.mark.asyncio
async def test_pixabay_no_results(
    tmp_path: Path,
    pixabay_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No search hits raises PixabaySearchError."""
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    provider = PixabayProvider(config=ProviderConfig(api_key_env="PIXABAY_API_KEY"))
    provider.api_key = "test-key"

    async def fake_get(self: object, url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"hits": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    output_path = tmp_path / "out.png"
    with pytest.raises(PixabaySearchError, match="no usable results"):
        await provider.generate(pixabay_image_request, output_path)


@pytest.mark.asyncio
async def test_pixabay_empty_prompt_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty prompt raises a clear error."""
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    provider = PixabayProvider(config=ProviderConfig(api_key_env="PIXABAY_API_KEY"))
    provider.api_key = "test-key"
    request = AssetRequest(
        asset_id="pixabay-empty",
        job_id="job-pixabay",
        scene_id="scene-pixabay",
        asset_type=AssetType.IMAGE,
        prompt="   ",
        provider="pixabay",
    )
    with pytest.raises(ValueError, match="non-empty prompt"):
        await provider.generate(request, tmp_path / "out.png")


def test_pixabay_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    cls = get_provider("pixabay")
    assert cls is PixabayProvider


def test_pixabay_orientations_validate() -> None:
    """Invalid orientations are dropped, valid ones kept."""
    provider = PixabayProvider()
    request = AssetRequest(
        asset_id="pixabay-ori",
        job_id="job-pixabay",
        scene_id="scene-pixabay",
        asset_type=AssetType.IMAGE,
        prompt="city skyline",
        provider="pixabay",
        params={"orientation": "bogus", "per_page": 3},
    )
    params = provider._build_search_params(request)
    assert "orientation" not in params
    assert params["per_page"] == 3


# --- #259: pick a clip that fits the scene, not the first hit -----------


def _pixabay_video_hit(video_id: int, duration: int) -> dict:
    return {
        "id": video_id,
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "user": "u",
        "videos": {
            "large": {
                "url": f"https://cdn.pixabay.com/videos/{video_id}.mp4",
                "width": 1920,
                "height": 1080,
            }
        },
    }


def test_video_selection_prefers_the_shortest_clip_covering_the_scene() -> None:
    provider = PixabayProvider()
    data = {"hits": [_pixabay_video_hit(1, 61), _pixabay_video_hit(2, 8)]}

    selected = provider._select_result(data, AssetType.VIDEO, target_duration=5.0)

    assert selected is not None
    assert selected[0] == "https://cdn.pixabay.com/videos/2.mp4"


def test_video_selection_uses_the_longest_clip_when_none_covers() -> None:
    provider = PixabayProvider()
    data = {"hits": [_pixabay_video_hit(1, 2), _pixabay_video_hit(2, 3)]}

    selected = provider._select_result(data, AssetType.VIDEO, target_duration=10.0)

    assert selected is not None
    assert selected[2]["duration"] == 3


# --- Pixabay API docs conformance (https://pixabay.com/api/docs/) ----------


def _variants(**sizes: tuple[int, int] | None) -> dict:
    """``videos`` block; a ``None`` size is the docs' "empty url, size 0" case."""
    out = {}
    for name, dims in sizes.items():
        if dims is None:
            out[name] = {"url": "", "width": 0, "height": 0, "size": 0}
        else:
            w, h = dims
            out[name] = {"url": f"https://cdn.pixabay.com/{name}.mp4", "width": w, "height": h}
    return out


def _video_data(videos: dict) -> dict:
    return {"hits": [{"id": 7, "user": "u", "user_id": 42, "duration": 9, "videos": videos}]}


@pytest.mark.parametrize(
    ("videos", "expected"),
    [
        # 4K large: the 1080p medium is enough and a fraction of the bytes.
        (_variants(large=(3840, 2160), medium=(1920, 1080), small=(1280, 720)), "medium"),
        # Older upload: large is the only 1080p file.
        (_variants(large=(1920, 1080), medium=(1280, 720), small=(960, 540)), "large"),
        # No large file at all: best of the rest.
        (_variants(large=None, medium=(1280, 720), small=(960, 540)), "medium"),
        # Portrait clip: the short side decides.
        (_variants(large=(2160, 3840), medium=(1080, 1920)), "medium"),
    ],
)
def test_video_variant_is_the_smallest_file_that_is_still_1080p(
    videos: dict, expected: str
) -> None:
    selected = PixabayProvider()._select_result(_video_data(videos), AssetType.VIDEO)

    assert selected is not None
    assert selected[0] == f"https://cdn.pixabay.com/{expected}.mp4"


def test_video_metadata_carries_the_chosen_files_dimensions() -> None:
    videos = _variants(large=(3840, 2160), medium=(1920, 1080))
    selected = PixabayProvider()._select_result(_video_data(videos), AssetType.VIDEO)

    assert selected is not None
    assert (selected[2]["width"], selected[2]["height"]) == (1920, 1080)


def test_attribution_links_the_authors_profile_page_not_the_avatar() -> None:
    videos = _variants(medium=(1920, 1080))
    selected = PixabayProvider()._select_result(_video_data(videos), AssetType.VIDEO)

    assert selected is not None
    assert selected[1]["videographer_url"] == "https://pixabay.com/users/u-42/"


@pytest.mark.parametrize(("asked", "sent"), [(1, 3), (2, 3), (500, 200)])
def test_per_page_is_clamped_to_the_documented_3_to_200(asked: int, sent: int) -> None:
    request = AssetRequest(
        asset_id="a",
        job_id="j",
        scene_id="s",
        asset_type=AssetType.VIDEO,
        prompt="q",
        provider="pixabay",
        params={"per_page": asked},
    )
    assert PixabayProvider()._build_search_params(request)["per_page"] == sent


def _mock_provider(handler) -> PixabayProvider:  # type: ignore[no-untyped-def]
    provider = PixabayProvider(config=ProviderConfig())
    provider.api_key = "test-key"
    transport = httpx.MockTransport(handler)
    provider._client = httpx.AsyncClient(
        base_url=provider.base_url, params={"key": "test-key"}, transport=transport
    )
    provider._download_client = httpx.AsyncClient(transport=transport)
    return provider


_FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64


@pytest.mark.asyncio
async def test_downloads_do_not_carry_the_api_key(
    tmp_path: Path, pixabay_video_request: AssetRequest
) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.host == "pixabay.com":
            return httpx.Response(200, json=_video_data(_variants(medium=(1920, 1080))))
        return httpx.Response(200, content=_FAKE_MP4)

    await _mock_provider(handler).generate(pixabay_video_request, tmp_path / "o.mp4")

    search, download = seen
    assert search.params.get("key") == "test-key"
    assert "key" not in download.params


@pytest.mark.asyncio
async def test_video_downloads_use_the_video_size_ceiling(
    tmp_path: Path,
    pixabay_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real Pixabay 1080p clips are often above the 20 MB image ceiling.
    from video_editor_asset_ingestion.providers.visual import pixabay

    monkeypatch.setattr(pixabay, "MAX_IMAGE_BYTES", 10)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pixabay.com":
            return httpx.Response(200, json=_video_data(_variants(medium=(1920, 1080))))
        return httpx.Response(200, content=_FAKE_MP4)

    out = tmp_path / "o.mp4"
    await _mock_provider(handler).generate(pixabay_video_request, out)

    assert out.read_bytes() == _FAKE_MP4


@pytest.mark.asyncio
async def test_a_429_waits_for_the_rate_limit_reset_then_retries(
    tmp_path: Path,
    pixabay_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from video_editor_asset_ingestion.providers.visual import pixabay

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(pixabay.asyncio, "sleep", fake_sleep)
    searches = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal searches
        if request.url.host == "pixabay.com":
            searches += 1
            if searches == 1:
                return httpx.Response(
                    429, text="API rate limit exceeded", headers={"X-RateLimit-Reset": "7"}
                )
            return httpx.Response(200, json=_video_data(_variants(medium=(1920, 1080))))
        return httpx.Response(200, content=_FAKE_MP4)

    await _mock_provider(handler).generate(pixabay_video_request, tmp_path / "o.mp4")

    assert searches == 2
    assert slept == [7.0]
