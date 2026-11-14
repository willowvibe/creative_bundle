"""Tests for scene-level asset request derivation."""

from __future__ import annotations

from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_asset_ingestion.scene_assets import (
    attach_results_to_plan,
    image_reuse_target,
    requests_from_plan,
    requests_from_scene,
    resolve_image_reuse,
    scene_wants_motion,
)
from video_editor_core.models.scene_plan import Scene, ScenePlan


def _scene(**overrides: object) -> Scene:
    defaults: dict[str, object] = {
        "id": "scene_001",
        "type": "narration",
        "duration_seconds": 5.0,
        "narration": "Hello world.",
        "visual_description": "A welcoming scene.",
        "asset_query": "",
        "props": {},
    }
    defaults.update(overrides)
    return Scene(**defaults)  # type: ignore[arg-type]


def test_requires_motion_boolean() -> None:
    assert scene_wants_motion(_scene(props={"requires_motion": True})) is True
    assert scene_wants_motion(_scene(props={"requires_motion": False})) is False


def test_requires_motion_string() -> None:
    assert scene_wants_motion(_scene(props={"requires_motion": "true"})) is True
    assert scene_wants_motion(_scene(props={"requires_motion": "False"})) is False


def test_requires_motion_missing() -> None:
    assert scene_wants_motion(_scene(props={})) is False


def test_static_scene_emits_only_image() -> None:
    scene = _scene(props={"requires_motion": False})
    requests = requests_from_scene(scene, "job-1", video_provider="stub_hyperframes_video")

    types = [r.asset_type for r in requests]
    assert types == [AssetType.TTS, AssetType.IMAGE]
    assert all(r.provider != "stub_hyperframes_video" for r in requests)


def test_motion_scene_emits_video_before_image() -> None:
    scene = _scene(props={"requires_motion": True})
    requests = requests_from_scene(scene, "job-1", video_provider="stub_hyperframes_video")

    types = [r.asset_type for r in requests]
    assert types == [AssetType.TTS, AssetType.VIDEO, AssetType.IMAGE]
    video = requests[1]
    assert video.asset_type == AssetType.VIDEO
    assert video.provider == "stub_hyperframes_video"
    assert video.params.get("duration_seconds") == 5.0


def test_video_provider_without_motion_flag_still_image_only() -> None:
    scene = _scene(props={})
    requests = requests_from_scene(scene, "job-1", video_provider="stub_hyperframes_video")

    types = [r.asset_type for r in requests]
    assert AssetType.VIDEO not in types
    assert types == [AssetType.TTS, AssetType.IMAGE]


def test_image_request_carries_the_planned_model() -> None:
    """The scene's ``image_model`` must reach the asset request.

    ``ve estimate`` prices a request from its own model, so without this the
    estimate falls back to the provider default and quotes a model the job will
    never call (#262).
    """
    scene = _scene(props={"image_model": "nano-banana-2-lite"})
    requests = requests_from_scene(scene, "job-1", image_provider="poyo_ai")

    image = next(r for r in requests if r.asset_type == AssetType.IMAGE)
    assert image.params.get("model") == "nano-banana-2-lite"


def test_video_request_carries_the_planned_motion_model() -> None:
    scene = _scene(props={"requires_motion": True, "motion_model": "seedance-2-fast"})
    requests = requests_from_scene(scene, "job-1", video_provider="poyo_ai")

    video = next(r for r in requests if r.asset_type == AssetType.VIDEO)
    assert video.params.get("model") == "seedance-2-fast"


def test_image_request_carries_the_planned_resolution() -> None:
    """A plan's ``image_resolution`` must reach the request as ``model_kwargs``.

    Resolution is both what the provider bills for (1.7 vs 6 credits) and what
    ``ve estimate`` must price (#269). Forwarding it here is what lets a plan
    declare its tier instead of the price gate assuming the base one.
    """
    scene = _scene(props={"image_model": "gpt-image-2.5-fixed", "image_resolution": "2K"})
    requests = requests_from_scene(scene, "job-1", image_provider="poyo_ai")

    image = next(r for r in requests if r.asset_type == AssetType.IMAGE)
    assert image.params["model_kwargs"]["resolution"] == "2K"


