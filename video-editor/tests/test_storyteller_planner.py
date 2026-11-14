"""Tests for build_storyteller_scene_plan() (mocked LLM, providers, and ffmpeg)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from video_editor_asset_ingestion.models import AssetResult, AssetType, BatchResult

FIXTURE = Path(__file__).parent / "fixtures" / "storyteller" / "sample_story.md"


# The planner's default ``project_dir`` is ``projects/_storyteller_tmp/`` in the
# CWD when tests do not pass one explicitly. Under xdist, tests from this file
# can run on several workers at once, so a shared cache directory races. Give
# every test its own project dir unless it passes one explicitly.
@pytest.fixture(autouse=True)
def _isolate_storyteller_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from video_editor_engine.planner import storyteller_planner

    original = storyteller_planner.build_storyteller_scene_plan

    async def wrapped(*args: object, **kwargs: object) -> object:
        kwargs.setdefault("project_dir", tmp_path / "storyteller")
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(storyteller_planner, "build_storyteller_scene_plan", wrapped)


def _fake_llm_router() -> MagicMock:
    """Return a router whose .generate() returns a deterministic JSON scene plan."""
    router = MagicMock()
    router.generate = AsyncMock(
        side_effect=[
            # Motion policy for the budget template (called FIRST by the planner)
            json.dumps(
                {
                    "scenes": [
                        {"id": "scene_001", "needs_motion": False},
                        {"id": "scene_002", "needs_motion": True},
                        {"id": "scene_003", "needs_motion": False},
                    ]
                }
            ),
            # Image-prompt derivation for the narrator scene
            json.dumps({"visual_description": "wide shot of a coastal kingdom at dawn"}),
            # Image-prompt derivation for the king scene
            json.dumps({"visual_description": "throne room, candles, king addressing court"}),
            # Image-prompt derivation for the princess scene
            json.dumps({"visual_description": "young princess at a stone window"}),
        ]
    )
    return router


def _fake_tts() -> MagicMock:
    tts = MagicMock()
    fake_audio = b"\x00" * 48000  # 1 second of silence at 48kHz mono
    tts.synthesize = AsyncMock(return_value=MagicMock(duration_seconds=2.0, audio_bytes=fake_audio))
    return tts


def _fake_asset_manager(
    tmp_path: Path,
    fail_types: set[str] | None = None,
    tts_duration_ms: int = 4000,
) -> MagicMock:
    """Fake manager supporting both all-or-nothing and partial resolution.

    ``fail_types`` names asset types ("video", "tts", ...) that should fail,
    so tests can exercise the motion-fallback path.
    """
    mgr = MagicMock()
    failing = fail_types or set()
    mgr.request_batches = []

    async def fake_resolve_partial(requests):
        mgr.request_batches.append(list(requests))
        results: list[AssetResult] = []
        failures: list[tuple[str, str]] = []
        for req in requests:
            if req.asset_type.value in failing:
                failures.append((req.asset_id, "simulated failure"))
                continue
            ext = (
                ".mp4"
                if req.asset_type.value == "video"
                else ".png"
                if req.asset_type.value == "image"
                else ".mp3"
            )
            path = tmp_path / f"{req.asset_type.value}-{len(results)}{ext}"
            path.write_bytes(b"FAKE")
            is_tts = req.asset_type == AssetType.TTS
            results.append(
                AssetResult(
                    asset_id=req.asset_id,
                    asset_type=req.asset_type,
                    scene_id=req.scene_id,
                    local_path=str(path),
                    duration_ms=tts_duration_ms if is_tts else 4000,
                    metadata={
                        "provider": req.provider,
                        "model": req.params.get("model"),
                        "kind": req.params.get("kind", "tts"),
                        "tier": req.params.get("tier"),
                    },
                )
            )
        return BatchResult(results=results, failures=failures)

    async def fake_resolve(requests):
        batch = await fake_resolve_partial(requests)
        if batch.failures:
            raise RuntimeError(f"{len(batch.failures)} asset request(s) failed")
        return batch.results

    mgr.resolve = fake_resolve
    mgr.resolve_partial = fake_resolve_partial
    return mgr


def _fake_music(tmp_path: Path) -> MagicMock:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicResult

    music = MagicMock()
    out = tmp_path / "bg.mp3"
    out.write_bytes(b"FAKE")
    music.generate_music = AsyncMock(
        return_value=MusicResult(
            provider="poyo_music", model="minimax-music-2.6", path=out, duration_seconds=120.0
        )
    )
    return music


def _fake_sfx(tmp_path: Path) -> MagicMock:
    sfx = MagicMock()

    async def fake_synthesize(prompt: str, output_path: Path) -> MagicMock:
        Path(output_path).write_bytes(b"FAKE")
        return MagicMock(
            provider="elevenlabs_sfx",
            model="sfx_v1",
            path=output_path,
            prompt=prompt,
            duration_seconds=2.0,
            asset_type=MagicMock(value="audio"),
        )

    sfx.synthesize = fake_synthesize
    return sfx


@pytest.mark.asyncio
async def test_storyteller_image_requests_default_to_the_chain(tmp_path: Path) -> None:
    """Storyteller stills must be routed through ``assets.image_chain`` by default."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    manager = _fake_asset_manager(tmp_path)
    await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=manager,
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    image_requests = [
        request
        for batch in manager.request_batches
        for request in batch
        if request.asset_type == AssetType.IMAGE
    ]
    assert image_requests
    assert {request.provider for request in image_requests} == {"auto"}


