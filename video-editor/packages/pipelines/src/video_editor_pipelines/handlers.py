"""Worker dispatch handlers for every queued command.

Each handler receives a ``JobContext`` and a raw ``payload`` dict,
validates paths, and delegates to the appropriate pipeline or skill.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import structlog
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.scene_assets import (
    alignment_request,
    music_request,
    sfx_requests_for_scene,
)
from video_editor_config import Settings
from video_editor_core.constants import REPO_ROOT
from video_editor_core.models.scene_plan import ScenePlan
from video_editor_core.templates import TemplateRouter
from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext

from video_editor_pipelines.pipeline import MusicVideoPipeline, SkillsPipeline

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Path validation helpers (shared by all handlers)
# ---------------------------------------------------------------------------


def _resolve_payload_path(
    path_str: str,
    allowed_roots: list[Path],
    label: str,
) -> Path:
    """Resolve a payload-supplied path and ensure it is inside allowed roots."""
    path = Path(path_str).resolve()
    resolved_roots = [root.resolve() for root in allowed_roots]
    for root in resolved_roots:
        if path.is_relative_to(root):
            return path
    raise ValueError(
        f"{label} must be inside one of the allowed roots: {resolved_roots}; got {path}"
    )


def _job_path(ctx: JobContext, payload: dict[str, Any], key: str) -> Path | None:
    """Resolve an optional payload path that must stay inside the job's dirs (#278).

    A relative value is taken relative to the job's work dir (the
    ``render_fablecut`` convention); ``~`` is expanded. Returns ``None`` when the
    key is absent or empty.
    """
    raw = payload.get(key)
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ctx.work_dir / path
    return _resolve_payload_path(str(path), [ctx.work_dir, ctx.output_dir], key)


def _require_job_path(ctx: JobContext, payload: dict[str, Any], key: str) -> Path:
    """:func:`_job_path` for a key the handler cannot run without."""
    path = _job_path(ctx, payload, key)
    if path is None:
        raise ValueError(f"payload requires {key!r}")
    return path


def _safe_filename(name: Any, label: str = "filename") -> str:
    """Validate a payload-supplied filename and reject path-traversal attempts."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"{label} must be a non-empty string")
    if "/" in name or "\\" in name or name in (".", "..") or ".." in name:
        raise ValueError(
            f"{label} must be a plain filename without path separators or "
            f"parent references; got {name!r}"
        )
    return name


def _resolve_config_path(payload: dict[str, Any], ctx: JobContext) -> Path | None:
    """Resolve a payload ``config_path`` and ensure it is inside the job roots."""
    raw = payload.get("config_path")
    if not raw:
        return None
    return _resolve_payload_path(str(raw), [ctx.work_dir, ctx.output_dir], "config_path")


def _resolve_presets_dir(
    presets_dir: Path | None,
    config_path: Path | None,
) -> Path | None:
    """Return the effective presets directory."""
    from video_editor_config import ProjectConfig

    if presets_dir is not None:
        return presets_dir
    project_config = ProjectConfig.load(config_path)
    repo_root = config_path.resolve().parent if config_path else REPO_ROOT
    return project_config.presets_dir(repo_root=repo_root)


def _llm_router(
    config_path: Path | None,
    ctx: JobContext | None = None,
) -> Any | None:
    """Build a model router from an optional project config.yaml."""
    from video_editor_llm.router import ModelRouter

    if config_path is None:
        return None

    def _log_callback(op: dict[str, Any]) -> None:
        if ctx is not None:
            ctx.manifest().log_operation(**op, tenant_id=ctx.tenant_id)

    return ModelRouter.from_config(config_path, log_callback=_log_callback)


# v2 skill handlers
# ---------------------------------------------------------------------------


async def _handle_verify(ctx: JobContext, payload: dict[str, Any]) -> None:
    from verify.verify import VerifySkill

    skill = VerifySkill(ctx)
    report = await skill.run()
    logger.info(
        "verify_handler_complete",
        job_id=ctx.job_id,
        overall=report.overall,
        checks=len(report.checks),
    )


