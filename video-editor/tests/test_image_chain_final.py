"""Draft vs final image chains (#291/§5.7)."""

from __future__ import annotations

from pathlib import Path

from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_state.job import JobContext


def _config() -> AssetProviderConfig:
    return AssetProviderConfig(
        image_chain=["agy_image", "poyo_ai"],
        image_chain_final=["poyo_ai", "openrouter", "fal_ai"],
        providers={
            "agy_image": ProviderConfig(),
            "poyo_ai": ProviderConfig(),
            "openrouter": ProviderConfig(),
            "fal_ai": ProviderConfig(),
        },
    )


def _request() -> AssetRequest:
    return AssetRequest(
        asset_id="a1",
        job_id="chain-job",
        scene_id="s1",
        asset_type=AssetType.IMAGE,
        prompt="a red circle",
        provider="auto",
        output_ext="png",
    )


def test_final_chain_is_selected_only_for_a_final_run(tmp_path: Path) -> None:
    ctx = JobContext(job_id="chain-job", work_root=tmp_path / "w", output_root=tmp_path / "o")
    draft = AssetIngestionManager(ctx, config=_config())
    final = AssetIngestionManager(ctx, config=_config(), final=True)

    assert draft._provider_chain(_request()) == ["agy_image", "poyo_ai"]
    assert final._provider_chain(_request()) == ["poyo_ai", "openrouter", "fal_ai"]


def test_an_empty_final_chain_falls_back_to_the_draft_chain() -> None:
    config = AssetProviderConfig(
        image_chain=["agy_image"],
        providers={"agy_image": ProviderConfig()},
    )

    assert config.image_chain_for(final=True) == ["agy_image"]
    assert config.image_chain_for(final=False) == ["agy_image"]
