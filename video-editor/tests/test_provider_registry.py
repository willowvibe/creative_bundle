"""Regression tests for the generative-provider registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.registry import (
    PROVIDER_REGISTRY,
    get_provider,
    register_provider,
)


def _result(request: AssetRequest, output_path: Path) -> AssetResult:
    return AssetResult(
        asset_id=request.asset_id,
        asset_type=request.asset_type,
        local_path=str(output_path),
        scene_id=request.scene_id,
    )


class _FakeProvider(GenerativeProvider):
    """A do-nothing provider for registry testing."""

    provider_name = "_test_fake"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        return _result(request, output_path)


class _FakeProvider2(GenerativeProvider):
    """Another do-nothing provider with the same registered name."""

    provider_name = "_test_fake"

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        return _result(request, output_path)


@pytest.fixture(autouse=True)
def _cleanup_fake_provider() -> Any:
    """Remove the test provider after each test so state doesn't leak."""
    original = dict(PROVIDER_REGISTRY)
    yield
    # Restore only keys that existed at the start; any newly added keys are
    # dropped, and overwritten values are restored.
    for key in list(PROVIDER_REGISTRY.keys()):
        if key not in original:
            PROVIDER_REGISTRY.pop(key, None)
        else:
            PROVIDER_REGISTRY[key] = original[key]


def test_register_provider_adds_new_name() -> None:
    """A brand-new provider name is added without warning."""
    register_provider("_test_fake", _FakeProvider)
    assert get_provider("_test_fake") is _FakeProvider


def test_hyperframes_video_providers_registered() -> None:
    """HyperFrames real and stub providers are available by name."""
    from video_editor_asset_ingestion.providers.visual.hyperframes_video import (
        HyperFramesVideoProvider,
        StubHyperFramesVideoProvider,
    )

    assert get_provider("hyperframes_video") is HyperFramesVideoProvider
    assert get_provider("stub_hyperframes_video") is StubHyperFramesVideoProvider


def test_pexels_provider_registered() -> None:
    """Pexels stock provider is available by name."""
    from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider

    assert get_provider("pexels") is PexelsProvider


def test_agy_image_is_registered() -> None:
    from video_editor_asset_ingestion.providers.registry import get_provider
    from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider

    assert get_provider("agy_image") is AGYImageProvider