async def _handle_run(ctx: JobContext, payload: dict[str, Any]) -> None:
    source_str = payload.get("source")
    if not source_str:
        raise ValueError("'run' payload requires 'source' (path to a video file or a directory)")
    allowed_roots = [ctx.work_dir, ctx.output_dir]
    source = _resolve_payload_path(source_str, allowed_roots, "source")
    if source.is_dir():
        _videos = sorted(
            q for ext in (".mp4", ".mov", ".mkv", ".avi", ".webm") for q in source.glob(f"*{ext}")
        )
        if not _videos:
            raise ValueError(f"No supported media found in {source}")
        source = _videos[0]
    tp = (
        _resolve_payload_path(payload["transcript_path"], allowed_roots, "transcript_path")
        if payload.get("transcript_path")
        else None
    )
    cp = (
        _resolve_payload_path(payload["config_path"], allowed_roots, "config_path")
        if payload.get("config_path")
        else None
    )
    router = TemplateRouter()
    router_overrides: dict[str, Any] = {}
    media_dir = _job_path(ctx, payload, "media_dir")
    if media_dir is not None:
        router_overrides["media_dir"] = str(media_dir)
    selected = router.select(
        source=source, template_name=payload.get("template"), overrides=router_overrides
    )
    ctx.manifest().log_operation(
        operation="template_selected",
        inputs={
            "template": selected.template_name,
            "pipeline_kind": selected.pipeline_kind,
            "preset": selected.preset,
            "aspect_ratio": selected.aspect_ratio,
        },
        tenant_id=ctx.tenant_id,
    )

    if selected.pipeline_kind == "explainer_stills":
        from video_editor_pipelines.explainer_pipeline import (
            check_approved_spend,
            estimate_explainer,
            explainer_job_requests,
            preflight_explainer,
            prepare_explainer,
            run_explainer_job,
        )
        from video_editor_pipelines.explainer_script import load_explainer_script

        if not source.is_file():
            raise ValueError(f"explainer_stills requires a script.md file, got: {source}")
        script = load_explainer_script(source)
        # Validate the free prerequisites (design, caption preset, HyperFrames)
        # before the estimate/approval branch so an unbuildable payload never
        # spends.
        preflight_explainer(script, caption_preset=str(payload.get("caption_preset", "")))
        plan, _reuse = prepare_explainer(ctx, script, reuse=not bool(payload.get("no_reuse")))
        music_path = (
            _resolve_payload_path(payload["music_path"], allowed_roots, "music_path")
            if payload.get("music_path")
            else None
        )
        tts_provider = str(payload.get("tts_provider", "tts_router"))
        image_provider = str(payload.get("image_provider", "auto"))
        music_provider = str(payload.get("music_provider", "poyo_music"))
        estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
        if not estimate_path.exists():
            # The QA gate compares the build's real price with the approved
            # estimate. A worker that never priced this build would spend
            # first and fail the gate afterwards, so refuse up front unless
            # the payload explicitly approved the spend.
            if not bool(payload.get("auto_approve")):
                raise ValueError(
                    "explainer_stills needs an approved cost estimate: run "
                    "`ve explain` first, or enqueue with auto_approve=true"
                )
            estimate_explainer(
                ctx,
                script,
                plan,
                config_path=cp,
                tts_provider=tts_provider,
                image_provider=image_provider,
                music_provider=music_provider,
                music_path=music_path,
                auto_approve=True,
                final=bool(payload.get("final", False)),
            )
        # The stored approval may have been priced for different flags (or by
        # ``ve estimate``, which prices a different request list). Re-price
        # what this payload will actually buy and stop before spending if the
        # approval does not cover it.
        check_approved_spend(
            estimate_path,
            explainer_job_requests(
                plan,
                script,
                ctx.job_id,
                tts_provider=tts_provider,
                image_provider=image_provider,
                music_provider=music_provider,
                music_path=music_path,
            ),
            image_chain=Settings.load(cp).config.assets.image_chain_for(
                bool(payload.get("final", False))
            ),
        )
        # The dispatcher awaits this handler inside its own event loop, and
        # ``run_explainer_job`` is blocking (ffmpeg/subprocess work) and
        # bridges to the manager with ``asyncio.run``. Calling it here would
        # raise "asyncio.run() cannot be called from a running event loop"
        # before any paid request, so run it off the loop in a worker thread.
        result = await asyncio.to_thread(
            run_explainer_job,
            ctx,
            script,
            plan,
            config_path=cp,
            tts_provider=tts_provider,
            image_provider=image_provider,
            music_provider=music_provider,
            music_path=music_path,
            caption_preset=str(payload.get("caption_preset", "")),
            final=bool(payload.get("final", False)),
        )
        logger.info(
            "explainer_job_complete",
            job_id=ctx.job_id,
            final=str(result.final_path),
            passed=result.passed,
        )
        if not result.passed:
            raise RuntimeError(f"explainer QA failed: {result.qa_path}")
        return

    if selected.pipeline_kind == "music_video":
        raw_output_name = payload.get("output_name")
        output_name = _safe_filename(raw_output_name, "output_name") if raw_output_name else None
        language = MusicVideoPipeline._safe_language(str(payload.get("language", "en")))
        media_dir_str = payload.get("media_dir")
        if not media_dir_str:
            raise ValueError("'run' payload for musical_video requires 'media_dir'")
        media_dir = _resolve_payload_path(media_dir_str, allowed_roots, "media_dir")
        if not media_dir.is_dir():
            raise ValueError(f"media_dir is not a directory: {media_dir}")
        if not source.is_file():
            raise ValueError(
                f"'source' for musical_video must be a music/audio file, not a directory: {source}"
            )
        lyrics = (
            _resolve_payload_path(payload["lyrics"], allowed_roots, "lyrics")
            if payload.get("lyrics")
            else None
        )
        presets_dir = (
            _resolve_payload_path(payload["presets_dir"], allowed_roots, "presets_dir")
            if payload.get("presets_dir")
            else None
        )
        from video_editor_asset_ingestion.budget import BudgetGovernor

        budget_governor = (
            BudgetGovernor(limit=payload.get("budget"))
            if payload.get("budget") is not None
            else None
        )
        pipeline = MusicVideoPipeline(
            ctx,
            music_path=source,
            media_dir=media_dir,
            segment_seconds=float(payload.get("segment_seconds", 5.0)),
            lyrics_path=lyrics,
            language=language,
            preset=payload.get("preset", "long_form"),
            presets_dir=presets_dir,
            config_path=cp,
            budget=budget_governor,
            render_engine=str(payload.get("render_engine", "auto")),
        )
        await asyncio.to_thread(
            pipeline.run,
            output_name=output_name,
            expected_aspect_ratio=payload.get("expected_aspect_ratio", "16:9"),
            run_post_render_review=bool(payload.get("review", True)),
        )
        return

    from video_editor_asset_ingestion.budget import BudgetGovernor

    budget_governor = (
        BudgetGovernor(limit=payload.get("budget")) if payload.get("budget") is not None else None
    )
    # raw_to_video + screen_to_video templates carry a default preset
    # (e.g. vlogger_youtube / tech_tutorial) that should win unless the
    # payload explicitly overrode --preset. The CLI default is
    # "short_explainer"; treat that sentinel as "not set".
    skills_preset = payload.get("preset", "short_explainer")
    if (
        selected.pipeline_kind in ("raw_to_video", "screen_to_video")
        and skills_preset == "short_explainer"
    ):
        skills_preset = selected.preset
    skills = SkillsPipeline(
        ctx,
        preset=skills_preset,
        presets_dir=(
            _resolve_payload_path(payload["presets_dir"], allowed_roots, "presets_dir")
            if payload.get("presets_dir")
            else None
        ),
        music_path=_job_path(ctx, payload, "music_path"),
        music_prompt=payload.get("music_prompt", ""),
        music_provider=payload.get("music_provider", "poyo_music"),
        music_duration_seconds=int(payload.get("music_duration_seconds", 30)),
        sfx_provider=payload.get("sfx_provider", ""),
        sfx_duration_seconds=float(payload.get("sfx_duration_seconds", 5.0)),
        sfx_gain_db=float(payload.get("sfx_gain_db", -6.0)),
        config_path=cp,
        skip_hyperframes_lint=bool(payload.get("skip_hyperframes_lint", False)),
        transition=str(payload.get("transition", "none")),
        transition_duration=float(payload.get("transition_duration", 0.3)),
        budget=budget_governor,
        final=bool(payload.get("final", False)),
        run_audio_cleanup=bool(payload.get("audio_cleanup", False)),
        run_transitions=bool(payload.get("transitions", False)),
        run_reframe=bool(payload.get("reframe", False)),
        run_broll=bool(payload.get("broll", False)),
        run_sfx_placement=bool(payload.get("sfx", False)),
        reframe_aspect=str(payload.get("reframe_aspect", "9:16")),
    )
    await asyncio.to_thread(
        skills.run,
        source,
        transcript_path=tp,
        output_name=payload.get("output_name"),
        run_qa=bool(payload.get("qa", False)),
        loudness_target_lufs=(
            float(payload["loudness_target_lufs"])
            if payload.get("loudness_target_lufs") is not None
            else None
        ),
        expected_aspect_ratio=payload.get("expected_aspect_ratio", "16:9"),
        run_verify=bool(payload.get("verify", False)),
        run_post_render_review=bool(payload.get("review", True)),
    )


