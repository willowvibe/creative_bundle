"""Map photos the pipeline fetches: region photos and image stickers."""

from __future__ import annotations

from pathlib import Path

from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_pipelines.explainer_pipeline import explainer_job_requests, price_requests
from video_editor_pipelines.explainer_plan import (
    attach_map_media,
    build_explainer_plan,
    map_media_requests,
)
from video_editor_pipelines.explainer_script import parse_explainer_script
from video_editor_pipelines.pricing import PricingRegistry

SCRIPT = """---
title: Godavari
aspect_ratio: "9:16"
---

## Scene 1 — Source
visual: map
country: India
regions:
  - {district: Nashik, image_query: trimbakeshwar temple, label: NASHIK}
  - {district: Bhadradri, image_prompt: "Bhadrachalam temple on the Godavari at dawn"}
stickers:
  - {image_prompt: "a small sticker of a river boat", lon: 81.8, lat: 17.0}
narration: The river begins at Trimbakeshwar.
"""


def _plan():
    script = parse_explainer_script(SCRIPT)
    plan, _ = build_explainer_plan(script, "job")
    return script, plan


def test_region_and_sticker_photos_become_asset_requests() -> None:
    _, plan = _plan()
    requests = map_media_requests(plan, "job", image_provider="auto")
    rows = [(r.asset_id, r.asset_type, r.provider, r.prompt) for r in requests]
    assert rows == [
        ("scene_1_region_0", AssetType.IMAGE, "stock", "trimbakeshwar temple"),
        (
            "scene_1_region_1",
            AssetType.IMAGE,
            "auto",
            "Bhadrachalam temple on the Godavari at dawn",
        ),
        ("scene_1_sticker_0", AssetType.IMAGE, "auto", "a small sticker of a river boat"),
    ]
    assert requests[1].params.get("aspect_ratio") == "9:16"


def test_the_job_prices_stock_photos_at_zero() -> None:
    script, plan = _plan()
    requests = explainer_job_requests(plan, script, "job", music_provider="")
    stock = [r for r in requests if r.provider == "stock" and r.asset_type is AssetType.IMAGE]
    assert len(stock) == 1
    items = price_requests(stock, PricingRegistry.load())
    assert items[0]["provider"] == "pexels" and items[0]["total_cost"] == 0.0


def test_fetched_photos_land_in_the_scene_props(tmp_path: Path) -> None:
    _, plan = _plan()
    results = [
        AssetResult(
            asset_id=f"scene_1_{name}",
            asset_type=AssetType.IMAGE,
            scene_id="scene_1",
            local_path=str(tmp_path / f"{name}.jpg"),
            metadata={},
        )
        for name in ("region_0", "region_1", "sticker_0")
    ]
    attach_map_media(plan, results)
    props = plan["scenes"][0]["props"]
    assert props["regions"][0]["image"].endswith("region_0.jpg")
    assert props["regions"][1]["image"].endswith("region_1.jpg")
    assert props["stickers"][0]["image"].endswith("sticker_0.jpg")
    assert "image_query" not in props["regions"][0] and "image_prompt" not in props["stickers"][0]


def test_the_manager_walks_the_stock_chain_for_images(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
    from video_editor_asset_ingestion.manager import AssetIngestionManager
    from video_editor_asset_ingestion.models import AssetRequest
    from video_editor_state.job import JobContext

    config = AssetProviderConfig(
        providers={"pexels": ProviderConfig(), "pixabay": ProviderConfig()}
    )
    ctx = JobContext(job_id="j", work_root=tmp_path / "w", output_root=tmp_path / "o")
    request = AssetRequest(
        asset_id="a",
        job_id="j",
        scene_id="s",
        asset_type=AssetType.IMAGE,
        prompt="temple",
        provider="stock",
        output_ext="jpg",
    )
    assert AssetIngestionManager(ctx, config=config)._provider_chain(request) == [
        "pexels",
        "pixabay",
    ]


def test_map_photos_are_not_counted_as_scene_stills() -> None:
    from video_editor_pipelines.explainer_plan import counts_as_scene_still

    assert counts_as_scene_still("scene_3_image") is True
    assert counts_as_scene_still("scene_2_region_1") is False
    assert counts_as_scene_still("scene_2_sticker_0") is False
    assert counts_as_scene_still("scene_4_fallback_image") is False


def test_sidecar_captions_front_matter_marks_map_scenes_not_burned() -> None:
    sidecar = SCRIPT.replace(
        'aspect_ratio: "9:16"', 'aspect_ratio: "9:16"\ncaptions: {mode: sidecar}'
    )
    plan, _ = build_explainer_plan(parse_explainer_script(sidecar), "job")
    assert plan["scenes"][0]["props"]["_captions_burned"] is False
    burned, _ = build_explainer_plan(parse_explainer_script(SCRIPT), "job")
    assert "_captions_burned" not in burned["scenes"][0]["props"]
