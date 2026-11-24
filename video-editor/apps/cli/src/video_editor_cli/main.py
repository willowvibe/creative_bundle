"""Typer CLI entrypoint for the video editor engine."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import structlog
import typer
from export.export import ExportSkill  # noqa: F401
from review.review import PostRenderReviewSkill  # noqa: F401
from video_editor_asset_ingestion.scene_assets import (  # noqa: F401
    isolation_request,
    speech_to_speech_request,
)
from video_editor_core.constants import REPO_ROOT  # noqa: F401
from video_editor_core.templates import TemplateRouter  # noqa: F401
from video_editor_engine.planner.storyteller_planner import (  # noqa: F401
    build_storyteller_scene_plan,
)

# Handler functions and registry are now in video_editor_pipelines.handlers
from video_editor_pipelines.handlers import (  # noqa: F401
    HANDLERS,
    _handle_align,
    _handle_audio,
    _handle_audio_isolate,
    _handle_brief,
    _handle_captions_v2,
    _handle_export_comfystudio,
    _handle_export_fablecut,
    _handle_graphics,
    _handle_import_comfystudio,
    _handle_import_fablecut,
    _handle_music,
    _handle_music_generate,
    _handle_render_fablecut,
    _handle_rough_cut,
    _handle_run,
    _handle_second_pass,
    _handle_sfx_generate,
    _handle_sfx_mix,
    _handle_speech_to_speech,
    _handle_storyteller_export,
    _handle_verify,
    _resolve_config_path,
    _resolve_payload_path,
    _safe_filename,
)
from video_editor_pipelines.pipeline import MusicVideoPipeline, SkillsPipeline  # noqa: F401
from video_editor_pipelines.scene_planner import write_scene_plan  # noqa: F401
from video_editor_state.errors import LeaseLostError
from video_editor_state.job import JobContext
from video_editor_state.ledger_hooks import record_pipeline_failed
from video_editor_state.paths import default_queue_db
from video_editor_state.queue import InMemoryJobQueue, JobMessage, JobQueue, PersistentJobQueue

from video_editor_cli.bridge_loader import FABLECUT, bridge
from video_editor_cli.cli_support import (  # noqa: F401
    _ALIGN_AUDIO_OPT,
    _ALIGN_OUTPUT_OPT,
    _ALIGN_PROVIDER_OPT,
    _ALIGN_TEXT_OPT,
    _ASPECT_RATIO_OPT,
    _ASSETS_DATA_VIZ_PROVIDER_OPT,
    _ASSETS_IMAGE_PROVIDER_OPT,
    _ASSETS_PLAN_NAME_OPT,
    _ASSETS_PLAN_OUT_NAME_OPT,
    _ASSETS_TTS_PROVIDER_OPT,
    _ASSETS_VIDEO_PROVIDER_OPT,
    _AUDIO_CLEANUP_OPT,
    _AUDIO_OUTPUT_NAME_OPT,
    _AUTO_APPROVE_OPT,
    _BRAND_JOB_ID_OPT,
    _BRAND_PRESETS_DIR_OPT,
    _BRAND_TENANT_ID_OPT,
    _BROLL_OPT,
    _BROLL_PROVIDER_OPT,
    _BUDGET_OPT,
    _COMFYSTUDIO_FPS_OPT,
    _COMFYSTUDIO_HEIGHT_OPT,
    _COMFYSTUDIO_PROJECT_DIR_OPT,
    _COMFYSTUDIO_WIDTH_OPT,
    _COMMANDS_JSON_OPT,
    _CONFIG_OPT,
    _CONFIG_RUN_OPT,
    _CONTROL_HOST_OPT,
    _CONTROL_PORT_OPT,
    _DEFAULT_DATA_VIZ_PROVIDER,
    _DEFAULT_IMAGE_PROVIDER,
    _DEFAULT_OUTPUT_ROOT,
    _DEFAULT_PLAN_NAME,
    _DEFAULT_PLAN_OUT_NAME,
    _DEFAULT_TTS_PROVIDER,
    _DEFAULT_VIDEO_PROVIDER,
    _DEFAULT_WORK_ROOT,
    _END_OPT,
    _EXPECTED_ASPECT_OPT,
    _EXPLAIN_CAPTION_PRESET_OPT,
    _EXPLAIN_FROM_TOPIC_OPT,
    _EXPLAIN_IMAGE_PROVIDER_OPT,
    _EXPLAIN_NO_REUSE_OPT,
    _EXPLAIN_TTS_PROVIDER_OPT,
    _FABLECUT_FPS_OPT,
    _FABLECUT_HEIGHT_OPT,
    _FABLECUT_PROJECT_DIR_OPT,
    _FABLECUT_WIDTH_OPT,
    _FORBID_ELEMENT_OPT,
    _FORCE_OPT,
    _FORCE_RUN_OPT,
    _FROM_MD_OPT,
    _IMAGE_TIER_OPT,
    _ISOLATE_AUDIO_OPT,
    _ISOLATE_FILE_FORMAT_OPT,
    _ISOLATE_OUTPUT_OPT,
    _ISOLATE_PROVIDER_OPT,
    _JOB_ID_ARG,
    _JOB_ID_OPT,
    _JUDGE_CHECK_OPT,
    _LABEL_OPT,
    _LANGUAGE_OPT,
    _LOUDNESS_TARGET_OPT,
    _LYRICS_OPT,
    _MEDIA_DIR_OPT,
    _MUSIC_DURATION_OPT,
    _MUSIC_MOOD_OPT,
    _MUSIC_PATH_OPT,
    _MUSIC_PROMPT_OPT,
    _MUSIC_PROVIDER_OPT,
    _MUST_MENTION_OPT,
    _OTIO_PATH_OPT,
    _OUTPUT_NAME_CAPTIONS_OPT,
    _OUTPUT_NAME_MUSIC_OPT,
    _OUTPUT_NAME_OPT,
    _OUTPUT_ROOT_OPT,
    _PATH_ARG,
    _PAYLOAD_SCHEMAS,
    _PLAN_OUT_OPT,
    _PRESET_OPT,
    _PRESETS_DIR_OPT,
    _QA_OPT,
    _QA_PER_SEGMENT_OPT,
    _REFRAME_ASPECT_OPT,
    _REFRAME_OPT,
    _RENDER_ENGINE_OPT,
    _RENDER_FABLECUT_FONT_OPT,
    _RENDER_FABLECUT_FPS_OPT,
    _RENDER_FABLECUT_HEIGHT_OPT,
    _RENDER_FABLECUT_OUTPUT_NAME_OPT,
    _RENDER_FABLECUT_PROJECT_DIR_OPT,
    _RENDER_FABLECUT_WIDTH_OPT,
    _REQUIRE_ASPECT_OPT,
    _REQUIRE_ELEMENT_OPT,
    _REQUIRE_MAX_DURATION_OPT,
    _REQUIRE_MIN_DURATION_OPT,
    _REVIEW_OPT,
    _RUN_EXPECTED_ASPECT_OPT,
    _RUN_IMAGE_PROVIDER_OPT,
    _RUN_SOURCE_ARG,
    _SCRIPT_PATH_ARG,
    _SEGMENT_OPT,
    _SEGMENT_SECONDS_OPT,
    _SFX_DURATION_OPT,
    _SFX_GAIN_DB_OPT,
    _SFX_PLACEMENT_OPT,
    _SFX_PROVIDER_OPT,
    _SKIP_HYPERFRAMES_LINT_OPT,
    _SKIP_MUSIC_OPT,
    _SKIP_PREVIEW_OPT,
    _SOURCE_PATH_ARG,
    _START_OPT,
    _STORYTELLER_PROJECT_DIR_OPT,
    _STRUCTLOG_CONSOLE,
    _STRUCTLOG_JSON,
    _STS_AUDIO_OPT,
    _STS_MODEL_ID_OPT,
    _STS_OUTPUT_EXT_OPT,
    _STS_OUTPUT_FORMAT_OPT,
    _STS_OUTPUT_OPT,
    _STS_PROVIDER_OPT,
    _STS_REMOVE_BG_OPT,
    _STS_SEED_OPT,
    _STS_SIMILARITY_OPT,
    _STS_STABILITY_OPT,
    _STS_VOICE_ID_OPT,
    _TEMPLATE_OPT,
    _TENANT_ID_OPT,
    _TRANSCRIPT_GRAPHICS_OPT,
    _TRANSCRIPT_ROUGH_CUT_OPT,
    _TRANSCRIPT_RUN_OPT,
    _TRANSITION_DURATION_OPT,
    _TRANSITION_OPT,
    _TRANSITIONS_OPT,
    _TTS_TEST_JOB_ID_OPT,
    _TTS_TEST_LANGUAGE_OPT,
    _TTS_TEST_TEXT_OPT,
    _TTS_TEST_VOICE_OPT,
    _VERIFY_OPT,
    _VIDEO_TIER_OPT,
    _VOICEOVER_GAIN_OPT,
    _VOICEOVER_OPT,
    _WORK_ROOT_OPT,
    DEFAULT_STALE_SECONDS,
    JobNotFoundError,
    _configure_structlog,
    _context,
    _discover_config_path,
    _Handler,
    _heartbeat,
    _llm_router,
    _load_dotenv,
    _preset_aspect_ratio,
    _RenderFableCutPayload,
    _resolve_ledger_cli,
    _resolve_presets_dir,
    _RunPayload,
    _SpeechToSpeechPayload,
    _StorytellerTier,
    _tts_test_synthesize,
    _validate_payload_root,
)
from video_editor_cli.commands.agy_setup import register_agy_setup
from video_editor_cli.commands.cache_prune import register_cache_prune
from video_editor_cli.commands.check_config import register_check_config
from video_editor_cli.commands.doctor import register_doctor
from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.commands.finalize import register_finalize
from video_editor_cli.commands.gen_models import register_gen_models
from video_editor_cli.commands.poyo_catalog import register_poyo_catalog
from video_editor_cli.commands.preview import register_preview
from video_editor_cli.commands.update_pricing import register_update_pricing
from video_editor_cli.paths import default_output_root, default_work_root
from video_editor_cli.payloads import (  # noqa: F401
    PAYLOAD_SCHEMAS,
    RenderFableCutPayload,
    RunPayload,
    SpeechToSpeechPayload,
)

_HANDLERS = HANDLERS  # Backward-compat alias; monkeypatched by tests


_queue_instance: JobQueue | None = None
_in_memory_warned = False


def _get_queue() -> JobQueue:
    """Return the shared queue, backed by SQLite unless a test injects one."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = PersistentJobQueue(default_queue_db())
    return _queue_instance