def _handle_rough_cut(ctx: JobContext, payload: dict[str, Any]) -> None:
    from rough_cut.rough_cut import RoughCutSkill

    source_str = payload.get("source")
    if not source_str:
        raise ValueError("'rough_cut' payload requires 'source' (path to a video file)")
    allowed_roots = [ctx.work_dir, ctx.output_dir]
    source = _resolve_payload_path(source_str, allowed_roots, "source")
    transcript_path = (
        _resolve_payload_path(payload["transcript_path"], allowed_roots, "transcript_path")
        if payload.get("transcript_path")
        else None
    )
    skill = RoughCutSkill(ctx)
    skill.run(source, transcript_path=transcript_path)


def _handle_graphics(ctx: JobContext, payload: dict[str, Any]) -> None:
    from graphics.graphics import GraphicsSkill

    presets_dir = _job_path(ctx, payload, "presets_dir")
    config_path = _resolve_config_path(payload, ctx)
    render_options = RenderOptions()
    render_options.skip_hyperframes_lint = bool(payload.get("skip_hyperframes_lint", False))
    skill = GraphicsSkill(
        ctx,
        presets_dir=_resolve_presets_dir(presets_dir, config_path),
        run_qa_per_segment=bool(payload.get("qa_per_segment", False)),
        render_options=render_options,
    )
    skill.run(preset_name=payload.get("preset", "short_explainer"))