def test_image_request_omits_model_kwargs_when_no_tier_is_planned() -> None:
    """No ``image_*`` tier props -> no ``model_kwargs`` key at all."""
    scene = _scene(props={"image_model": "nano-banana-2-lite"})
    requests = requests_from_scene(scene, "job-1", image_provider="poyo_ai")

    image = next(r for r in requests if r.asset_type == AssetType.IMAGE)
    assert "model_kwargs" not in image.params


# --- image reuse (#271) -----------------------------------------------------


def _plan(*scenes: Scene) -> ScenePlan:
    return ScenePlan(job_id="job-1", script="script", scenes=list(scenes))


def test_image_reuse_target_reads_the_plan_prop() -> None:
    """``props.image_scene`` names the scene whose still this one shows."""
    assert image_reuse_target(_scene(props={"image_scene": "scene_003"})) == "scene_003"
    assert image_reuse_target(_scene(props={"reuse_image_from": "scene_003"})) == "scene_003"
    assert image_reuse_target(_scene(props={"image_scene": "scene_001"})) is None
    assert image_reuse_target(_scene(props={"image_scene": ""})) is None
    assert image_reuse_target(_scene(props={})) is None


def test_requests_from_plan_skips_the_reused_still() -> None:
    """Three scenes, two stills: the reusing scene must not be billed (#271).

    ``saju-zodiac-animal`` shows five stills across eight scenes, but every
    scene emitted an image request, so the gate priced eight 2K renders.
    """
    plan = _plan(
        _scene(id="scene_001"),
        _scene(id="scene_002", props={"image_scene": "scene_001"}),
        _scene(id="scene_003"),
    )

    requests = requests_from_plan(plan, "job-1", image_provider="poyo_ai")

    image_ids = [r.asset_id for r in requests if r.asset_type == AssetType.IMAGE]
    assert image_ids == ["scene_001_image", "scene_003_image"]


def test_requests_from_plan_generates_when_the_reuse_target_is_unknown() -> None:
    """A typo must not leave a scene with no visual: fall back to generating."""
    plan = _plan(
        _scene(id="scene_001"),
        _scene(id="scene_002", props={"image_scene": "scene_999"}),
    )

    requests = requests_from_plan(plan, "job-1", image_provider="poyo_ai")

    image_ids = [r.asset_id for r in requests if r.asset_type == AssetType.IMAGE]
    assert image_ids == ["scene_001_image", "scene_002_image"]


def test_requests_from_plan_breaks_a_reuse_cycle() -> None:
    """Two scenes pointing at each other both generate rather than loop."""
    plan = _plan(
        _scene(id="scene_001", props={"image_scene": "scene_002"}),
        _scene(id="scene_002", props={"image_scene": "scene_001"}),
    )

    requests = requests_from_plan(plan, "job-1", image_provider="poyo_ai")

    image_ids = [r.asset_id for r in requests if r.asset_type == AssetType.IMAGE]
    assert image_ids == ["scene_001_image", "scene_002_image"]
    assert resolve_image_reuse(plan) == {}


def test_resolve_image_reuse_follows_a_chain_to_its_owner() -> None:
    """scene_003 -> scene_002 -> scene_001 resolves to the scene that owns it."""
    plan = _plan(
        _scene(id="scene_001"),
        _scene(id="scene_002", props={"image_scene": "scene_001"}),
        _scene(id="scene_003", props={"image_scene": "scene_002"}),
    )

    assert resolve_image_reuse(plan) == {
        "scene_002": "scene_001",
        "scene_003": "scene_001",
    }


def test_attach_results_to_plan_shares_the_referenced_still() -> None:
    """A reusing scene renders the owner's file, under its own asset id."""
    plan = _plan(
        _scene(id="scene_001"),
        _scene(id="scene_002", narration="", props={"image_scene": "scene_001"}),
    )
    result = AssetResult(
        asset_id="scene_001_image",
        scene_id="scene_001",
        asset_type=AssetType.IMAGE,
        local_path="/tmp/still.png",
    )

    attach_results_to_plan(plan, [result])

    shared = next(a for a in plan.scenes[1].assets if a.asset_type == AssetType.IMAGE)
    assert shared.local_path == result.local_path
    assert shared.asset_id == "scene_002_image"
    assert shared.scene_id == "scene_002"
    assert shared.metadata["reused_from"] == "scene_001"
    # The owner keeps its own asset; nothing is moved out from under it.
    assert [a.asset_id for a in plan.scenes[0].assets] == ["scene_001_image"]