def _warn_in_memory_queue_once(queue: JobQueue | None = None) -> None:
    """Warn once per process that the in-memory queue does not persist
    across processes (``ve enqueue`` and ``ve worker`` in different terminals
    will silently lose jobs).

    The warning is emitted to stderr so scripts can still parse the stdout
    output (e.g. ``Enqueued ...``) without the warning polluting the result.
    """
    global _in_memory_warned
    if queue is not None and not isinstance(queue, InMemoryJobQueue):
        return
    if _in_memory_warned:
        return
    _in_memory_warned = True
    typer.echo(
        "warning: in-memory job queue (test/override), jobs are not shared "
        "across processes. The default is PersistentJobQueue (VE_QUEUE_DB); "
        "inject one queue instance for in-process workflows.",
        err=True,
    )


async def storyteller_to_fablecut(*args: Any, **kwargs: Any) -> Any:
    """Export a storyteller plan to FableCut; the bridge is loaded only when this runs."""
    export = bridge(f"{FABLECUT}.storyteller_export", "storyteller_to_fablecut")
    return await export(*args, **kwargs)


__all__ = [
    "REPO_ROOT",
    "speech_to_speech_request",
    "isolation_request",
    "write_scene_plan",
    "SkillsPipeline",
    "MusicVideoPipeline",
    "storyteller_to_fablecut",
    "build_storyteller_scene_plan",
    "TemplateRouter",
    "PostRenderReviewSkill",
    "ExportSkill",
    "_resolve_presets_dir",
    "_ALIGN_AUDIO_OPT",
    "_ALIGN_OUTPUT_OPT",
    "_ALIGN_PROVIDER_OPT",
    "_ALIGN_TEXT_OPT",
    "_ASPECT_RATIO_OPT",
    "_ASSETS_DATA_VIZ_PROVIDER_OPT",
    "_ASSETS_IMAGE_PROVIDER_OPT",
    "_ASSETS_PLAN_NAME_OPT",
    "_ASSETS_PLAN_OUT_NAME_OPT",
    "_ASSETS_TTS_PROVIDER_OPT",
    "_ASSETS_VIDEO_PROVIDER_OPT",
    "_AUDIO_CLEANUP_OPT",
    "_AUDIO_OUTPUT_NAME_OPT",
    "_AUTO_APPROVE_OPT",
    "_BRAND_JOB_ID_OPT",
    "_BRAND_PRESETS_DIR_OPT",
    "_BRAND_TENANT_ID_OPT",
    "_BROLL_OPT",
    "_BROLL_PROVIDER_OPT",
    "_BUDGET_OPT",
    "_COMFYSTUDIO_FPS_OPT",
    "_COMFYSTUDIO_HEIGHT_OPT",
    "_COMFYSTUDIO_PROJECT_DIR_OPT",
    "_COMFYSTUDIO_WIDTH_OPT",
    "_COMMANDS_JSON_OPT",
    "_CONFIG_OPT",
    "_CONFIG_RUN_OPT",
    "_CONTROL_HOST_OPT",
    "_CONTROL_PORT_OPT",
    "_DEFAULT_DATA_VIZ_PROVIDER",
    "_DEFAULT_IMAGE_PROVIDER",
    "_DEFAULT_OUTPUT_ROOT",
    "_DEFAULT_PLAN_NAME",
    "_DEFAULT_PLAN_OUT_NAME",
    "_DEFAULT_TTS_PROVIDER",
    "_DEFAULT_VIDEO_PROVIDER",
    "_DEFAULT_WORK_ROOT",
    "_END_OPT",
    "_EXPECTED_ASPECT_OPT",
    "_EXPLAIN_CAPTION_PRESET_OPT",
    "_EXPLAIN_FROM_TOPIC_OPT",
    "_EXPLAIN_IMAGE_PROVIDER_OPT",
    "_EXPLAIN_NO_REUSE_OPT",
    "_EXPLAIN_TTS_PROVIDER_OPT",
    "_FABLECUT_FPS_OPT",
    "_FABLECUT_HEIGHT_OPT",
    "_FABLECUT_PROJECT_DIR_OPT",
    "_FABLECUT_WIDTH_OPT",
    "_FORBID_ELEMENT_OPT",
    "_FORCE_OPT",
    "_FORCE_RUN_OPT",
    "_FROM_MD_OPT",
    "_IMAGE_TIER_OPT",
    "_ISOLATE_AUDIO_OPT",
    "_ISOLATE_FILE_FORMAT_OPT",
    "_ISOLATE_OUTPUT_OPT",
    "_ISOLATE_PROVIDER_OPT",
    "_JOB_ID_ARG",
    "_JOB_ID_OPT",
    "_JUDGE_CHECK_OPT",
    "_LABEL_OPT",
    "_LANGUAGE_OPT",
    "_LOUDNESS_TARGET_OPT",
    "_LYRICS_OPT",
    "_MEDIA_DIR_OPT",
    "_MUSIC_DURATION_OPT",
    "_MUSIC_MOOD_OPT",
    "_MUSIC_PATH_OPT",
    "_MUSIC_PROMPT_OPT",
    "_MUSIC_PROVIDER_OPT",
    "_MUST_MENTION_OPT",
    "_OTIO_PATH_OPT",
    "_OUTPUT_NAME_CAPTIONS_OPT",
    "_OUTPUT_NAME_MUSIC_OPT",
    "_OUTPUT_NAME_OPT",
    "_OUTPUT_ROOT_OPT",
    "_PATH_ARG",
    "_PAYLOAD_SCHEMAS",
    "_PLAN_OUT_OPT",
    "_PRESET_OPT",
    "_PRESETS_DIR_OPT",
    "_QA_OPT",
    "_QA_PER_SEGMENT_OPT",
    "_REFRAME_ASPECT_OPT",
    "_REFRAME_OPT",
    "_RENDER_ENGINE_OPT",
    "_RENDER_FABLECUT_FONT_OPT",
    "_RENDER_FABLECUT_FPS_OPT",
    "_RENDER_FABLECUT_HEIGHT_OPT",
    "_RENDER_FABLECUT_OUTPUT_NAME_OPT",
    "_RENDER_FABLECUT_PROJECT_DIR_OPT",
    "_RENDER_FABLECUT_WIDTH_OPT",
    "_REQUIRE_ASPECT_OPT",
    "_REQUIRE_ELEMENT_OPT",
    "_REQUIRE_MAX_DURATION_OPT",
    "_REQUIRE_MIN_DURATION_OPT",
    "_REVIEW_OPT",
    "_RUN_EXPECTED_ASPECT_OPT",
    "_RUN_IMAGE_PROVIDER_OPT",
    "_RUN_SOURCE_ARG",
    "_SCRIPT_PATH_ARG",
    "_SEGMENT_OPT",
    "_SEGMENT_SECONDS_OPT",
    "_SFX_DURATION_OPT",
    "_SFX_GAIN_DB_OPT",
    "_SFX_PLACEMENT_OPT",
    "_SFX_PROVIDER_OPT",
    "_SKIP_HYPERFRAMES_LINT_OPT",
    "_SKIP_MUSIC_OPT",
    "_SKIP_PREVIEW_OPT",
    "_SOURCE_PATH_ARG",
    "_START_OPT",
    "_STORYTELLER_PROJECT_DIR_OPT",
    "_STRUCTLOG_CONSOLE",
    "_STRUCTLOG_JSON",
    "_STS_AUDIO_OPT",
    "_STS_MODEL_ID_OPT",
    "_STS_OUTPUT_EXT_OPT",
    "_STS_OUTPUT_FORMAT_OPT",
    "_STS_OUTPUT_OPT",
    "_STS_PROVIDER_OPT",
    "_STS_REMOVE_BG_OPT",
    "_STS_SEED_OPT",
    "_STS_SIMILARITY_OPT",
    "_STS_STABILITY_OPT",
    "_STS_VOICE_ID_OPT",
    "_TEMPLATE_OPT",
    "_TENANT_ID_OPT",
    "_TRANSCRIPT_GRAPHICS_OPT",
    "_TRANSCRIPT_ROUGH_CUT_OPT",
    "_TRANSCRIPT_RUN_OPT",
    "_TRANSITION_DURATION_OPT",
    "_TRANSITION_OPT",
    "_TRANSITIONS_OPT",
    "_TTS_TEST_JOB_ID_OPT",
    "_TTS_TEST_LANGUAGE_OPT",
    "_TTS_TEST_TEXT_OPT",
    "_TTS_TEST_VOICE_OPT",
    "_VERIFY_OPT",
    "_VIDEO_TIER_OPT",
    "_VOICEOVER_GAIN_OPT",
    "_VOICEOVER_OPT",
    "_WORK_ROOT_OPT",
    "DEFAULT_STALE_SECONDS",
    "JobNotFoundError",
    "_configure_structlog",
    "_context",
    "_discover_config_path",
    "_Handler",
    "_heartbeat",
    "_llm_router",
    "_load_dotenv",
    "_preset_aspect_ratio",
    "_RenderFableCutPayload",
    "_resolve_ledger_cli",
    "_RunPayload",
    "_SpeechToSpeechPayload",
    "_StorytellerTier",
    "_tts_test_synthesize",
    "_validate_payload_root",
]


