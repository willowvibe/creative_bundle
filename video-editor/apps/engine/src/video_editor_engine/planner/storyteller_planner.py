"""Storyteller planner: parse a persona-tagged script and build a ScenePlan.

Public API:
- ``parse_storyteller_script(path)`` → ``StorytellerScript``
- ``build_storyteller_scene_plan(script, llm_router, ...)`` → ``ScenePlan`` (Task 7)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_core.models.motion import (
    motion_duration_for,
)
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_core.models.storyteller_script import (
    CastMember,
    StorytellerScene,
    StorytellerScript,
)
from video_editor_core.templates.tiers import STORYTELLER_TIER_PRESETS

if TYPE_CHECKING:
    from video_editor_asset_ingestion.manager import AssetIngestionManager
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider
    from video_editor_llm.router import ModelRouter

logger = structlog.get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_DIRECTIVE_RE = re.compile(r"^>\s*(visual|motion|language|sfx)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_SPEAKER_RE = re.compile(r"^##\s+Speaker:\s*(\S+)\s*$")
_BARE_BLOCKQUOTE_RE = re.compile(r"^>\s*(.+?)\s*$")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) or raise ValueError."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(
            "storyteller script must start with a YAML frontmatter block delimited by '---'"
        )
    try:
        import yaml

        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    return fm, m.group(2)


def _scene_id(index: int) -> str:
    return f"scene_{index + 1:03d}"


def _parse_scenes(
    body: str, cast: dict[str, CastMember], default_language: str
) -> list[StorytellerScene]:
    """Walk the body and emit one StorytellerScene per ## Speaker: heading."""
    scenes: list[StorytellerScene] = []
    current_speaker: str | None = None
    current_lines: list[str] = []
    current_visual: str | None = None
    current_sfx: list[str] = []
    current_lang_override: str | None = None
    current_motion_override: bool | None = None
    bare_blockquote_pending: bool = False  # true after heading, before any text
    # Bug I2: a `> visual:` or `> sfx:` directive may be followed by bare-
    # blockquote continuation lines (lines starting with `>` but no `kind:`).
    # Those continuations must fold into the directive value, not into the
    # scene narration text. Continuation ends at a blank line or any
    # non-blockquote line (including a new directive).
    visual_continuation: bool = False
    sfx_continuation: bool = False

    def flush() -> None:
        nonlocal \
            current_speaker, \
            current_lines, \
            current_visual, \
            current_sfx, \
            current_lang_override, \
            current_motion_override, \
            bare_blockquote_pending, \
            visual_continuation, \
            sfx_continuation
        if current_speaker is None:
            return
        text = "\n".join(current_lines).strip()
        if not text:
            return
        if current_speaker not in cast:
            raise ValueError(
                f"speaker {current_speaker!r} is not in cast (declared speakers: {sorted(cast)})"
            )
        member = cast[current_speaker]
        resolved_lang = current_lang_override or member.language or default_language
        scenes.append(
            StorytellerScene(
                id=_scene_id(len(scenes)),
                speaker=current_speaker,
                text=text,
                language=resolved_lang,
                voice_id=member.voice_id,
                visual_override=current_visual,
                sfx_prompts=list(current_sfx),
                motion_override=current_motion_override,
            )
        )
        current_speaker = None
        current_lines = []
        current_visual = None
        current_sfx = []
        current_lang_override = None
        current_motion_override = None
        bare_blockquote_pending = False
        visual_continuation = False
        sfx_continuation = False

    for raw_line in body.splitlines():
        speaker_m = _SPEAKER_RE.match(raw_line)
        if speaker_m:
            flush()
            current_speaker = speaker_m.group(1)
            bare_blockquote_pending = True
            continue
        if current_speaker is None:
            # Skip blank lines and stray non-heading content before the first speaker.
            continue

        # Bug I2: continuation lines for a preceding `> visual:` or `> sfx:`
        # directive. Continuation ends at a blank line, a new directive, or
        # any non-blockquote line.
        if visual_continuation or sfx_continuation:
            if not raw_line.strip():
                visual_continuation = False
                sfx_continuation = False
                bare_blockquote_pending = False
                continue
            cont_m = _BARE_BLOCKQUOTE_RE.match(raw_line)
            cont_directive_m = _DIRECTIVE_RE.match(raw_line)
            if cont_m and not cont_directive_m:
                if visual_continuation:
                    current_visual = f"{current_visual} {cont_m.group(1)}".strip()
                elif sfx_continuation:
                    current_sfx[-1] = f"{current_sfx[-1]} {cont_m.group(1)}".strip()
                continue
            # Not a continuation — end continuation and fall through to
            # normal directive/text handling below.
            visual_continuation = False
            sfx_continuation = False

        directive_m = _DIRECTIVE_RE.match(raw_line)
        if directive_m:
            kind = directive_m.group(1).lower()
            value = directive_m.group(2)
            bare_blockquote_pending = False
            if kind == "visual":
                current_visual = value
                visual_continuation = True
            elif kind == "sfx":
                current_sfx.append(value)
                sfx_continuation = True
            elif kind == "language":
                current_lang_override = value
            elif kind == "motion":
                current_motion_override = value.strip().lower() in {"true", "1", "yes"}
            continue
        bare_m = _BARE_BLOCKQUOTE_RE.match(raw_line)
        if bare_m and bare_blockquote_pending:
            # Bare blockquote directly under a heading → SFX shorthand
            current_sfx.append(bare_m.group(1))
            bare_blockquote_pending = False
            continue
        if not raw_line.strip():
            bare_blockquote_pending = False
            continue
        current_lines.append(raw_line)
        bare_blockquote_pending = False

    flush()
    return scenes


