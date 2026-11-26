"""``visual: footage``: stock video first, an AI still only when no clip fits."""

from __future__ import annotations

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_pipelines.explainer_pipeline import explainer_job_requests
from video_editor_pipelines.explainer_plan import (
    FALLBACK_IMAGE_SUFFIX,
    build_explainer_plan,
    footage_fallbacks,
    initial_requests,
)
from video_editor_pipelines.explainer_script import ScriptError, parse_explainer_script

SCRIPT = """---
title: Chai stall
aspect_ratio: "16:9"
---

## Scene 1 — Morning
visual: footage
query: tea stall india morning
image: a roadside chai stall at dawn, steam rising, documentary photo
narration: |
  Five in the morning, the first boil of milk.

## Scene 2 — The count
visual: typographic
layout: big_stat
headline: ₹12
narration: Twelve rupees a cup.

## Scene 3 — Queue
visual: footage
query: people drinking tea street
image: office workers drinking chai at a stall, documentary photo
narration: By nine the queue is ten deep and the kettle never stops.
"""


def _script():
    return parse_explainer_script(SCRIPT)


def test_footage_scene_parses_with_query_and_fallback_image() -> None:
    scene = _script().scenes[0]

    assert scene.visual == "footage"
    assert scene.props["query"] == "tea stall india morning"
    assert scene.image.startswith("a roadside chai stall")


@pytest.mark.parametrize("missing", ["query", "image"])
def test_footage_scene_needs_a_query_and_a_fallback_image(missing: str) -> None:
    lines = [line for line in SCRIPT.splitlines() if not line.startswith(f"{missing}: ")]
    with pytest.raises(ScriptError, match=f"footage scene needs a `{missing}`"):
        parse_explainer_script("\n".join(lines))


def test_plan_marks_footage_scenes_and_requests_no_still_up_front() -> None:
    plan, reuse = build_explainer_plan(_script(), "job")
    scene = plan["scenes"][0]

    assert scene["type"] == "footage"
    assert scene["visual_description"] == ""
    assert scene["props"]["stock_query"] == "tea stall india morning"
    assert scene["props"]["fallback_image"].startswith("a roadside chai stall")
    # Footage never joins a still-reuse cluster.
    assert reuse.still_scenes == 0


def test_job_requests_price_one_stock_clip_and_one_fallback_still_per_footage_scene() -> None:
    plan, _ = build_explainer_plan(_script(), "job")
    requests = explainer_job_requests(plan, _script(), "job", music_provider="")

    videos = [r for r in requests if r.asset_type is AssetType.VIDEO]
    fallbacks = [r for r in requests if r.asset_id.endswith(FALLBACK_IMAGE_SUFFIX)]
    assert [(r.scene_id, r.provider, r.prompt) for r in videos] == [
        ("scene_1", "stock", "tea stall india morning"),
        ("scene_3", "stock", "people drinking tea street"),
    ]
    assert all(r.params["duration_seconds"] >= 4 for r in videos)
    # The gate prices the worst case: every footage scene falling back to AI.
    assert [r.scene_id for r in fallbacks] == ["scene_1", "scene_3"]
    assert all(r.asset_type is AssetType.IMAGE and r.provider == "auto" for r in fallbacks)


def test_the_first_pass_leaves_the_fallback_stills_out() -> None:
    plan, _ = build_explainer_plan(_script(), "job")
    requests = explainer_job_requests(plan, _script(), "job", music_provider="")

    first = initial_requests(requests)

    assert not any(r.asset_id.endswith(FALLBACK_IMAGE_SUFFIX) for r in first)
    assert sum(r.asset_type is AssetType.VIDEO for r in first) == 2


def test_fallbacks_are_requested_only_for_scenes_without_an_accepted_clip() -> None:
    plan, _ = build_explainer_plan(_script(), "job")
    requests = explainer_job_requests(plan, _script(), "job", music_provider="")

    chosen = footage_fallbacks(requests, scenes_without_clip={"scene_3"})

    assert [(r.scene_id, r.prompt[:14]) for r in chosen] == [("scene_3", "office workers")]