_load_dotenv()
_configure_structlog()


app = typer.Typer(help="Video Editor Engine CLI")
logger = structlog.get_logger(__name__)


# Payload models are now in video_editor_cli.payloads
# Module-level Typer parameter singletons to satisfy B008.


# Type alias for a worker handler: receives the JobContext and the
# message payload, returns either nothing (sync) or a coroutine.


def _build_job_context(message: JobMessage) -> JobContext:
    """Build a ``JobContext`` from a queued ``JobMessage``.

    Pulls ``work_root`` and ``output_root`` out of the message
    payload, resolving them through ``video_editor_state.paths`` when
    the payload omits them. Attaches a ledger when the payload carries
    a ``config_path`` or when a ``config.yaml`` is auto-detected.
    Does **not** call ``ensure_dirs()`` — the caller is expected to
    do that once per dequeue so a worker draining many jobs doesn't
    pay the stat/mkdir cost on every dispatch.
    """
    payload = message.payload
    work_root = payload.get("work_root") or default_work_root()
    output_root = payload.get("output_root") or default_output_root()
    _validate_payload_root(work_root, default_work_root(), "work_root")
    _validate_payload_root(output_root, default_output_root(), "output_root")
    config_path = payload.get("config_path")
    ledger = JobContext._resolve_ledger(config_path)
    return JobContext(
        job_id=message.job_id,
        work_root=work_root,
        output_root=output_root,
        tenant_id=message.tenant_id,
        ledger=ledger,
    )


