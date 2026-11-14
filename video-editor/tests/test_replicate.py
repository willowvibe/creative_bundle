"""Tests for the Replicate image/video generation provider."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.visual.replicate import ReplicateImageProvider


@pytest.fixture
def replicate_image_request(tmp_path: Path) -> AssetRequest:
    return AssetRequest(
        asset_id="rep-img-1",
        job_id="job-rep",
        scene_id="scene-rep",
        asset_type=AssetType.IMAGE,
        prompt="A robot painting.",
        provider="replicate_image",
        params={"model": "black-forest-labs/flux-schnell"},
        output_ext="png",
    )


@pytest.mark.asyncio
async def test_replicate_polls_async_predictions_to_completion(
    tmp_path: Path,
    replicate_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C6 regression: models slower than Replicate's ~10s ``Prefer: wait``
    window return ``status: starting/processing`` with a polling URL. The
    provider must poll ``urls.get`` until ``status == succeeded`` before
    trying to extract the output URL.
    """
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-token")
    provider = ReplicateImageProvider(config=ProviderConfig(api_key_env="REPLICATE_API_TOKEN"))
    provider.api_key = "test-token"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"0" * 32

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "id": "pred-123",
                "status": "starting",
                "urls": {"get": "https://api.replicate.com/v1/predictions/pred-123"},
            },
            request=request,
        )

    poll_count = 0

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        nonlocal poll_count
        if "predictions" in url and not url.endswith(".png"):
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(
                    200,
                    json={"id": "pred-123", "status": "processing"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "id": "pred-123",
                    "status": "succeeded",
                    "output": "https://cdn.replicate.com/output.png",
                },
                request=request,
            )
        return httpx.Response(200, content=fake_png, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    result = await provider.generate(replicate_image_request, output_path)

    assert output_path.exists()
    assert poll_count == 2
    assert result.asset_type == AssetType.IMAGE
    assert result.metadata.get("provider") == "replicate_image"


@pytest.mark.asyncio
async def test_replicate_raises_on_failed_prediction(
    tmp_path: Path,
    replicate_image_request: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Replicate prediction that reaches ``failed`` must surface the error."""
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-token")
    provider = ReplicateImageProvider(config=ProviderConfig(api_key_env="REPLICATE_API_TOKEN"))
    provider.api_key = "test-token"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "id": "pred-456",
                "status": "starting",
                "urls": {"get": "https://api.replicate.com/v1/predictions/pred-456"},
            },
            request=request,
        )

    async def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "id": "pred-456",
                "status": "failed",
                "error": "CUDA out of memory",
            },
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)
    monkeypatch.setattr(client, "get", fake_get)

    output_path = tmp_path / "out.png"
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        await provider.generate(replicate_image_request, output_path)