def _handle_second_pass(ctx: JobContext, payload: dict[str, Any]) -> None:
    from graphics.graphics import GraphicsSkill
    from second_pass.models import SegmentEdit
    from second_pass.second_pass import SecondPassSkill

    edit = payload.get("edit")
    if not isinstance(edit, dict):
        raise ValueError("second_pass payload requires an 'edit' object with segment/keeps fields")
    seg_edit = SegmentEdit.model_validate(edit)
    presets_dir = _job_path(ctx, payload, "presets_dir")
    config_path = _resolve_config_path(payload, ctx)
    graphics_skill = GraphicsSkill(ctx, presets_dir=_resolve_presets_dir(presets_dir, config_path))
    skill = SecondPassSkill(ctx, graphics_skill=graphics_skill)
    skill.run(seg_edit)


def _handle_captions_v2(ctx: JobContext, payload: dict[str, Any]) -> None:
    from captions.captions import CaptionsSkill
    from graphics.graphics import GraphicsSkill

    presets_dir = _job_path(ctx, payload, "presets_dir")
    config_path = _resolve_config_path(payload, ctx)
    graphics = GraphicsSkill(ctx, presets_dir=_resolve_presets_dir(presets_dir, config_path))
    skill = CaptionsSkill(ctx, graphics_skill=graphics)
    output_name = _safe_filename(payload.get("output_name", "with_captions.mp4"), "output_name")
    skill.run(output_name=output_name)


def _handle_music(ctx: JobContext, payload: dict[str, Any]) -> None:
    from music.music import MusicSkill

    skill = MusicSkill(ctx, music_path=_job_path(ctx, payload, "music_path"))
    output_name = _safe_filename(payload.get("output_name", "with_music.mp4"), "output_name")
    skill.run(output_name=output_name)


async def _handle_music_generate(ctx: JobContext, payload: dict[str, Any]) -> None:
    prompt = payload.get("prompt")
    if not prompt:
        raise ValueError("music_generate payload requires 'prompt'")
    config = Settings.load(
        _resolve_config_path(payload, ctx) if payload.get("config_path") else None
    ).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    request = music_request(
        job_id=ctx.job_id,
        prompt=prompt,
        duration_seconds=int(payload.get("duration_seconds", 30)),
        provider=payload.get("provider", "poyo_music"),
    )
    results = await manager.resolve([request])
    music_dir = ctx.work_dir / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    final_path = music_dir / "generated_music.mp3"
    shutil.copyfile(Path(results[0].local_path), final_path)