def test_the_gate_prices_stock_clips_at_zero() -> None:
    from video_editor_pipelines.explainer_pipeline import price_requests
    from video_editor_pipelines.pricing import PricingRegistry

    plan, _ = build_explainer_plan(_script(), "job")
    videos = [
        r
        for r in explainer_job_requests(plan, _script(), "job", music_provider="")
        if r.asset_type is AssetType.VIDEO
    ]
    registry = PricingRegistry.load()

    items = price_requests(videos, registry)

    assert {item["provider"] for item in items} == {"pexels"}
    assert sum(item["total_cost"] for item in items) == 0.0
    assert all(
        registry.unit_cost(item["provider"], item["model"], item["task_type"]) is not None
        for item in items
    )


def test_a_bad_tally_fails_when_the_script_is_read() -> None:
    bad = SCRIPT.replace("headline: ₹12\n", "headline: ₹12\ntally: {label: cash, value: lots}\n")
    with pytest.raises(ScriptError, match="tally"):
        parse_explainer_script(bad)


def test_a_tally_rides_through_to_the_plan() -> None:
    good = SCRIPT.replace("headline: ₹12\n", "headline: ₹12\ntally: {label: cash, value: -3400}\n")
    plan, _ = build_explainer_plan(parse_explainer_script(good), "job")
    assert plan["scenes"][1]["props"]["tally"] == {"label": "cash", "value": -3400}


def test_footage_scenes_get_a_transition_cue() -> None:
    from video_editor_asset_ingestion.scene_assets import sfx_query_for_scene
    from video_editor_core.models.scene_plan import Scene

    scene = Scene(id="s", type="footage", duration_seconds=3.0, narration="x")
    assert sfx_query_for_scene(scene) == "soft whoosh"


# --- the vision judge's LLM budget ----------------------------------------------


def _stock_video_requests(count: int) -> list:
    from video_editor_asset_ingestion.models import AssetRequest

    return [
        AssetRequest(
            asset_id=f"footage_scene_{i}_0",
            job_id="job",
            scene_id=f"scene_{i}",
            asset_type=AssetType.VIDEO,
            prompt="q",
            provider="stock",
            output_ext="mp4",
        )
        for i in range(count)
    ]


def test_the_judge_budget_covers_three_tries_in_every_library() -> None:
    from video_editor_pipelines.explainer_footage import (
        JUDGE_BUDGET_MARGIN,
        MAX_TRIES_PER_LIBRARY,
        footage_judge_attempt_budget,
    )

    requests = _stock_video_requests(14)  # 14 clip requests, 2 libraries

    needed = footage_judge_attempt_budget(requests, ["pexels", "pixabay"], configured=50)

    assert needed == 14 * MAX_TRIES_PER_LIBRARY * 2 + JUDGE_BUDGET_MARGIN
    assert needed > 51  # "LLM attempt budget exceeded: 51 > 50"


def test_a_larger_configured_budget_is_kept_and_photos_are_not_counted() -> None:
    from video_editor_asset_ingestion.models import AssetRequest
    from video_editor_pipelines.explainer_footage import footage_judge_attempt_budget

    photo = AssetRequest(
        asset_id="footage_scene_1_1",
        job_id="job",
        scene_id="scene_1",
        asset_type=AssetType.IMAGE,
        prompt="p",
        provider="stock",
        output_ext="jpg",
    )

    assert footage_judge_attempt_budget([photo], ["pexels"], configured=200) == 200
    assert footage_judge_attempt_budget([photo], ["pexels"], configured=5) == 10  # margin only


def test_the_router_is_resized_so_the_judge_never_starves() -> None:
    from video_editor_llm.config import LLMConfig
    from video_editor_llm.router import ModelRouter
    from video_editor_pipelines.explainer_footage import size_router_for_footage

    router = ModelRouter(config=LLMConfig(max_llm_attempts_per_job=50))
    requests = _stock_video_requests(14)

    budget = size_router_for_footage(router, requests, ["pexels", "pixabay"])

    assert budget == router.config.max_llm_attempts_per_job == 94
    # attempt 51 used to raise LLMBudgetExceededError; now the router accepts it
    router._attempts = 50
    router._record_attempt("critique", "m", "p")
    # a config that already allows more is left alone
    big = ModelRouter(config=LLMConfig(max_llm_attempts_per_job=500))
    assert size_router_for_footage(big, requests, ["pexels", "pixabay"]) == 500