def test_attach_results_to_plan_leaves_a_scene_without_a_shared_still() -> None:
    """A resolved reuse whose target produced nothing stays empty, not broken."""
    plan = _plan(
        _scene(id="scene_001"),
        _scene(id="scene_002", narration="", props={"image_scene": "scene_001"}),
    )

    attach_results_to_plan(plan, [])

    assert plan.scenes[1].assets == []


# --- plan aspect (#291) -----------------------------------------------------


def test_a_vertical_plan_frames_its_stills() -> None:
    """A 9:16 plan's stills carry the plan's aspect; providers drew 16:9 before."""
    plan = _plan(_scene()).model_copy(update={"aspect_ratio": "9:16"})

    [image] = [
        r
        for r in requests_from_plan(plan, "job-1", image_provider="auto")
        if r.asset_type == AssetType.IMAGE
    ]

    assert image.params["aspect_ratio"] == "9:16"
    assert image.aspect() == (9, 16)


def test_a_16_9_plan_leaves_its_requests_and_cache_keys_alone() -> None:
    plan = _plan(_scene())
    [image] = [
        r
        for r in requests_from_plan(plan, "job-1", image_provider="auto")
        if r.asset_type == AssetType.IMAGE
    ]

    assert "aspect_ratio" not in image.params


def test_a_still_with_its_own_framing_keeps_it() -> None:
    """A planned gpt-image ``size`` preset wins over the plan aspect."""
    plan = _plan(_scene(props={"image_size": "1:1"})).model_copy(update={"aspect_ratio": "9:16"})
    [image] = [
        r
        for r in requests_from_plan(plan, "job-1", image_provider="auto")
        if r.asset_type == AssetType.IMAGE
    ]

    assert "aspect_ratio" not in image.params
    assert image.aspect() == (1, 1)


def test_a_search_provider_gets_keywords_not_a_diffusion_prompt() -> None:
    """Freesound's ``query`` *is* the prompt, so prose searches its own words.

    A designed still's visual description names things like "no people" and
    "layered ridges"; sending it whole makes the search match those words
    instead of the sound the scene needs.
    """
    from video_editor_asset_ingestion.scene_assets import sfx_query_for_scene

    scene = _scene(
        type="composite",
        visual_description=(
            "aerial view of the Sundarbans mangrove delta, branching tidal "
            "channels cutting through dense green forest, no people"
        ),
    )

    assert sfx_query_for_scene(scene, "freesound_sfx") == "water"


def test_a_generative_provider_still_gets_the_whole_description() -> None:
    from video_editor_asset_ingestion.scene_assets import sfx_query_for_scene

    scene = _scene(type="composite", visual_description="a warm room with rain on the window")

    assert sfx_query_for_scene(scene, "elevenlabs_sfx") == "a warm room with rain on the window"


def test_a_typographic_scene_with_no_description_still_gets_its_cut_cue() -> None:
    """Cards and maps carry no visual description; they used to get no SFX."""
    from video_editor_asset_ingestion.scene_assets import (
        sfx_query_for_scene,
        sfx_requests_for_scene,
    )

    card = _scene(id="scene_12", type="typographic", visual_description="")
    plate = _scene(id="scene_2", type="map", visual_description="")

    assert sfx_query_for_scene(card, "freesound_sfx") == "soft impact"
    assert sfx_query_for_scene(plate, "freesound_sfx") == "map whoosh"
    [request] = sfx_requests_for_scene(card, job_id="job", sfx_provider="freesound_sfx")
    assert request.prompt == "soft impact"
    assert request.asset_type == AssetType.SFX


def test_an_unknown_scene_type_with_nothing_to_search_stays_silent() -> None:
    from video_editor_asset_ingestion.scene_assets import sfx_requests_for_scene

    scene = _scene(type="narration", visual_description="")

    assert sfx_requests_for_scene(scene, job_id="job", sfx_provider="freesound_sfx") == []