async def _handle_sfx_generate(ctx: JobContext, payload: dict[str, Any]) -> None:
    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Scene plan not found: {plan_path}")
    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    config = Settings.load(
        _resolve_config_path(payload, ctx) if payload.get("config_path") else None
    ).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    requests = [
        req
        for scene in plan.scenes
        for req in sfx_requests_for_scene(
            scene,
            ctx.job_id,
            sfx_provider=payload.get("provider", ""),
            duration_seconds=float(payload.get("duration_seconds", 5.0)),
        )
    ]
    if not requests:
        return
    results = await manager.resolve(requests)
    sfx_dir = ctx.work_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    sfx_map: dict[str, str] = {}
    for result in results:
        out_path = sfx_dir / f"{result.scene_id}.mp3"
        shutil.copyfile(Path(result.local_path), out_path)
        sfx_map[result.scene_id] = str(out_path)
    sfx_map_path = sfx_dir / "sfx_map.json"
    sfx_map_path.write_text(json.dumps(sfx_map, indent=2), encoding="utf-8")


def _handle_sfx_mix(ctx: JobContext, payload: dict[str, Any]) -> None:
    plan_name = _safe_filename(payload.get("plan_name", "scene_plan.json"), "plan_name")
    base_video = _safe_filename(payload.get("base", "with_broll.mp4"), "base_video")
    gain_db = float(payload.get("sfx_gain_db", -6.0))

    plan_path = ctx.work_dir / "plan" / plan_name
    sfx_map_path = ctx.work_dir / "sfx" / "sfx_map.json"
    base_video_path = ctx.work_dir / "broll" / base_video
    output_path = ctx.work_dir / "broll" / "with_broll_sfx.mp4"

    if not base_video_path.exists():
        raise FileNotFoundError(f"base video not found: {base_video_path}")
    if not sfx_map_path.exists():
        shutil.copyfile(base_video_path, output_path)
        return

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    sfx_map: dict[str, str] = json.loads(sfx_map_path.read_text(encoding="utf-8"))
    timeline: list[tuple[Path, float]] = []
    cursor = 0.0
    for scene in plan.scenes:
        sfx_path_str = sfx_map.get(scene.id)
        if not sfx_path_str or not Path(sfx_path_str).exists():
            cursor += scene.duration_seconds
            continue
        timeline.append((Path(sfx_path_str), cursor))
        cursor += scene.duration_seconds
    mix_sfx_into_video(
        base_video=base_video_path,
        sfx_timeline=timeline,
        output_path=output_path,
        sfx_gain_db=gain_db,
    )


async def _handle_align(ctx: JobContext, payload: dict[str, Any]) -> None:
    audio = payload.get("audio")
    if not audio:
        raise ValueError("align payload requires 'audio' pointing to an audio file")
    text = payload.get("text")
    if not text:
        raise ValueError("align payload requires 'text' for the known transcript")
    output_name = _safe_filename(payload.get("output_name", "alignment.json"), "output_name")
    audio_path = _resolve_payload_path(
        audio,
        [ctx.work_dir, ctx.output_dir],
        "audio",
    )
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")
    if audio_path.is_dir():
        raise ValueError(f"audio must be a file, not a directory: {audio_path}")
    config = Settings.load(
        _resolve_config_path(payload, ctx) if payload.get("config_path") else None
    ).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    request = alignment_request(
        job_id=ctx.job_id,
        audio_path=str(audio_path),
        text=text,
        provider=payload.get("provider", ""),
    )
    results = await manager.resolve([request])
    transcript_dir = ctx.work_dir / "transcript"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    final_path = transcript_dir / output_name
    shutil.copyfile(Path(results[0].local_path), final_path)


