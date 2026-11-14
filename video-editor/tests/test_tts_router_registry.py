"""Regression tests for the ``tts_router`` entry in ``PROVIDER_REGISTRY``.

Bug: ``TTSRouter`` was missing from ``PROVIDER_REGISTRY``, so any
``AssetRequest(provider="tts_router", ...)`` raised ``KeyError`` inside
``AssetIngestionManager.resolve`` — i.e. every storyteller TTS scene crashed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY, get_provider
from video_editor_state.job import JobContext


def test_tts_router_is_in_provider_registry() -> None:
    """``tts_router`` must resolve to ``TTSRouter`` via the registry."""
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    assert "tts_router" in PROVIDER_REGISTRY
    assert get_provider("tts_router") is TTSRouter


@pytest.mark.asyncio
async def test_resolve_with_tts_router_does_not_raise_key_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AssetIngestionManager.resolve([... provider='tts_router' ...])`` must
    not raise ``KeyError`` — it should hand off to the router, which delegates
    to Sarvam/ElevenLabs (mocked here so no network).
    """
    monkeypatch.setenv("SARAVAM_API", "test-key")

    request = AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="hello",
        provider="tts_router",
        params={"voice_id": "sarvam:shubh", "language": "hi"},
    )

    fake_result = AssetResult(
        asset_id=request.asset_id,
        asset_type=AssetType.TTS,
        scene_id=request.scene_id,
        local_path=str(tmp_path / "a.mp3"),
        duration_ms=1000,
        metadata={"provider": "sarvam_tts"},
    )

    # Patch the lazy sub-providers' ``generate`` so the router never touches
    # the network. Patching the class methods is enough because the router
    # constructs the sub-providers lazily on first use.
    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        return fake_result

    sarvam_module = __import__(
        "video_editor_asset_ingestion.providers.tts.sarvam_tts",
        fromlist=["SarvamTTSProvider"],
    )
    elevenlabs_module = __import__(
        "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
        fromlist=["ElevenLabsTTSProvider"],
    )
    with (
        patch.object(sarvam_module.SarvamTTSProvider, "generate", new=fake_sarvam),
        patch.object(elevenlabs_module.ElevenLabsTTSProvider, "generate", new=AsyncMock()),
    ):
        ctx = JobContext(
            job_id="tts-registry-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        manager = AssetIngestionManager(ctx)
        # ``_resolve_provider`` checks ``provider_name in self._provider_instances``
        # first; bypass any cached instance by going straight through the
        # registry path. We exercise the real registry lookup by asking the
        # manager to resolve the request — the manager calls ``get_provider``
        # internally, which is what the regression is about.
        results = await manager.resolve([request])

    assert len(results) == 1
    assert results[0].asset_id == "scene_001_tts"


@pytest.mark.parametrize(
    ("name", "class_name"),
    [("kitten_tts", "KittenTTSProvider"), ("moss_tts", "MossTTSProvider")],
)
def test_local_tts_providers_are_in_provider_registry(name: str, class_name: str) -> None:
    """A provider the pricing registry calls free must also be constructible.

    ``pricing.yaml`` and ``_pick_provider`` both name ``kitten_tts``/``moss_tts``,
    but the registry did not export them, so asking for the free local voice
    failed at resolve time with ``Unknown provider``.
    """
    assert name in PROVIDER_REGISTRY
    assert get_provider(name).__name__ == class_name