@pytest.mark.asyncio
async def test_storyteller_explicit_image_provider_wins(tmp_path: Path) -> None:
    """A user-supplied provider must reach the requests unchanged."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    manager = _fake_asset_manager(tmp_path)
    await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=manager,
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
        image_provider="poyo_ai",
    )

    image_requests = [
        request
        for batch in manager.request_batches
        for request in batch
        if request.asset_type == AssetType.IMAGE
    ]
    assert {request.provider for request in image_requests} == {"poyo_ai"}


@pytest.mark.asyncio
async def test_storyteller_hd_respects_motion_override(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    assert len(plan.scenes) == 3
    assert [s.props.get("needs_motion") for s in plan.scenes] == [False, True, False]
    # The motion scene (scene_002) is rendered as a video, the others as images
    assert [s.props.get("render_track") for s in plan.scenes] == ["image", "video", "image"]


@pytest.mark.asyncio
async def test_storyteller_budget_uses_llm_motion_policy(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    assert [s.props.get("needs_motion") for s in plan.scenes] == [False, True, False]
    # Budget model IDs
    assert plan.scenes[0].props.get("image_model") == "flux-schnell"
    assert plan.scenes[1].props.get("motion_model") == "wan2.2-image-to-video-fast"


@pytest.mark.asyncio
async def test_visual_override_skips_llm_call(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    router = _fake_llm_router()
    plan = await build_storyteller_scene_plan(
        script,
        router,
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )
    # The narrator scene has a `> visual:` override; the planner uses it as-is
    assert "aerial wide shot" in plan.scenes[0].visual_description
    # So the first LLM call (image-prompt derivation) is NOT for scene_001
    # — the call count is: 2 derivations (king, princess) + 1 motion policy? No, HD = no motion policy.
    # Total: 2 LLM calls (king + princess)
    assert router.generate.await_count == 2


@pytest.mark.asyncio
async def test_music_mood_inherited_from_script(tmp_path: Path) -> None:
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    music = _fake_music(tmp_path)
    await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=music,
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )
    call_args = music.generate_music.await_args
    assert "cinematic" in (call_args.args[0] if call_args.args else call_args.kwargs["prompt"])


@pytest.mark.asyncio
async def test_premium_preset_uses_premium_models(tmp_path: Path) -> None:
    """The storyteller_premium preset (premium, premium) must use seedance-2 for motion scenes."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    # Make king scene's `> motion: true` override trigger motion path
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_premium",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )
    # King scene had `> motion: true` so it goes through the motion path.
    motion_scene = next(s for s in plan.scenes if s.props["needs_motion"])
    assert motion_scene.props["motion_model"] == "seedance-2"
    assert motion_scene.props["motion_resolution"] == "1080p"
    assert motion_scene.props["video_tier"] == "premium"
    # All scenes record the image tier even when no image is generated
    for s in plan.scenes:
        assert s.props["image_tier"] == "premium"


@pytest.mark.asyncio
async def test_explicit_tier_overrides_template_preset(tmp_path: Path) -> None:
    """--image-tier premium --video-tier budget on a storyteller_hd job must use flux-schnell + seedance-2."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_hd",
        image_tier="budget",
        video_tier="premium",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )
    # All still-image scenes pick up the budget image tier
    image_scenes = [s for s in plan.scenes if not s.props["needs_motion"]]
    assert all(s.props["image_model"] == "flux-schnell" for s in image_scenes)
    # Motion scene picks up the premium motion tier
    motion_scenes = [s for s in plan.scenes if s.props["needs_motion"]]
    assert all(s.props["motion_model"] == "seedance-2" for s in motion_scenes)


@pytest.mark.asyncio
async def test_motion_scene_keeps_image_asset_as_fallback(tmp_path: Path) -> None:
    """A motion scene must carry a still as well as its video.

    Every renderer picks ``video`` first, so the still is invisible while the
    video exists -- but it is what the scene falls back to when motion
    generation or rendering fails.
    """
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=_fake_asset_manager(tmp_path),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    motion_scene = plan.scenes[1]
    kinds = [a.asset_type.value for a in motion_scene.assets]
    assert kinds.count("video") == 1
    assert kinds.count("image") == 1
    # The video still wins at render time.
    assert motion_scene.props["render_track"] == "video"


@pytest.mark.asyncio
async def test_motion_failure_falls_back_to_image(tmp_path: Path) -> None:
    """Video generation failure degrades the scene to its still, not the job."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    plan = await build_storyteller_scene_plan(
        script,
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=_fake_asset_manager(tmp_path, fail_types={"video"}),
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    motion_scene = plan.scenes[1]
    kinds = [a.asset_type.value for a in motion_scene.assets]
    assert "video" not in kinds
    assert kinds.count("image") == 1
    assert motion_scene.props["render_track"] == "image"
    # The intent is still recorded, but the scene is no longer a motion scene.
    assert motion_scene.props["needs_motion"] is False
    assert motion_scene.props["motion_requested"] is True


