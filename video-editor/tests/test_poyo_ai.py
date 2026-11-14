"""Tests for the PoYo.ai provider."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider

from tests.conftest import _require_ffmpeg


@pytest.fixture
def poyo_image_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="poyo-img-1",
        job_id="job-poyo",
        scene_id="scene-poyo",
        asset_type=AssetType.IMAGE,
        prompt="A futuristic cityscape.",
        provider="poyo_ai",
        output_ext="png",
        params={"model": "gpt-image-2"},
    )


@pytest.fixture
def poyo_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="poyo-vid-1",
        job_id="job-poyo",
        scene_id="scene-poyo",
        asset_type=AssetType.VIDEO,
        prompt="A futuristic cityscape with motion.",
        provider="poyo_ai",
        output_ext="mp4",
        params={"model": "seedance-2-fast"},
    )


@pytest.mark.asyncio
async def test_poyo_image_with_mock(
    tmp_path: Path,
    poyo_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock PoYo submit/poll/download for an image asset."""
    monkeypatch.setenv("POYO_API_KEY", "test-key")
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    provider.api_key = "test-key"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    task_id = "task-poyo-img-1"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"task_id": task_id, "status": "not_started"}},
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if f"/api/generate/status/{task_id}" in url:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "task_id": task_id,
                        "status": "finished",
                        "progress": 100,
                        "files": [
                            {
                                "file_url": "https://storage.poyo.ai/generated/image.png",
                                "file_type": "image",
                            }
                        ],
                    },
                },
                request=request,
            )
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(poyo_image_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata.get("provider") == "poyo_ai"
    assert result.metadata.get("model") == "gpt-image-2"


@pytest.mark.asyncio
async def test_poyo_video_with_mock(
    tmp_path: Path,
    poyo_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock PoYo submit/poll/download for a video asset."""
    _require_ffmpeg()
    monkeypatch.setenv("POYO_API_KEY", "test-key")
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    provider.api_key = "test-key"

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

    task_id = "task-poyo-vid-1"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"task_id": task_id, "status": "not_started"}},
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if f"/api/generate/status/{task_id}" in url:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "task_id": task_id,
                        "status": "finished",
                        "progress": 100,
                        "files": [
                            {
                                "file_url": "https://storage.poyo.ai/generated/video.mp4",
                                "file_type": "video",
                            }
                        ],
                    },
                },
                request=request,
            )
        return httpx.Response(200, content=mp4_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(poyo_video_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "poyo_ai"
    assert result.metadata.get("model") == "seedance-2-fast"


def test_poyo_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("poyo_ai")
    assert cls is PoyoAIProvider


def test_poyo_resolve_model_uses_video_slot_for_video_request() -> None:
    """VIDEO requests must use ``provider_options.video_model``."""
    config = ProviderConfig(
        api_key_env="POYO_API_KEY",
        default_model="gpt-image-2",
        provider_options={"video_model": "seedance-2"},
    )
    provider = PoyoAIProvider(config=config)
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="poyo_ai",
        output_ext="mp4",
    )
    assert provider._resolve_model(request) == "seedance-2"


def test_poyo_resolve_model_request_override_wins() -> None:
    """``request.params["model"]`` overrides both defaults and video_model."""
    config = ProviderConfig(
        api_key_env="POYO_API_KEY",
        default_model="gpt-image-2",
        provider_options={"video_model": "seedance-2"},
    )
    provider = PoyoAIProvider(config=config)
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="poyo_ai",
        output_ext="mp4",
        params={"model": "veo3.1-fast"},
    )
    assert provider._resolve_model(request) == "veo3.1-fast"


def test_poyo_resolve_model_image_request_uses_default_model() -> None:
    """IMAGE requests use ``config.default_model``."""
    config = ProviderConfig(
        api_key_env="POYO_API_KEY",
        default_model="gpt-image-2",
    )
    provider = PoyoAIProvider(config=config)
    request = AssetRequest(
        asset_id="img-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still cityscape",
        provider="poyo_ai",
        output_ext="png",
    )
    assert provider._resolve_model(request) == "gpt-image-2"


def test_poyo_resolve_model_falls_back_to_defaults() -> None:
    """Without any configured model, built-in defaults are used.

    Images default to ``nano-banana-2-lite`` (the cheapest good-quality
    still); a plan or provider config naming another model still wins.
    """
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    image_request = AssetRequest(
        asset_id="img-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still cityscape",
        provider="poyo_ai",
        output_ext="png",
    )
    video_request = image_request.model_copy(
        update={"asset_id": "vid-1", "asset_type": AssetType.VIDEO, "output_ext": "mp4"}
    )
    assert provider._resolve_model(image_request) == "nano-banana-2-lite"
    assert provider._resolve_model(video_request) == "seedance-2-fast"


def test_poyo_default_image_payload_speaks_aspect_ratio() -> None:
    """The default image model must not be sent gpt-image's presets.

    ``nano-banana-2-lite`` takes ``aspect_ratio`` and rejects ``size`` and
    ``resolution`` (#265). A bare request that picks up the default model
    therefore has to be framed in the nano-banana vocabulary.
    """
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-default",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still cityscape",
        provider="poyo_ai",
        output_ext="png",
    )
    model = provider._resolve_model(request)
    payload = provider._build_payload(request, model)
    assert payload["model"] == "nano-banana-2-lite"
    assert payload["input"]["aspect_ratio"] == "16:9"
    assert "size" not in payload["input"]
    assert "resolution" not in payload["input"]


def test_poyo_aspect_ratio_model_keeps_size_as_ratio() -> None:
    """A plan's ``size`` preset becomes the aspect ratio; ``resolution`` drops.

    Plans written for gpt-image carry ``size`` (a preset such as ``9:16``) and
    ``resolution``. For an aspect-ratio model the preset is the ratio, and the
    resolution tier is meaningless — forwarding it fails the request.
    """
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-portrait",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A vertical still",
        provider="poyo_ai",
        output_ext="png",
        params={"model_kwargs": {"size": "9:16", "resolution": "2K"}},
    )
    payload = provider._build_payload(request, "nano-banana-2-lite")
    assert payload["input"]["aspect_ratio"] == "9:16"
    assert "size" not in payload["input"]
    assert "resolution" not in payload["input"]


def test_poyo_aspect_ratio_default_uses_request_dimensions() -> None:
    """Without a ratio or size, width/height on the request set the ratio."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-vertical",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A vertical still",
        provider="poyo_ai",
        output_ext="png",
        params={"width": 1080, "height": 1920},
    )
    payload = provider._build_payload(request, "nano-banana-2-lite")
    assert payload["input"]["aspect_ratio"] == "9:16"


def test_poyo_build_payload_injects_safe_defaults() -> None:
    """VIDEO payloads get aspect_ratio/resolution/generate_audio defaults."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="poyo_ai",
        output_ext="mp4",
        params={"duration_seconds": 10.0},
    )
    payload = provider._build_payload(request, "seedance-2-fast")
    assert payload["model"] == "seedance-2-fast"
    assert payload["input"]["prompt"] == "A moving cityscape"
    assert payload["input"]["duration"] == 10
    assert payload["input"]["aspect_ratio"] == "16:9"
    assert payload["input"]["resolution"] == "720p"
    assert payload["input"]["generate_audio"] is False


def test_poyo_build_payload_image_defaults() -> None:
    """IMAGE payloads get size/resolution defaults."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still cityscape",
        provider="poyo_ai",
        output_ext="png",
    )
    payload = provider._build_payload(request, "gpt-image-2")
    assert payload["input"]["size"] == "16:9"
    assert payload["input"]["resolution"] == "1K"


def test_poyo_build_payload_image_aspect_ratio_replaces_size() -> None:
    """A caller-supplied ``aspect_ratio`` must not get a ``size`` default too.

    ``size`` is a gpt-image preset. Nano-banana models take ``aspect_ratio``
    and reject ``size`` (400: "resolution is not supported by
    nano-banana-2-lite" — the message names the wrong field), so injecting
    both makes every vertical nano-banana request fail.
    """
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-portrait",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A vertical still",
        provider="poyo_ai",
        output_ext="png",
        params={"model_kwargs": {"aspect_ratio": "9:16"}},
    )
    payload = provider._build_payload(request, "nano-banana-2-lite")
    assert payload["input"]["aspect_ratio"] == "9:16"
    assert "size" not in payload["input"]
    assert "resolution" not in payload["input"]


