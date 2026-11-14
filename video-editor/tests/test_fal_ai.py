"""Tests for the FAL.AI provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

from tests.conftest import _require_ffmpeg


@pytest.fixture
def fal_image_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="fal-img-1",
        job_id="job-fal",
        scene_id="scene-fal",
        asset_type=AssetType.IMAGE,
        prompt="A futuristic cityscape.",
        provider="fal_ai",
        params={"model": "fal-ai/flux/dev"},
        output_ext="png",
    )


@pytest.fixture
def fal_video_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="fal-vid-1",
        job_id="job-fal",
        scene_id="scene-fal",
        asset_type=AssetType.VIDEO,
        prompt="A futuristic cityscape with motion.",
        provider="fal_ai",
        params={"model": "fal-ai/luma-dream-machine"},
        output_ext="mp4",
    )


@pytest.mark.asyncio
async def test_fal_image_with_mock(
    tmp_path: Path,
    fal_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock FAL submit/poll/download for an image asset."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    provider.api_key = "test-key"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "status_url": "https://queue.fal.run/fal-ai/status/123",
                "response_url": "https://queue.fal.run/fal-ai/result/123",
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "status" in url:
            return httpx.Response(200, json={"status": "COMPLETED"}, request=request)
        if "result" in url:
            return httpx.Response(
                200,
                json={"images": [{"url": "https://cdn.fal.media/fake.png"}]},
                request=request,
            )
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(fal_image_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata.get("provider") == "fal_ai"
    assert result.metadata.get("model") == "fal-ai/flux/dev"


@pytest.mark.asyncio
async def test_fal_video_with_mock(
    tmp_path: Path,
    fal_video_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock FAL submit/poll/download for a video asset."""
    _require_ffmpeg()
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
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

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "status_url": "https://queue.fal.run/fal-ai/status/456",
                "response_url": "https://queue.fal.run/fal-ai/result/456",
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "status" in url:
            return httpx.Response(200, json={"status": "COMPLETED"}, request=request)
        if "result" in url:
            return httpx.Response(
                200,
                json={"video_url": "https://cdn.fal.media/fake.mp4"},
                request=request,
            )
        return httpx.Response(200, content=mp4_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.mp4"
    result = await provider.generate(fal_video_request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.VIDEO
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "fal_ai"
    assert result.metadata.get("model") == "fal-ai/luma-dream-machine"


def test_fal_registry_resolution() -> None:
    """The registry can resolve the new provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("fal_ai")
    assert cls is FalAIProvider


def test_fal_resolve_model_uses_video_slot_for_video_request() -> None:
    """Bug #48 regression: VIDEO requests must not fall back to ``default_model``.

    With ``default_model: fal-ai/flux/dev`` (an image model) and
    ``provider_options.video_model: fal-ai/luma-dream-machine``, a
    VIDEO request resolves to the video model, not the image one.
    """
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        default_model="fal-ai/flux/dev",
        provider_options={"video_model": "fal-ai/luma-dream-machine"},
    )
    provider = FalAIProvider(config=config)
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        output_ext="mp4",
    )
    assert provider._resolve_model(request) == "fal-ai/luma-dream-machine"


def test_fal_resolve_model_falls_back_to_default_video_model() -> None:
    """VIDEO requests without a configured ``video_model`` use the built-in default.

    Bug #51 regression: the legacy ``fal-ai/luma-dream-machine`` default is
    deprecated and polls 500. The provider now defaults to the tested
    Seedance-2.0 mini endpoint.
    """
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        default_model="fal-ai/flux/dev",  # image model only
    )
    provider = FalAIProvider(config=config)
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        output_ext="mp4",
    )
    # Must NOT return the image model even though default_model is set.
    assert provider._resolve_model(request) == "bytedance/seedance-2.0/mini/text-to-video"


def test_fal_resolve_model_image_request_uses_default_model() -> None:
    """IMAGE requests still use ``default_model`` (regression guard)."""
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        default_model="fal-ai/flux/dev",
    )
    provider = FalAIProvider(config=config)
    request = AssetRequest(
        asset_id="img-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.IMAGE,
        prompt="A still cityscape",
        provider="fal_ai",
        output_ext="png",
    )
    assert provider._resolve_model(request) == "fal-ai/flux/dev"


def test_fal_build_payload_defaults_generate_audio_false_for_seedance() -> None:
    """Bug #51 regression: Seedance models must not default to audio generation.

    FAL's Seedance endpoints default to ``generate_audio: true``, which
    frequently trips content-policy checks on generic prompts. The provider
    should inject ``generate_audio: false`` unless the caller explicitly set
    it.
    """
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        output_ext="mp4",
    )
    payload = provider._build_payload(request, "bytedance/seedance-2.0/mini/text-to-video")
    assert payload["generate_audio"] is False


def test_fal_build_payload_allows_generate_audio_override_for_seedance() -> None:
    """Callers can opt back into audio generation on Seedance models."""
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"model_kwargs": {"generate_audio": True}},
        output_ext="mp4",
    )
    payload = provider._build_payload(request, "bytedance/seedance-2.0/mini/text-to-video")
    assert payload["generate_audio"] is True


def test_fal_build_payload_does_not_inject_generate_audio_for_non_bytedance() -> None:
    """Legacy/other FAL video models keep their original defaults."""
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        output_ext="mp4",
    )
    payload = provider._build_payload(request, "fal-ai/luma-dream-machine")
    assert "generate_audio" not in payload


def test_fal_status_url_overrides_from_provider_options() -> None:
    """Regression guard: ``provider_options.status_url`` is honored.

    Pre-fix, ``self.config.model_config.get('status_url', ...)`` was
    reading Pydantic's reserved class attribute (always ``{}``) and
    silently returning ``DEFAULT_STATUS_URL`` no matter what the user
    configured. The fix renames the field to ``provider_options``.
    """
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        provider_options={"status_url": "https://custom.fal.run/queue/requests"},
    )
    provider = FalAIProvider(config=config)
    assert provider.status_url == "https://custom.fal.run/queue/requests"


def test_fal_status_url_default_when_unset() -> None:
    """Without a configured override, ``status_url`` falls back to the default."""
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    from video_editor_asset_ingestion.providers.visual.fal_ai import DEFAULT_STATUS_URL

    assert provider.status_url == DEFAULT_STATUS_URL


def test_fal_poll_timeout_is_configurable() -> None:
    """Bug #68 regression: video poll timeout/interval must be configurable."""
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        provider_options={
            "poll_timeout_seconds": 120.0,
            "poll_interval_seconds": 3.0,
        },
    )
    provider = FalAIProvider(config=config)
    assert provider.poll_interval_seconds == 3.0
    assert provider.poll_max_attempts == 40


def test_fal_poll_timeout_default_is_ten_minutes() -> None:
    """The default poll budget covers long video models (Luma/Wan)."""
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    assert provider.poll_interval_seconds == 2.0
    assert provider.poll_max_attempts == 300


@pytest.mark.asyncio
async def test_fal_image_rejects_non_image_bytes(
    tmp_path: Path,
    fal_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C9 regression: a 200-OK JSON/HTML error body from the FAL CDN must
    not be written to the image cache as a corrupt ``.png``."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "status_url": "https://queue.fal.run/fal-ai/status/789",
                "response_url": "https://queue.fal.run/fal-ai/result/789",
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "status" in url:
            return httpx.Response(200, json={"status": "COMPLETED"}, request=request)
        if "result" in url:
            return httpx.Response(
                200,
                json={"images": [{"url": "https://cdn.fal.media/fake.png"}]},
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
    from video_editor_asset_ingestion.providers._media import MediaContentError

    with pytest.raises(MediaContentError, match="Expected PNG/JPEG/WebP"):
        await provider.generate(fal_image_request, output_path)

    # The corrupt file must not be cached on disk.
    assert not output_path.exists()


@pytest.mark.asyncio
async def test_fal_poll_5xx_does_not_resubmit(
    tmp_path: Path,
    fal_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C12 regression: a transient 5xx from the FAL status/response endpoint
    must be retried inside the poll loop, not by re-submitting the job."""
    monkeypatch.setenv("FAL_KEY", "test-key")
    provider = FalAIProvider(
        config=ProviderConfig(
            api_key_env="FAL_KEY",
            provider_options={"poll_interval_seconds": 0.05},
        )
    )
    provider.api_key = "test-key"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    post_calls = 0
    status_calls = 0

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        nonlocal post_calls
        post_calls += 1
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "status_url": "https://queue.fal.run/fal-ai/status/999",
                "response_url": "https://queue.fal.run/fal-ai/result/999",
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "status" in url:
            nonlocal status_calls
            status_calls += 1
            if status_calls <= 2:
                return httpx.Response(503, text="Temporary FAL outage", request=request)
            return httpx.Response(200, json={"status": "COMPLETED"}, request=request)
        if "result" in url:
            if status_calls <= 2:
                return httpx.Response(503, text="Temporary FAL outage", request=request)
            return httpx.Response(
                200,
                json={"images": [{"url": "https://cdn.fal.media/fake.png"}]},
                request=request,
            )
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(fal_image_request, output_path)

    assert result.asset_type == AssetType.IMAGE
    assert post_calls == 1, "FAL job was re-submitted instead of retrying the poll"
    assert status_calls >= 3, "status endpoint should have been polled multiple times"


def test_fal_extract_download_url_supports_nested_video_object() -> None:
    """Seedance-2.0 and some FAL video models return the result under a
    ``video`` object rather than a flat ``video_url`` key."""
    url = FalAIProvider._extract_download_url(
        {"video": {"url": "https://cdn.fal.media/seedance.mp4"}},
        AssetType.VIDEO,
    )
    assert url == "https://cdn.fal.media/seedance.mp4"


def test_fal_extract_download_url_prefers_flat_video_url() -> None:
    """Legacy Luma-style responses with a top-level ``video_url`` still work."""
    url = FalAIProvider._extract_download_url(
        {"video_url": "https://cdn.fal.media/luma.mp4"},
        AssetType.VIDEO,
    )
    assert url == "https://cdn.fal.media/luma.mp4"


def test_fal_build_payload_maps_luma_duration() -> None:
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"duration_seconds": 9},
        output_ext="mp4",
    )

    payload = provider._build_payload(request, "fal-ai/luma-dream-machine")

    assert payload["duration"] == "9s"


def test_fal_build_payload_maps_seedance_duration_to_seconds() -> None:
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"duration_seconds": 9},
        output_ext="mp4",
    )

    payload = provider._build_payload(request, "bytedance/seedance-2.0/mini/text-to-video")

    assert payload["duration"] == 9


def test_fal_build_payload_maps_wan_duration_to_frames() -> None:
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"duration_seconds": 9},
        output_ext="mp4",
    )

    payload = provider._build_payload(request, "fal-ai/wan-2.2/text-to-video")

    assert payload["num_frames"] == 144


def test_fal_build_payload_does_not_guess_unknown_model_duration() -> None:
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"duration_seconds": 9},
        output_ext="mp4",
    )

    payload = provider._build_payload(request, "fal-ai/unknown-video")

    assert "duration" not in payload
    assert "num_frames" not in payload


def test_fal_build_payload_keeps_explicit_duration_override() -> None:
    provider = FalAIProvider(config=ProviderConfig(api_key_env="FAL_KEY"))
    request = AssetRequest(
        asset_id="vid-1",
        job_id="job-1",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt="A moving cityscape",
        provider="fal_ai",
        params={"duration_seconds": 9, "model_kwargs": {"duration": "5s"}},
        output_ext="mp4",
    )

    payload = provider._build_payload(request, "fal-ai/luma-dream-machine")

    assert payload["duration"] == "5s"