@pytest.mark.asyncio
async def test_tts_failure_still_fails_the_plan(tmp_path: Path) -> None:
    """Partial resolution must not swallow failures of required assets."""
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    script = parse_storyteller_script(FIXTURE)
    with pytest.raises(RuntimeError, match="tts"):
        await build_storyteller_scene_plan(
            script,
            _fake_llm_router(),
            template_name="storyteller_budget",
            asset_manager=_fake_asset_manager(tmp_path, fail_types={"tts"}),
            music_provider=_fake_music(tmp_path),
            sfx_provider=_fake_sfx(tmp_path),
            tts_provider=_fake_tts(),
        )


@pytest.mark.asyncio
async def test_motion_request_duration_follows_tts(tmp_path: Path) -> None:
    """The clip must be generated at the spoken scene length, not a fixed 4 s.

    A 4 s narration makes a 4.6 s scene; the clip is asked for at the next
    whole second so it covers the scene instead of ending early and freezing.
    """
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    mgr = _fake_asset_manager(tmp_path, tts_duration_ms=4000)
    await build_storyteller_scene_plan(
        parse_storyteller_script(FIXTURE),
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=mgr,
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    video_reqs = [
        req for batch in mgr.request_batches for req in batch if req.asset_type == AssetType.VIDEO
    ]
    assert len(video_reqs) == 1
    assert video_reqs[0].params["duration_seconds"] == 5  # ceil(4.0 s * 1.15)


@pytest.mark.asyncio
async def test_motion_request_duration_is_clamped(tmp_path: Path) -> None:
    """A very long narration must not ask for an unsupported clip length."""
    from video_editor_core.models.motion import MOTION_DURATION_MAX
    from video_editor_engine.planner.storyteller_planner import (
        build_storyteller_scene_plan,
        parse_storyteller_script,
    )

    mgr = _fake_asset_manager(tmp_path, tts_duration_ms=20000)  # 23 s scene
    await build_storyteller_scene_plan(
        parse_storyteller_script(FIXTURE),
        _fake_llm_router(),
        template_name="storyteller_budget",
        asset_manager=mgr,
        music_provider=_fake_music(tmp_path),
        sfx_provider=_fake_sfx(tmp_path),
        tts_provider=_fake_tts(),
    )

    video_reqs = [
        req for batch in mgr.request_batches for req in batch if req.asset_type == AssetType.VIDEO
    ]
    assert video_reqs[0].params["duration_seconds"] == MOTION_DURATION_MAX


def test_invalid_tier_raises() -> None:
    from video_editor_engine.planner.storyteller_planner import _resolve_tiers

    with pytest.raises(ValueError, match="invalid image_tier"):
        _resolve_tiers("storyteller_hd", image_tier="garbage", video_tier=None)
    with pytest.raises(ValueError, match="invalid video_tier"):
        _resolve_tiers("storyteller_hd", image_tier=None, video_tier="garbage")
    with pytest.raises(ValueError, match="unknown storyteller template"):
        _resolve_tiers("storyteller_xl", image_tier=None, video_tier=None)


def test_budget_motion_uses_the_poyo_catalog_id_and_bills_per_request():
    from video_editor_asset_ingestion.models import AssetRequest, AssetType
    from video_editor_asset_ingestion.pricing import PricingRegistry, estimate_video_cost
    from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider
    from video_editor_engine.planner.storyteller_planner import VIDEO_TIER_MODELS

    model, resolution = VIDEO_TIER_MODELS["budget"]
    assert model == "wan2.2-image-to-video-fast"
    registry = PricingRegistry.load()
    params = {"model_kwargs": {"resolution": resolution}}
    # one fixed-length clip per request, whatever duration is asked for
    assert estimate_video_cost("poyo_ai", model, 8.0, registry, params=params) == 0.03
    assert estimate_video_cost("poyo_ai", model, 8.0, registry, params=params, count=3) == 0.09
    request = AssetRequest(
        asset_id="m1",
        job_id="j",
        scene_id="scene_1",
        asset_type=AssetType.VIDEO,
        prompt="gentle parallax",
        params={"duration_seconds": 6, "model_kwargs": {"image_urls": ["https://x/y.png"]}},
    )
    payload = PoyoAIProvider.__new__(PoyoAIProvider)._build_payload(request, model)
    sent = payload.get("input", payload)
    assert "duration" not in sent and "aspect_ratio" not in sent and "generate_audio" not in sent
    assert sent["resolution"] == "480p"