def test_poyo_build_payload_image_custom_size_is_kept() -> None:
    """An explicit ``size`` still wins over the default and suppresses none."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-size",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still",
        provider="poyo_ai",
        output_ext="png",
        params={"model_kwargs": {"size": "1024x1536"}},
    )
    payload = provider._build_payload(request, "gpt-image-2")
    assert payload["input"]["size"] == "1024x1536"


def test_poyo_build_payload_keeps_model_kwargs() -> None:
    """``model_kwargs`` are merged into the input object."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="poyo_ai",
        output_ext="mp4",
        params={"model_kwargs": {"resolution": "1080p", "aspect_ratio": "9:16"}},
    )
    payload = provider._build_payload(request, "seedance-2-fast")
    assert payload["input"]["resolution"] == "1080p"
    assert payload["input"]["aspect_ratio"] == "9:16"
    assert payload["input"]["generate_audio"] is False


@pytest.mark.asyncio
async def test_poyo_image_rejects_non_image_bytes(
    tmp_path: Path,
    poyo_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200-OK HTML error body from the PoYo CDN must not be written as a PNG."""
    monkeypatch.setenv("POYO_API_KEY", "test-key")
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    provider.api_key = "test-key"

    task_id = "task-poyo-img-bad"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"task_id": task_id, "status": "not_started"}},
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if f"/api/generate/status/{task_id}" in url:
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "task_id": task_id,
                        "status": "finished",
                        "files": [
                            {
                                "file_url": "https://storage.poyo.ai/generated/image.png",
                                "file_type": "image",
                            }
                        ],
                    },
                },
                request=request,
            )
        # CDN returns an HTML error page masquerading as an image.
        return httpx.Response(
            200,
            content=b"<html><body>Service Unavailable</body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    with pytest.raises(MediaContentError, match="Expected PNG/JPEG/WebP"):
        await provider.generate(poyo_image_request, output_path)

    assert not output_path.exists()


@pytest.mark.asyncio
async def test_poyo_poll_failed_status_raises(
    tmp_path: Path,
    poyo_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``failed`` PoYo task surfaces ``error_message``."""
    monkeypatch.setenv("POYO_API_KEY", "test-key")
    provider = PoyoAIProvider(
        config=ProviderConfig(
            api_key_env="POYO_API_KEY",
            provider_options={"poll_interval_seconds": 0.05},
        )
    )
    provider.api_key = "test-key"

    task_id = "task-poyo-fail"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"task_id": task_id, "status": "not_started"}},
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "task_id": task_id,
                    "status": "failed",
                    "error_message": "content policy violation",
                },
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    with pytest.raises(RuntimeError, match="content policy violation"):
        await provider.generate(poyo_image_request, output_path)