async def _dispatch_message(
    queue: JobQueue,
    message: JobMessage,
    ctx: JobContext | None = None,
) -> str:
    """Dispatch a single queued ``JobMessage`` to the matching handler.

    The handler registry maps a ``command`` string to a function
    ``(ctx, payload) -> Any``. Sync handlers are invoked directly;
    async handlers return a coroutine that is awaited. Unknown
    commands and handler exceptions are reported via ``queue.fail``
    so a single bad job never kills the worker loop.

    If ``ctx`` is provided it is reused; otherwise a fresh context is
    built via ``_build_job_context``. The worker loop builds one
    context per dequeued message and calls ``ensure_dirs()`` once,
    then passes it here so the ledger connection is shared for the
    duration of the job.

    Returns a status literal so the worker loop can account for the
    message exactly once without inspecting queue internals:

    - ``"completed"`` — handler succeeded; ``queue.complete`` called.
    - ``"failed"`` — handler raised (or unknown command);
      ``queue.fail`` called (message retried to ``_delayed`` or dead-
      lettered, per its retry budget).
    - ``"lost"`` — another worker reclaimed the job while this one ran it
      (#298). The claim is not ours, so neither ``complete`` nor ``fail``
      touched the row; the new owner decides the outcome.
    - ``"unknown"`` — dispatcher returned without moving the message
      to a terminal state. The worker loop treats this as an orphan
      and fails the message itself (guard against future regressions).
    """
    if ctx is None:
        ctx = _build_job_context(message)

    handler = _HANDLERS.get(message.command)
    if handler is None:
        typer.echo(f"Worker: unknown command {message.command!r}", err=True)
        await queue.fail(message, f"unknown_command: {message.command}")
        return "failed"

    try:
        # Sync handlers do blocking work (FFmpeg, provider calls), so they run
        # in a worker thread: the heartbeat task on this loop must keep beating
        # for the whole job, otherwise another worker reclaims a live job
        # (#298).
        if inspect.iscoroutinefunction(handler):
            result = handler(ctx, message.payload)
        else:
            result = await asyncio.to_thread(handler, ctx, message.payload)
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001 — fail the job, keep the worker alive
        typer.echo(f"Worker: {message.command} failed: {exc!r}", err=True)
        try:
            record_pipeline_failed(ctx, repr(exc), status="failed")
        except Exception:  # noqa: BLE001 — ledger failure must not break queue handling
            logger.warning("ledger_failure_record_failed", job_id=ctx.job_id, exc_info=True)
        try:
            await queue.fail(message, repr(exc))
        except LeaseLostError:
            logger.warning("worker_lease_lost", job_id=message.job_id, command=message.command)
            return "lost"
        return "failed"

    try:
        await queue.complete(message)
    except LeaseLostError:
        logger.warning("worker_lease_lost", job_id=message.job_id, command=message.command)
        return "lost"
    return "completed"