async def _handle_audio_isolate(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_asset_ingestion.scene_assets import isolation_request

    audio = payload.get("audio")
    if not audio:
        raise ValueError("audio_isolate payload requires 'audio' pointing to an audio file")
    output_name = _safe_filename(payload.get("output_name", "isolated.mp3"), "output_name")
    audio_path = _resolve_payload_path(
        audio,
        [ctx.work_dir, ctx.output_dir],
        "audio",
    )
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")
    if audio_path.is_dir():
        raise ValueError(f"audio must be a file, not a directory: {audio_path}")

    config_path = _resolve_config_path(payload, ctx)
    config = Settings.load(config_path).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    output_ext = Path(output_name).suffix.lstrip(".") or "mp3"
    request = isolation_request(
        job_id=ctx.job_id,
        audio_path=str(audio_path),
        provider=payload.get("provider", ""),
        file_format=payload.get("file_format", "other"),
        output_ext=output_ext,
    )
    results = await manager.resolve([request])
    output_dir = ctx.work_dir / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / output_name
    shutil.copyfile(Path(results[0].local_path), final_path)


async def _handle_speech_to_speech(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_asset_ingestion.scene_assets import speech_to_speech_request

    audio = payload.get("audio")
    if not audio:
        raise ValueError("speech_to_speech payload requires 'audio' pointing to an audio file")
    voice_id = payload.get("voice_id")
    if not voice_id:
        raise ValueError("speech_to_speech payload requires 'voice_id'")
    output_name = _safe_filename(payload.get("output_name", "converted.mp3"), "output_name")
    audio_path = _resolve_payload_path(
        audio,
        [ctx.work_dir, ctx.output_dir],
        "audio",
    )
    if not audio_path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")
    if audio_path.is_dir():
        raise ValueError(f"audio must be a file, not a directory: {audio_path}")

    config_path = _resolve_config_path(payload, ctx)
    config = Settings.load(config_path).config.assets
    manager = AssetIngestionManager(ctx, config=config)
    effective_output_ext = (
        payload.get("output_ext") or Path(output_name).suffix.lstrip(".") or "mp3"
    )
    if effective_output_ext != Path(output_name).suffix.lstrip("."):
        output_name = f"{Path(output_name).stem}.{effective_output_ext}"
    output_ext = effective_output_ext
    output_format = payload.get("output_format", "")
    seed_raw = payload.get("seed")
    seed = int(seed_raw) if seed_raw is not None else 0
    voice_settings: dict[str, Any] = {}
    stability_raw = payload.get("stability")
    if stability_raw is not None:
        voice_settings["stability"] = float(stability_raw)
    similarity_raw = payload.get("similarity_boost")
    if similarity_raw is not None:
        voice_settings["similarity_boost"] = float(similarity_raw)

    request = speech_to_speech_request(
        job_id=ctx.job_id,
        audio_path=str(audio_path),
        voice_id=voice_id,
        provider=payload.get("provider", ""),
        model_id=payload.get("model_id", "eleven_english_sts_v2"),
        output_format=output_format,
        remove_background_noise=bool(payload.get("remove_background_noise", False)),
        seed=seed,
        voice_settings=voice_settings,
        output_ext=output_ext,
    )
    results = await manager.resolve([request])
    output_dir = ctx.work_dir / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / output_name
    shutil.copyfile(Path(results[0].local_path), final_path)


def _handle_audio(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_ffmpeg.voiceover import mux_voiceover

    source_str = payload.get("source")
    if not source_str:
        raise ValueError("'audio' payload requires 'source' (path to a video file)")
    voiceover_str = payload.get("voiceover")
    if not voiceover_str:
        raise ValueError("'audio' payload requires 'voiceover' (path to an audio file)")
    output_name = _safe_filename(payload.get("output_name", "with_voiceover.mp4"), "output_name")
    allowed_roots = [ctx.work_dir, ctx.output_dir]
    source = _resolve_payload_path(source_str, allowed_roots, "source")
    voiceover = _resolve_payload_path(voiceover_str, allowed_roots, "voiceover")
    if not source.exists():
        raise ValueError(f"Video file not found: {source}")
    if not voiceover.exists():
        raise ValueError(f"Audio file not found: {voiceover}")
    output_dir = ctx.work_dir / "intake"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name
    mux_voiceover(source, voiceover, output_path)


# ---------------------------------------------------------------------------
# Bridge handlers (ComfyStudio / FableCut)
# ---------------------------------------------------------------------------


def _handle_export_comfystudio(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_comfystudio_bridge.exporter import export_to_comfystudio

    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Scene plan not found: {plan_path}")
    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    project_dir = _require_job_path(ctx, payload, "project_dir")
    export_to_comfystudio(
        scene_plan=plan,
        project_root=project_dir,
        fps=int(payload.get("fps", 30)),
        width=int(payload.get("width", 1920)),
        height=int(payload.get("height", 1080)),
        music_path=_job_path(ctx, payload, "music_path"),
    )


def _handle_import_comfystudio(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_comfystudio_bridge.importer import import_from_comfystudio

    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Original scene plan not found: {plan_path}")
    original_plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    project_file = _require_job_path(ctx, payload, "project_dir") / "project.comfystudio"
    updated_plan = import_from_comfystudio(project_file, original_plan)
    plan_out_path = (
        ctx.work_dir / "plan" / payload.get("plan_out_name", "scene_plan_with_assets.json")
    )
    plan_out_path.parent.mkdir(parents=True, exist_ok=True)
    plan_out_path.write_text(updated_plan.model_dump_json(indent=2), encoding="utf-8")


def _handle_export_fablecut(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_fablecut_bridge.exporter import export_to_fablecut

    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Scene plan not found: {plan_path}")
    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    project_dir = _require_job_path(ctx, payload, "project_dir")
    export_to_fablecut(
        scene_plan=plan,
        project_root=project_dir,
        fps=int(payload.get("fps", 30)),
        width=int(payload.get("width", 1920)),
        height=int(payload.get("height", 1080)),
        music_path=_job_path(ctx, payload, "music_path"),
        lyrics_path=_job_path(ctx, payload, "lyrics_path"),
    )


async def _handle_storyteller_export(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut

    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Scene plan not found: {plan_path}")
    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    project_dir = _require_job_path(ctx, payload, "project_dir")
    await storyteller_to_fablecut(
        scene_plan=plan,
        project_dir=project_dir,
        aspect_ratio=payload.get("aspect_ratio", "16:9"),
    )


def _handle_import_fablecut(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_fablecut_bridge.importer import import_from_fablecut

    plan_name = payload.get("plan_name", "scene_plan.json")
    plan_path = ctx.work_dir / "plan" / plan_name
    if not plan_path.exists():
        raise ValueError(f"Original scene plan not found: {plan_path}")
    original_plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    project_file = _require_job_path(ctx, payload, "project_dir") / "project.json"
    updated_plan = import_from_fablecut(project_file, original_plan)
    plan_out_path = (
        ctx.work_dir / "plan" / payload.get("plan_out_name", "scene_plan_with_assets.json")
    )
    plan_out_path.parent.mkdir(parents=True, exist_ok=True)
    plan_out_path.write_text(updated_plan.model_dump_json(indent=2), encoding="utf-8")


async def _handle_render_fablecut(ctx: JobContext, payload: dict[str, Any]) -> None:
    from video_editor_config import ProjectConfig
    from video_editor_fablecut_bridge.renderer import render_fablecut_project
    from video_editor_ffmpeg.render_options import RenderOptions

    allowed_roots = [ctx.work_dir, ctx.output_dir]
    raw_project_dir = payload["project_dir"]
    project_dir_path = Path(raw_project_dir)
    if not project_dir_path.is_absolute():
        project_dir_path = (ctx.work_dir / raw_project_dir).resolve()
    project_dir = _resolve_payload_path(str(project_dir_path), allowed_roots, "project_dir")
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        raise FileNotFoundError(f"FableCut project not found: {project_file}")

    config_path = _resolve_config_path(payload, ctx)
    project_config = ProjectConfig.load(config_path)
    render_options = RenderOptions.from_mapping(
        project_config.render.model_dump(
            include={
                "video_codec",
                "audio_codec",
                "video_bitrate",
                "audio_bitrate",
                "fps",
                "crf",
                "width",
                "height",
            }
        )
    )
    if payload.get("width") is not None:
        render_options.width = int(payload["width"])
    if payload.get("height") is not None:
        render_options.height = int(payload["height"])
    if payload.get("fps") is not None:
        render_options.fps = int(payload["fps"])
    output_name = _safe_filename(payload.get("output_name", "final.mp4"), "output_name")
    output_path = ctx.output_dir / output_name
    await asyncio.to_thread(
        render_fablecut_project,
        project_path=project_file,
        output_path=output_path,
        media_dir=project_dir / "media",
        render_options=render_options,
        font_path=payload.get("font"),
        allowed_roots=allowed_roots,
    )
    ctx.manifest().stage_done("export", {"final": str(output_path)})
    ctx.store.save()


def _handle_brief(ctx: JobContext, payload: dict[str, Any]) -> None:
    import shutil
    import tempfile

    from video_editor_state.project_brief import load_project_brief, write_project_brief

    from_md_str = payload.get("from_md")
    if not from_md_str:
        raise ValueError("brief payload requires 'from_md' pointing to a project.md")
    from_md = _resolve_payload_path(
        from_md_str,
        [ctx.work_dir, ctx.output_dir],
        "from_md",
    )
    if not from_md.exists():
        raise FileNotFoundError(f"project.md not found: {from_md}")
    if from_md.is_dir():
        raise ValueError(f"'from_md' must be a file path, got directory: {from_md}")
    with tempfile.TemporaryDirectory() as parse_dir:
        parse_root = Path(parse_dir)
        canonical = parse_root / "project.md"
        shutil.copy2(from_md, canonical)
        parsed = load_project_brief(parse_root)
    if parsed is None:
        raise ValueError(f"Could not parse project.md at {from_md}")
    parsed.job_id = ctx.job_id
    write_project_brief(ctx.work_dir, parsed)


def _handle_short(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Run a content-pack short from the queue (#303).

    The job directory must already hold an approved ``cost_estimate.json``;
    ``run_short_job`` re-checks it before any paid stage, so the queue never
    approves spend by itself.
    """
    from video_editor_core.content_pack import ContentPackError, load_content_pack

    from video_editor_pipelines.short_pipeline import (
        ShortPipelineError,
        ShortPreflightError,
        preflight_short,
        run_short_job,
    )

    pack_str = payload.get("pack")
    if not pack_str:
        raise ValueError("short payload requires 'pack' (path to a short.yaml)")
    pack_path = _resolve_payload_path(pack_str, [ctx.work_dir, ctx.output_dir], "pack")
    if not pack_path.is_file():
        raise FileNotFoundError(f"content pack not found: {pack_path}")
    try:
        pack = load_content_pack(pack_path)
        tokens = preflight_short(pack)
    except (ContentPackError, ShortPreflightError) as exc:
        raise ShortPipelineError(str(exc)) from exc

    result = run_short_job(
        ctx,
        pack,
        tokens,
        config_path=_resolve_config_path(payload, ctx),
        final=bool(payload.get("final", False)),
    )
    if not result.passed:
        raise RuntimeError(f"short QA failed: {result.qa_path}")


def _handle_musical(ctx: JobContext, payload: dict[str, Any]) -> None:
    """Compose a musical video from local files via the queue (#303)."""
    from video_editor_core.musical import MusicalPlanError

    from video_editor_pipelines.musical_pipeline import MusicalPipelineError, run_musical_job

    allowed = [ctx.work_dir, ctx.output_dir]
    missing = [key for key in ("music", "media_dir", "lyrics") if not payload.get(key)]
    if missing:
        raise ValueError(f"musical payload requires {', '.join(missing)}")
    try:
        result = run_musical_job(
            ctx,
            music_path=_resolve_payload_path(payload["music"], allowed, "music"),
            media_dir=_resolve_payload_path(payload["media_dir"], allowed, "media_dir"),
            lyrics_path=_resolve_payload_path(payload["lyrics"], allowed, "lyrics"),
            output_path=ctx.output_dir
            / _safe_filename(payload.get("output_name", "final.mp4"), "output_name"),
            width=int(payload.get("width", 1920)),
            height=int(payload.get("height", 1080)),
            fps=int(payload.get("fps", 30)),
            segment_seconds=float(payload.get("segment_seconds", 8.0)),
            transition_seconds=float(payload.get("transition_seconds", 1.5)),
            run_qa=bool(payload.get("qa", True)),
            quantize=str(payload.get("quantize", "off")),
            beat_tolerance=float(payload.get("beat_tolerance", 0.12)),
            swells=tuple(
                (float(at), float(db), float(duration))
                for at, db, duration in payload.get("swells", [])
            ),
        )
    except (MusicalPipelineError, MusicalPlanError) as exc:
        raise RuntimeError(str(exc)) from exc
    if result.qa_path is not None and not result.passed:
        raise RuntimeError(f"musical QA failed: {result.qa_path}")


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------


HANDLERS: dict[str, Any] = {
    # Full pipeline run + verification gate
    "run": _handle_run,
    "short": _handle_short,
    "musical": _handle_musical,
    "verify": _handle_verify,
    # v2 skill shortcuts
    "rough_cut": _handle_rough_cut,
    "graphics": _handle_graphics,
    "second_pass": _handle_second_pass,
    "captions_v2": _handle_captions_v2,
    "music": _handle_music,
    "brief": _handle_brief,
    # AI music + SFX
    "music_generate": _handle_music_generate,
    "sfx_generate": _handle_sfx_generate,
    "sfx_mix": _handle_sfx_mix,
    # Forced alignment
    "align": _handle_align,
    # Audio isolation / voice extraction
    "audio_isolate": _handle_audio_isolate,
    # Speech-to-speech / voice conversion
    "speech_to_speech": _handle_speech_to_speech,
    # External voiceover muxing
    "audio": _handle_audio,
    # ComfyStudio timeline bridge
    "export_comfystudio": _handle_export_comfystudio,
    "import_comfystudio": _handle_import_comfystudio,
    # FableCut timeline bridge
    "export_fablecut": _handle_export_fablecut,
    "storyteller_export": _handle_storyteller_export,
    "import_fablecut": _handle_import_fablecut,
    "render_fablecut": _handle_render_fablecut,
}