def test_poyo_extract_download_url_without_file_type() -> None:
    """Defensive: a response missing ``file_type`` still returns the first URL."""
    url = PoyoAIProvider._extract_download_url(
        {"files": [{"file_url": "https://storage.poyo.ai/file.png"}]},
        AssetType.IMAGE,
    )
    assert url == "https://storage.poyo.ai/file.png"


def test_poyo_extract_download_url_returns_empty_when_missing() -> None:
    """A finished response with no ``files`` or ``url`` yields an empty string."""
    url = PoyoAIProvider._extract_download_url(
        {"task_id": "x", "status": "finished"},
        AssetType.VIDEO,
    )
    assert url == ""


def test_poyo_unwrap_response_detects_api_error() -> None:
    """A non-200 ``code`` inside the response body raises a clear error."""
    with pytest.raises(RuntimeError, match=r"PoYo API error \(402\)"):
        PoyoAIProvider._unwrap_response({"code": 402, "error": {"message": "insufficient balance"}})


def test_poyo_nano_banana_takes_a_plan_level_aspect_ratio() -> None:
    """#291: a 9:16 plan's still (top-level ``aspect_ratio``) is drawn 9:16."""
    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    request = AssetRequest(
        asset_id="img-plan",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A vertical still",
        provider="poyo_ai",
        output_ext="png",
        params={"aspect_ratio": "9:16"},
    )

    payload = provider._build_payload(request, "nano-banana-2-lite")

    assert payload["input"]["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_a_timed_out_paid_task_is_resumed_not_resubmitted(
    tmp_path: Path,
    poyo_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid-task persistence: a poll timeout keeps the task id; the next run polls it instead of paying again."""
    from video_editor_asset_ingestion.providers.poyo_tasks import journal_path

    provider = PoyoAIProvider(config=ProviderConfig(api_key_env="POYO_API_KEY"))
    provider.api_key = "test-key"
    provider.poll_max_attempts = 1
    provider.poll_interval_seconds = 0
    submits: list[str] = []
    state = {"finished": False}
    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        submits.append(url)
        return httpx.Response(
            200,
            json={"code": 200, "data": {"task_id": "paid-1"}},
            request=httpx.Request("POST", url),
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "/api/generate/status/paid-1" in url:
            data: dict[str, object] = {"task_id": "paid-1", "status": "running"}
            if state["finished"]:
                data = {
                    "task_id": "paid-1",
                    "status": "finished",
                    "files": [{"file_url": "https://s/x.png"}],
                }
            return httpx.Response(200, json={"code": 200, "data": data}, request=request)
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)
    out = tmp_path / "out.png"

    with pytest.raises(RuntimeError, match="timed out"):
        await provider.generate(poyo_image_request, out)
    assert journal_path(out).exists() and len(submits) == 1

    state["finished"] = True
    await provider.generate(poyo_image_request, out)
    assert out.exists()
    assert len(submits) == 1  # resumed, not paid for twice
    assert not journal_path(out).exists()


@pytest.mark.asyncio
async def test_a_changed_request_does_not_resume_the_old_task(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.poyo_tasks import pending_task, record_task

    out = tmp_path / "x.png"
    record_task(out, {"prompt": "a"}, "t-1")
    assert pending_task(out, {"prompt": "a"}) == "t-1"
    assert pending_task(out, {"prompt": "b"}) is None