def parse_storyteller_script(path: Path) -> StorytellerScript:
    """Parse a markdown script file into a StorytellerScript."""
    text = Path(path).read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    cast_raw = fm.get("cast", {})
    if not cast_raw:
        raise ValueError("frontmatter must declare a non-empty `cast:` map")
    cast = {
        name: CastMember(
            voice_id=member["voice_id"],
            language=member.get("language", fm.get("language", "en")),
            volume=float(member.get("volume", 1.0)),
        )
        for name, member in cast_raw.items()
    }
    scenes = _parse_scenes(body, cast, default_language=fm.get("language", "en"))
    if not scenes:
        raise ValueError("script has no `## Speaker:` scenes after frontmatter")
    return StorytellerScript(
        language=fm.get("language", "en"),
        music_mood=fm.get("music_mood", "cinematic"),
        title=fm.get("title", ""),
        cast=cast,
        scenes=scenes,
    )


# ---------------------------------------------------------------------------
# build_storyteller_scene_plan() — Task 10
#
# Quality-tier model tables (per docs/POYO_ROUTING.md tiers). Users pick one
# of each independently via --image-tier / --video-tier; template presets map
# to (image_tier, video_tier) pairs.
# ---------------------------------------------------------------------------

VIDEO_TIER_MODELS: dict[str, tuple[str, str]] = {
    # tier -> (model, resolution)
    "budget": ("wan2.2-image-to-video-fast", "480p"),  # ~$0.030/gen, gentle parallax
    "standard": ("seedance-2-fast", "720p"),  # ~$0.07-$0.14/sec, default
    "premium": ("seedance-2", "1080p"),  # ~$0.31/sec, cinematic
}

# Motion clips are generated at the scene's spoken length, clamped to the
# supported generation windows. The rule lives in ``models.motion`` so the
# asset stage (``ve assets``) applies the same one (#257); the constants are
# re-exported here for callers that already import them from this module.

IMAGE_TIER_MODELS: dict[str, str] = {
    "budget": "flux-schnell",  # ~$0.002
    "standard": "gpt-image-2",  # default; the planner pins 1K low via metadata
    "premium": "gpt-image-2",  # 2K/4K high via metadata "image_size"
}

IMAGE_TIER_SIZE: dict[str, str] = {
    "budget": "1024x1024",
    "standard": "1024x1024",  # 1K low
    "premium": "2048x2048",  # 2K high (override via metadata if 4K is needed)
}

# Template-preset -> (image_tier, video_tier) defaults. Re-exported under the
# old name for callers/tests; the data lives beside the templates so the
# planner and ``Template.quality_tier_pair`` cannot drift.
TEMPLATE_TIER_PRESETS = STORYTELLER_TIER_PRESETS


