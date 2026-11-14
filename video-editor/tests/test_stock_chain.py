"""The ``stock`` provider name: walk the free stock libraries in order."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from video_editor_asset_ingestion.config import (
    DEFAULT_STOCK_VIDEO_CHAIN,
    AssetProviderConfig,
    ProviderConfig,
)
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_state.job import JobContext


def _stock_request() -> AssetRequest:
    return AssetRequest(
        asset_id="scene_1_footage",
        job_id="stock-job",
        scene_id="scene_1",
        asset_type=AssetType.VIDEO,
        prompt="chicken market india",
        provider="stock",
        output_ext="mp4",
    )


def _all_configured(**extra: object) -> AssetProviderConfig:
    return AssetProviderConfig(
        providers={name: ProviderConfig() for name in DEFAULT_STOCK_VIDEO_CHAIN},
        **extra,  # type: ignore[arg-type]
    )


def test_default_stock_chain_is_the_working_libraries_in_order() -> None:
    assert _all_configured().stock_video_chain_for() == ["pexels", "pixabay"]


def test_default_stock_chain_skips_libraries_the_config_does_not_set_up() -> None:
    config = AssetProviderConfig(providers={"pixabay": ProviderConfig()})

    assert config.stock_video_chain_for() == ["pixabay"]


def test_an_explicit_stock_chain_wins() -> None:
    config = AssetProviderConfig(
        providers={n: ProviderConfig() for n in ("pexels", "pixabay", "coverr")},
        stock_video_chain=["pixabay", "coverr"],
    )

    assert config.stock_video_chain_for() == ["pixabay", "coverr"]


def test_an_unknown_stock_provider_is_rejected_at_load() -> None:
    with pytest.raises(ValidationError, match="stock_video_chain"):
        _all_configured(stock_video_chain=["pexles"])


def test_the_stock_provider_name_resolves_to_the_stock_chain(tmp_path: Path) -> None:
    ctx = JobContext(job_id="stock-job", work_root=tmp_path / "w", output_root=tmp_path / "o")
    manager = AssetIngestionManager(ctx, config=_all_configured())

    assert manager._provider_chain(_stock_request()) == list(DEFAULT_STOCK_VIDEO_CHAIN)