# Re-exported for test monkeypatching compatibility.
# Tests patch these on ``video_editor_cli.main``, but handlers import them
# directly. Re-exporting lets monkeypatch.setattr(main, ...) still work.
_HANDLERS = HANDLERS  # Backward-compat alias; monkeypatched by tests


register_agy_setup(app)
register_check_config(app)
register_cache_prune(app)
register_estimate(app)
register_finalize(app)
register_poyo_catalog(app)
register_preview(app)
register_update_pricing(app)
register_gen_models(app)
register_doctor(app)


def _register_group_commands() -> None:
    """Register the extracted command groups after ``main`` is fully defined."""
    from video_editor_cli.commands.assets import register_assets
    from video_editor_cli.commands.brand_capture import register_brand_capture
    from video_editor_cli.commands.bridges import register_bridges
    from video_editor_cli.commands.critique import register_critique
    from video_editor_cli.commands.explain import register_explain
    from video_editor_cli.commands.ledger import register_ledger
    from video_editor_cli.commands.longform import register_longform
    from video_editor_cli.commands.misc import register_misc
    from video_editor_cli.commands.musical import register_musical
    from video_editor_cli.commands.queue import register_queue
    from video_editor_cli.commands.run import register_run
    from video_editor_cli.commands.short import register_short
    from video_editor_cli.commands.style_from import register_style_from
    from video_editor_cli.commands.talking_avatar import register_talking_avatar
    from video_editor_cli.commands.voice_clone import register_voice_clone

    register_assets(app)
    register_brand_capture(app)
    register_bridges(app)
    register_critique(app)
    register_explain(app)
    register_ledger(app)
    register_longform(app)
    register_misc(app)
    register_musical(app)
    register_queue(app)
    register_run(app)
    register_short(app)
    register_style_from(app)
    register_talking_avatar(app)
    register_voice_clone(app)


_register_group_commands()

if __name__ == "__main__":
    app()