def _resolve_tiers(
    template_name: str,
    image_tier: str | None,
    video_tier: str | None,
) -> tuple[str, str]:
    """Resolve (image_tier, video_tier) from template preset + optional overrides.

    CLI overrides win over template defaults. Invalid tier values raise
    ``ValueError`` with a list of accepted values.
    """
    if template_name not in TEMPLATE_TIER_PRESETS:
        raise ValueError(
            f"unknown storyteller template {template_name!r}; "
            f"valid: {sorted(TEMPLATE_TIER_PRESETS)}"
        )
    base_image, base_video = TEMPLATE_TIER_PRESETS[template_name]
    image = image_tier or base_image
    video = video_tier or base_video
    if image not in IMAGE_TIER_MODELS:
        raise ValueError(f"invalid image_tier {image!r}; valid: {sorted(IMAGE_TIER_MODELS)}")
    if video not in VIDEO_TIER_MODELS:
        raise ValueError(f"invalid video_tier {video!r}; valid: {sorted(VIDEO_TIER_MODELS)}")
    return image, video


def _cache_key(text: str, speaker: str, prev_visual: str) -> str:
    return hashlib.sha256(f"{text}|{speaker}|{prev_visual}".encode()).hexdigest()


def _load_cache(project_dir: Path, filename: str) -> dict[str, Any]:
    cache_path = project_dir / ".cache" / filename
    if not cache_path.exists():
        return {}
    try:
        return cast(dict[str, Any], json.loads(cache_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(project_dir: Path, filename: str, cache: dict[str, Any]) -> None:
    cache_path = project_dir / ".cache" / filename
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


async def _derive_visual_description(
    text: str,
    speaker: str,
    prev_visual: str,
    llm_router: ModelRouter,
    cache: dict[str, str],
) -> str:
    key = _cache_key(text, speaker, prev_visual)
    if key in cache:
        return cache[key]
    prompt = (
        "You are an image-prompt writer for a story video. Given a piece of narration "
        "and the previous scene's visual, output ONE rich, generative image prompt "
        "suitable for a single 16:9 frame.\n\n"
        f"Speaker: {speaker}\n"
        f"Previous scene visual: {prev_visual or 'none'}\n"
        f"Narration: {text}\n\n"
        "Return ONLY the prompt, no preamble, no JSON."
    )
    response = await llm_router.generate(task_type="scene_plan", prompt=prompt)
    cache[key] = response.strip()
    return cache[key]


def _resolve_motion_policy(
    template_name: Literal["storyteller_hd", "storyteller_budget"],
    scenes: list[StorytellerScene],
) -> dict[str, bool]:
    """Return a {scene_id: needs_motion} map per template's policy.

    HD: honour ``> motion: true``; otherwise false.
    Budget: leave all None — the caller will fill it in via the LLM call.
    """
    if template_name == "storyteller_hd":
        return {s.id: (s.motion_override is True) for s in scenes}
    return {s.id: False for s in scenes}


async def _budget_motion_policy(
    scenes: list[StorytellerScene],
    llm_router: ModelRouter,
    cache: dict[str, Any],
    project_dir: Path,
) -> dict[str, bool]:
    cache_key = hashlib.sha256(
        json.dumps([(s.id, s.text, s.speaker) for s in scenes], sort_keys=True).encode()
    ).hexdigest()
    if cache_key in cache:
        return cast(dict[str, bool], cache[cache_key])
    payload = [{"id": s.id, "speaker": s.speaker, "text": s.text} for s in scenes]
    prompt = (
        "For each story scene below, decide needs_motion (true if the narration "
        "describes movement, action, scene-establishing, or anything that would feel "
        "static as a still image; false for portraits, dialogue, contemplation).\n"
        "Aim for ~50% true. Prefer false for short lines.\n"
        f"Scenes: {json.dumps(payload)}\n\n"
        'Return ONLY JSON: {"scenes": [{"id": "...", "needs_motion": true|false}, ...]}'
    )
    response = await llm_router.generate(task_type="scene_plan", prompt=prompt)
    parsed = json.loads(response)
    result = {item["id"]: bool(item["needs_motion"]) for item in parsed.get("scenes", [])}
    cache[cache_key] = result
    return result


async def build_storyteller_scene_plan(
    script: StorytellerScript,
    llm_router: ModelRouter,
    *,
    template_name: Literal["storyteller_budget", "storyteller_hd", "storyteller_premium"],
    asset_manager: AssetIngestionManager,
    music_provider: MusicProvider,
    sfx_provider: Any,
    tts_provider: Any,
    project_dir: Path | None = None,
    on_scene_ready: Callable[[Scene], Awaitable[None]] | None = None,
    image_tier: str | None = None,
    video_tier: str | None = None,
    image_provider: str = "auto",
) -> ScenePlan:
    """Plan + generate assets for a StorytellerScript and return a ScenePlan.

    ``image_tier`` and ``video_tier`` override the template preset's defaults
    independently (e.g. ``--image-tier premium --video-tier standard``).
    ``image_provider`` defaults to ``"auto"`` so stills walk the configured
    ``assets.image_chain``; an explicit name bypasses the chain.
    """
    image_tier, video_tier = _resolve_tiers(template_name, image_tier, video_tier)
    if project_dir is None:
        project_dir = Path("projects/_storyteller_tmp")
    project_dir.mkdir(parents=True, exist_ok=True)
    image_cache = _load_cache(project_dir, "image_prompts.json")
    motion_cache = _load_cache(project_dir, "motion_policy.json")

    # 1. Resolve motion policy. Budget preset = LLM picks motion; HD/Premium = user marks `> motion: true`.
    if template_name == "storyteller_budget":
        motion = await _budget_motion_policy(script.scenes, llm_router, motion_cache, project_dir)
    else:
        motion = {s.id: (s.motion_override is True) for s in script.scenes}

    # 2. Per-scene: derive visual, generate assets
    image_model = IMAGE_TIER_MODELS[image_tier]
    motion_model, motion_resolution = VIDEO_TIER_MODELS[video_tier]
    image_size = IMAGE_TIER_SIZE[image_tier]

    plan_scenes: list[Scene] = []
    prev_visual = ""
    for _i, story_scene in enumerate(script.scenes):
        # Visual description
        if story_scene.visual_override:
            visual = story_scene.visual_override
            prompt_source = "override"
        else:
            visual = await _derive_visual_description(
                story_scene.text, story_scene.speaker, prev_visual, llm_router, image_cache
            )
            prompt_source = "llm"

        # Asset requests. Speech and the still resolve first: the scene's
        # duration comes from the TTS result, and the motion clip is requested
        # afterwards at that length (see the second batch below).
        needs_motion = motion.get(story_scene.id, False)
        requests: list[AssetRequest] = []
        # Always request the still. Every renderer prefers a video asset, so on
        # a motion scene it is invisible while the clip exists -- and it is the
        # fallback when generation fails or a renderer cannot play the clip.
        # At image-tier prices it is a small fraction of the video cost.
        requests.append(
            AssetRequest(
                asset_id=f"{story_scene.id}-image",
                job_id=project_dir.name,
                scene_id=story_scene.id,
                asset_type=AssetType.IMAGE,
                prompt=visual,
                provider=image_provider,
                params={
                    "model": image_model,
                    "aspect_ratio": "16:9",
                    "image_size": image_size,
                    "tier": image_tier,
                },
            )
        )

        # TTS
        requests.append(
            AssetRequest(
                asset_id=f"{story_scene.id}-tts",
                job_id=project_dir.name,
                scene_id=story_scene.id,
                asset_type=AssetType.TTS,
                prompt=story_scene.text,
                provider="tts_router",
                params={
                    "model": "auto",
                    "voice_id": story_scene.voice_id,
                    "language": story_scene.language,
                },
            )
        )

        # SFX
        for sfx_idx, sfx_prompt in enumerate(story_scene.sfx_prompts):
            requests.append(
                AssetRequest(
                    asset_id=f"{story_scene.id}-sfx-{sfx_idx}",
                    job_id=project_dir.name,
                    scene_id=story_scene.id,
                    asset_type=AssetType.SFX,
                    prompt=sfx_prompt,
                    provider="elevenlabs_sfx",
                    params={
                        "model": "sfx_v1",
                        "kind": "sfx",
                    },
                )
            )

        # Resolve partially so a failed motion clip degrades the scene to its
        # still instead of failing the whole job. Speech and SFX are required;
        # the still is required only when no clip will cover the scene.
        batch = await asset_manager.resolve_partial(requests)
        failed_ids = {asset_id for asset_id, _reason in batch.failures}
        required_ids = {
            r.asset_id
            for r in requests
            if r.asset_type in (AssetType.TTS, AssetType.SFX)
            or (r.asset_type == AssetType.IMAGE and not needs_motion)
        }
        missing = sorted(required_ids & failed_ids)
        if missing:
            raise RuntimeError(f"required asset request(s) failed: {', '.join(missing)}")
        results = batch.results

        # Compute duration from TTS result
        tts_results = [r for r in results if r.asset_type == AssetType.TTS]
        if tts_results and tts_results[0].duration_ms > 0:
            duration = (tts_results[0].duration_ms / 1000.0) * 1.15
        else:
            duration = 4.0  # safe default for a silent scene

        # Motion is requested only now, at the scene's spoken length, so the
        # clip cannot end early and hold its last frame.
        motion_duration: int | None = None
        if needs_motion:
            motion_duration = motion_duration_for(duration)
            motion_batch = await asset_manager.resolve_partial(
                [
                    AssetRequest(
                        asset_id=f"{story_scene.id}-video",
                        job_id=project_dir.name,
                        scene_id=story_scene.id,
                        asset_type=AssetType.VIDEO,
                        prompt=visual,
                        provider="poyo_ai",
                        params={
                            "model": motion_model,
                            "resolution": motion_resolution,
                            "duration_seconds": motion_duration,
                            "tier": video_tier,
                        },
                    )
                ]
            )
            failed_ids |= {aid for aid, _reason in motion_batch.failures}
            results = [*results, *motion_batch.results]

        image_ok = any(r.asset_type == AssetType.IMAGE for r in results)
        has_video = any(r.asset_type == AssetType.VIDEO for r in results)
        if not (image_ok or has_video):
            raise RuntimeError(f"required asset request(s) failed: {story_scene.id} visual")
        if failed_ids:
            logger.warning(
                "storyteller_asset_fallback",
                job_id=project_dir.name,
                scene_id=story_scene.id,
                failed=sorted(failed_ids),
            )

        scene = Scene(
            id=story_scene.id,
            type="story",
            duration_seconds=duration,
            narration=story_scene.text,
            visual_description=visual,
            asset_query="",
            props={
                "speaker": story_scene.speaker,
                "language": story_scene.language,
                "voice_id": story_scene.voice_id,
                "tts_text": story_scene.text,
                # ``needs_motion`` reflects what actually landed, so downstream
                # (FableCut export/import) never claims a video track that fell
                # back to its still. ``motion_requested`` keeps the intent.
                "needs_motion": has_video,
                "motion_requested": needs_motion,
                "motion_model": motion_model if needs_motion else None,
                "motion_resolution": motion_resolution if needs_motion else None,
                "motion_duration_seconds": motion_duration,
                "image_model": image_model,
                "image_size": image_size,
                "image_tier": image_tier,
                "video_tier": video_tier,
                "sfx_prompts": list(story_scene.sfx_prompts),
                "music_mood": script.music_mood,
                "render_track": "video" if has_video else "image",
                "visual_prompt_source": prompt_source,
            },
            assets=results,
        )
        plan_scenes.append(scene)
        prev_visual = visual
        if on_scene_ready is not None:
            await on_scene_ready(scene)

    # 3. Background music — single track for the whole story
    total_duration = sum(s.duration_seconds for s in plan_scenes)
    music_prompt = f"{script.title or 'story'} ({script.music_mood}), ~{int(total_duration)}s"
    music_path = project_dir / "assets" / "music" / "bg.mp3"
    music_path.parent.mkdir(parents=True, exist_ok=True)
    await music_provider.generate_music(music_prompt, total_duration, music_path)

    # 4. Persist caches
    _save_cache(project_dir, "image_prompts.json", image_cache)
    _save_cache(project_dir, "motion_policy.json", motion_cache)

    return ScenePlan(
        job_id=project_dir.name,
        script="",  # the original markdown is on disk; ScenePlan only carries the resolved plan
        scenes=plan_scenes,
        aspect_ratio="16:9",
        music_mood=script.music_mood,
        music_path=str(music_path),
    )
