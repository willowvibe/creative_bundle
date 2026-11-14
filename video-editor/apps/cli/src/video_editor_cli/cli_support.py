"""CLI helpers and Typer option definitions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
import typer
from dotenv import load_dotenv
from video_editor_asset_ingestion.models import AssetRequest, AssetResult
from video_editor_config import ProjectConfig
from video_editor_llm.router import ModelRouter
from video_editor_state.errors import LeaseLostError, VEPipelineError
from video_editor_state.job import JobContext
from video_editor_state.ledger import NullLedger, ProjectLedger
from video_editor_state.queue import JobMessage, JobQueue

from video_editor_cli.constants import REPO_ROOT
from video_editor_cli.paths import default_output_root, default_work_root
from video_editor_cli.payloads import (
    PAYLOAD_SCHEMAS,
    RenderFableCutPayload,
    RunPayload,
    SpeechToSpeechPayload,
)

logger = structlog.get_logger(__name__)


class JobNotFoundError(VEPipelineError):
    """Raised when a downstream command is run on a job that has not been
    ingested yet."""


def _llm_router(
    config_path: Path | None,
    ctx: JobContext | None = None,
) -> ModelRouter | None:
    """Build a model router from an optional project config.yaml.

    When a job context is provided, every LLM call is recorded in the manifest
    operation log so the pipeline's reasoning remains auditable.
    """
    if config_path is None:
        return None

    def _log_callback(op: dict[str, Any]) -> None:
        if ctx is not None:
            ctx.manifest().log_operation(**op, tenant_id=ctx.tenant_id)

    return ModelRouter.from_config(config_path, log_callback=_log_callback)


def _discover_config_path(
    config_path: Path | None,
    *,
    start: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Return the config.yaml to use, searching the usual locations.

    Explicit ``--config`` wins, then ``$VE_CONFIG``, then ``config.yaml`` in
    ``start`` or any of its parents, then the repo root. Every other command
    already resolves its paths this way; ``ve plan-script`` should not be the
    one command that must repeat a path that is already the default (#258).
    """
    if config_path is not None:
        return config_path
    env_path = os.environ.get("VE_CONFIG")
    if env_path:
        return Path(env_path)
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / "config.yaml"
        if candidate.is_file():
            return candidate
    if repo_root is not None:
        root = Path(repo_root)
    else:
        from video_editor_cli import main as cli_main

        root = Path(getattr(cli_main, "REPO_ROOT", REPO_ROOT))
    fallback = root / "config.yaml"
    return fallback if fallback.is_file() else None


def _resolve_presets_dir(
    presets_dir: Path | None,
    config_path: Path | None,
) -> Path | None:
    """Return the effective presets directory.

    Explicit CLI flag wins. Otherwise load the project config and use
    ``paths.presets``. When a config file is provided, its parent directory
    is treated as the repo root so tests and per-project configs resolve
    relative to the config file. The default repo-relative ``presets/``
    folder is used when neither is provided.
    """
    if presets_dir is not None:
        return presets_dir
    project_config = ProjectConfig.load(config_path)
    repo_root = config_path.resolve().parent if config_path else REPO_ROOT
    return project_config.presets_dir(repo_root=repo_root)


def _preset_aspect_ratio(presets_dir: Path | None, preset: str) -> str | None:
    """Return ``preset``'s target aspect ratio, or ``None`` when unknown.

    The QA and review gates need the aspect the graphics preset actually
    renders; defaulting them to 16:9 fails every vertical preset.
    """
    if presets_dir is None:
        return None
    path = Path(presets_dir) / f"{preset}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    aspect = data.get("aspect_ratio") if isinstance(data, dict) else None
    return aspect if isinstance(aspect, str) and aspect else None


def _load_dotenv() -> None:
    """Load environment variables from the repository's .env file if present."""
    repo_root = Path(__file__).resolve().parents[4]
    env_file = repo_root / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


_STRUCTLOG_CONSOLE: dict[str, Any] = {
    "processors": [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    "context_class": dict,
    "logger_factory": structlog.stdlib.LoggerFactory(),
    "wrapper_class": structlog.stdlib.BoundLogger,
    "cache_logger_on_first_use": True,
}


_STRUCTLOG_JSON: dict[str, Any] = {
    "processors": [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    "context_class": dict,
    "logger_factory": structlog.stdlib.LoggerFactory(),
    "wrapper_class": structlog.stdlib.BoundLogger,
    "cache_logger_on_first_use": True,
}


def _configure_structlog() -> None:
    """Configure structlog for the current process.

    In production, set ``VIDEO_EDITOR_LOG_FORMAT=json`` to emit structured
    JSON logs that downstream log aggregators can parse. The default is the
    human-friendly console renderer.
    """
    fmt = os.getenv("VIDEO_EDITOR_LOG_FORMAT", "console").lower()
    config = _STRUCTLOG_JSON if fmt == "json" else _STRUCTLOG_CONSOLE
    structlog.configure(**config)


_PAYLOAD_SCHEMAS = PAYLOAD_SCHEMAS  # Backward-compat alias


_RenderFableCutPayload = RenderFableCutPayload  # Backward-compat alias


_RunPayload = RunPayload  # Backward-compat alias


_SpeechToSpeechPayload = SpeechToSpeechPayload  # Backward-compat alias


_DEFAULT_WORK_ROOT = default_work_root()


_DEFAULT_OUTPUT_ROOT = default_output_root()


_DEFAULT_PLAN_NAME = "scene_plan.json"


_DEFAULT_PLAN_OUT_NAME = "scene_plan_with_assets.json"


_DEFAULT_TTS_PROVIDER = "auto"


_DEFAULT_IMAGE_PROVIDER = "auto"


_DEFAULT_DATA_VIZ_PROVIDER = "auto"


_DEFAULT_VIDEO_PROVIDER = "auto"


_JOB_ID_ARG = typer.Argument(..., help="Job identifier")


_SOURCE_PATH_ARG = typer.Argument(..., help="Path to raw footage")


_RUN_SOURCE_ARG = typer.Argument(
    ...,
    help="Path to a raw-footage file, or a directory of clips",
)


_PATH_ARG = typer.Argument(..., help="Path to a media file")


_JOB_ID_OPT = typer.Option(..., "--job-id", "-j", help="Unique job identifier")


_WORK_ROOT_OPT = typer.Option(_DEFAULT_WORK_ROOT, "--work-root")


_OUTPUT_ROOT_OPT = typer.Option(_DEFAULT_OUTPUT_ROOT, "--output-root")


_FORCE_OPT = typer.Option(False, "--force", "-f")


_FORCE_RUN_OPT = typer.Option(
    False, "--force", "-f", help="Force re-run stages (legacy pipeline only)"
)


_OUTPUT_NAME_OPT = typer.Option("final.mp4", "--output-name", help="Output video filename")


_OUTPUT_NAME_CAPTIONS_OPT = typer.Option(
    "with_captions.mp4", "--output-name", help="Output video filename"
)


_OUTPUT_NAME_MUSIC_OPT = typer.Option(
    "with_music.mp4", "--output-name", help="Output video filename"
)


_PRESET_OPT = typer.Option(
    "short_explainer", "--preset", "-p", help="Graphics preset for the skills pipeline"
)


_PRESETS_DIR_OPT = typer.Option(None, "--presets-dir", help="Path to presets directory")


_MUSIC_PATH_OPT = typer.Option(None, "--music-path", "-m", help="Path to background music file")


_SKIP_MUSIC_OPT = typer.Option(
    False, "--skip-music", help="Skip the music stage even if a default music file exists"
)


_QA_OPT = typer.Option(
    False,
    "--qa",
    help=(
        "Run the QA self-evaluation gate before export. With --qa, the "
        "pipeline fails the build if any QA check fails (so a known-bad "
        "render never reaches the deliverable)."
    ),
)


_QA_PER_SEGMENT_OPT = typer.Option(
    False,
    "--qa-per-segment",
    help=(
        "Run per-segment QA at every cut boundary (PSNR visual-jump + "
        "audio-pop detection). Costs ~5% extra render time."
    ),
)


_SKIP_HYPERFRAMES_LINT_OPT = typer.Option(
    False,
    "--skip-hyperframes-lint",
    help=(
        "Skip the local HyperFrames plan lint that runs before each render. "
        "Useful only when debugging a plan that the linter rejects incorrectly."
    ),
)


_TRANSITION_OPT = typer.Option(
    "none",
    "--transition",
    help=(
        "Transition style between segments: none, fade, slideleft, "
        "slideright, slidedown, slideup, wipeleft, wiperight, radial."
    ),
)


_TRANSITION_DURATION_OPT = typer.Option(
    0.3,
    "--transition-duration",
    help="Transition overlap in seconds (default 0.3).",
)


_REFRAME_OPT = typer.Option(
    False,
    "--reframe",
    help=(
        "Reframe the base video to a vertical (9:16) format using smart "
        "face tracking. For raw_to_video templates (vlog_reel, "
        "talking_head_polish)."
    ),
)


_REFRAME_ASPECT_OPT = typer.Option(
    "9:16",
    "--reframe-aspect",
    help="Target aspect ratio for --reframe (default 9:16).",
)


_RENDER_ENGINE_OPT = typer.Option(
    "auto",
    "--render-engine",
    help=(
        "Render engine for the composition: auto (router — fast FFmpeg unless "
        "the graph needs HTML motion), ffmpeg (single-pass), or hyperframes "
        "(HTML composition)."
    ),
)


_BROLL_OPT = typer.Option(
    False,
    "--broll",
    help=(
        "Insert B-roll coverage over the base video driven by the transcript. "
        "For raw_to_video templates (vlog_reel, talking_head_polish)."
    ),
)


_SFX_PLACEMENT_OPT = typer.Option(
    False,
    "--sfx",
    help=(
        "Place context-aware sound effects into the video driven by the "
        "transcript. For raw_to_video templates (vlog_reel, "
        "talking_head_polish)."
    ),
)


_TRANSITIONS_OPT = typer.Option(
    False,
    "--transitions",
    help=(
        "Apply auto-selected transitions between rough-cut segments. For "
        "raw_to_video templates (vlog_reel, talking_head_polish)."
    ),
)


_AUDIO_CLEANUP_OPT = typer.Option(
    False,
    "--audio-cleanup",
    help=(
        "Run the audio cleanup pre-stage (de-noise/vocal-isolation) on the "
        "source before the rough cut. For raw_to_video templates."
    ),
)


_BUDGET_OPT = typer.Option(
    None,
    "--budget",
    help=(
        "Credit budget cap for generative asset generation (e.g. 50). "
        "When set, provider selection and spend are tracked and surfaced "
        "in the export report."
    ),
)


_BRAND_PRESETS_DIR_OPT = typer.Option(
    ..., "--presets-dir", "-p", help="Directory containing brand.json and brands/"
)


_BRAND_JOB_ID_OPT = typer.Option(None, "--job-id", "-j", help="Project/job id for override lookup")


_BRAND_TENANT_ID_OPT = typer.Option(None, "--tenant-id", "-t", help="Tenant id for override lookup")


_LOUDNESS_TARGET_OPT = typer.Option(
    None,
    "--loudness-target-lufs",
    help=(
        "Override the default -16 LUFS loudness target for the QA gate "
        "(e.g. -16.0 for YouTube, -14.0 for podcasts)."
    ),
)


_EXPECTED_ASPECT_OPT = typer.Option(
    "16:9",
    "--expected-aspect-ratio",
    help="Expected aspect ratio (16:9, 9:16, 1:1, 4:3). Forwarded to the QA skill.",
)


_RUN_EXPECTED_ASPECT_OPT = typer.Option(
    None,
    "--expected-aspect-ratio",
    help=(
        "Expected aspect ratio for the QA/review gates. Defaults to the "
        "aspect ratio declared by the active graphics preset."
    ),
)


_RUN_IMAGE_PROVIDER_OPT = typer.Option(
    "auto",
    "--image-provider",
    help=(
        "Still-image provider for `ve run`. 'auto' walks the configured "
        "assets.image_chain; a named provider bypasses it. Only storyteller "
        "templates build image requests."
    ),
)


_TEMPLATE_OPT = typer.Option(
    None,
    "--template",
    help=(
        "Explicitly select a video template instead of inferring it from the "
        "source path. See `ve list-templates` for supported names."
    ),
)


_AUTO_APPROVE_OPT = typer.Option(
    False, "--auto-approve", "-y", help="Skip the cost estimate approval prompt"
)


_EXPLAIN_FROM_TOPIC_OPT = typer.Option(
    "",
    "--from-topic",
    help="Draft script.md from this topic first, then price and build it.",
)


_EXPLAIN_TTS_PROVIDER_OPT = typer.Option(
    "tts_router", "--tts-provider", help="Narration TTS provider for `ve explain`."
)


_EXPLAIN_IMAGE_PROVIDER_OPT = typer.Option(
    "auto",
    "--image-provider",
    help=(
        "Still-image provider for `ve explain`. 'auto' walks the configured "
        "assets.image_chain; a named provider bypasses it."
    ),
)


_EXPLAIN_CAPTION_PRESET_OPT = typer.Option(
    "",
    "--caption-preset",
    help="Override the script's caption preset (e.g. karaoke).",
)


_EXPLAIN_NO_REUSE_OPT = typer.Option(
    False, "--no-reuse", help="Render every still separately instead of reusing reprised subjects."
)


_SKIP_PREVIEW_OPT = typer.Option(False, "--skip-preview", help="Skip the FableCut preview stage")


_COMMANDS_JSON_OPT = typer.Option(
    False, "--json", help="Emit machine-readable JSON instead of plain text."
)


_CONTROL_PORT_OPT = typer.Option(..., "--port", help="TCP port for the JSON-lines control server.")


_CONTROL_HOST_OPT = typer.Option(
    "127.0.0.1", "--host", help="Interface/host the control server binds to."
)


class _StorytellerTier(StrEnum):
    """Quality tier for storyteller image/video generation.

    Used by ``ve run --image-tier`` / ``--video-tier``. Overrides the
    template preset's default tier independently.
    """

    budget = "budget"
    standard = "standard"
    premium = "premium"


_IMAGE_TIER_OPT = typer.Option(
    None,
    "--image-tier",
    help=(
        "Image quality tier (overrides the template preset's default). "
        "Standard = gpt-image-2 1K low; budget = flux-schnell; "
        "premium = gpt-image-2 2K."
    ),
)


_VIDEO_TIER_OPT = typer.Option(
    None,
    "--video-tier",
    help=(
        "Motion quality tier (overrides the template preset's default). "
        "Standard = seedance-2-fast 720p; budget = wan2.2-image-to-video-fast 480p; "
        "premium = seedance-2 1080p."
    ),
)


_MEDIA_DIR_OPT = typer.Option(
    None,
    "--media-dir",
    help="Directory containing images/short videos for the musical_video template.",
)


_SEGMENT_SECONDS_OPT = typer.Option(
    5.0,
    "--segment-seconds",
    help="Target visual segment length for the musical_video template.",
)


_LYRICS_OPT = typer.Option(
    None,
    "--lyrics",
    help="Text file with lyrics for forced-alignment captions on a musical_video.",
)


_LANGUAGE_OPT = typer.Option(
    "en",
    "--language",
    help="Language code for the musical_video lyrics output filename.",
)


_CONFIG_OPT = typer.Option(None, "--config", "-c", help="Path to project config.yaml")


_CONFIG_RUN_OPT = typer.Option(None, "--config", "-c", help="Path to project config.yaml")


_TRANSCRIPT_ROUGH_CUT_OPT = typer.Option(
    None,
    "--transcript",
    "-t",
    help="Use an existing transcript JSON instead of running Whisper/WhisperX",
)


_TRANSCRIPT_RUN_OPT = typer.Option(
    None,
    "--transcript",
    "-t",
    help="Use an existing transcript JSON instead of running Whisper/WhisperX",
)


_TRANSCRIPT_GRAPHICS_OPT = typer.Option(None, "--transcript", "-t", help="Path to transcript JSON")


_SEGMENT_OPT = typer.Option(..., "--segment", "-s", help="Segment id to refine")


_START_OPT = typer.Option(None, "--start", help="New source start time")


_END_OPT = typer.Option(None, "--end", help="New source end time")


_LABEL_OPT = typer.Option(None, "--label", "-l", help="New segment label")


_ASSETS_PLAN_NAME_OPT = typer.Option(
    _DEFAULT_PLAN_NAME, "--plan-name", help="Input scene plan filename"
)


_ASSETS_PLAN_OUT_NAME_OPT = typer.Option(
    _DEFAULT_PLAN_OUT_NAME, "--plan-out-name", help="Output scene plan filename"
)


_ASSETS_TTS_PROVIDER_OPT = typer.Option(
    _DEFAULT_TTS_PROVIDER,
    "--tts-provider",
    help="Provider for narration TTS. Default 'auto' uses scored selection.",
)


_ASSETS_IMAGE_PROVIDER_OPT = typer.Option(
    _DEFAULT_IMAGE_PROVIDER,
    "--image-provider",
    help="Provider for visual descriptions. Default 'auto' uses scored selection.",
)


_ASSETS_DATA_VIZ_PROVIDER_OPT = typer.Option(
    _DEFAULT_DATA_VIZ_PROVIDER,
    "--data-viz-provider",
    help="Provider for data visualizations. Default 'auto' uses scored selection.",
)


_ASSETS_VIDEO_PROVIDER_OPT = typer.Option(
    _DEFAULT_VIDEO_PROVIDER,
    "--video-provider",
    help=(
        "Opt in to video generation for scenes that request real motion. "
        "Default 'auto' uses the scored provider selector (falls back to "
        "stub_hyperframes_video when no providers are configured). Set to a "
        "provider name (e.g. 'hyperframes_video' or 'stub_hyperframes_video') "
        "to also emit a VIDEO request for any scene with "
        "props.requires_motion=True; the renderer will pick the VIDEO "
        "over the IMAGE. HyperFrames renders a prompt-driven HTML composition "
        "to MP4."
    ),
)


_SFX_PROVIDER_OPT = typer.Option(
    "",
    "--sfx-provider",
    help=(
        "Opt in to per-scene AI sound effects (SFX). When set, the "
        "pipeline generates one SFX clip per scene via this provider "
        "(e.g. 'freesound_sfx', 'stub_sfx'; ElevenLabs is voice-over only) and mixes them into the "
        "audio track at each scene's start time. Empty (default) "
        "skips SFX entirely. Freesound and Openverse clips are free."
    ),
)


_SFX_GAIN_DB_OPT = typer.Option(
    -6.0,
    "--sfx-gain-db",
    help=(
        "Gain (dB) applied to each SFX stream when mixing into the "
        "base audio. -6.0 dB default keeps SFX under voiceover "
        "without ducking. Use -12.0 for busier voiceover mixes."
    ),
)


_MUSIC_PROMPT_OPT = typer.Option(
    "",
    "--music-prompt",
    help=(
        "Prompt for the AI music generator. When non-empty, the "
        "pipeline generates a single music track for the whole "
        "reel via --music-provider and routes it to MusicSkill. "
        "Empty (default) skips music generation. Cost: ~$0.10 per "
        "track with poyo_music."
    ),
)


_MUSIC_PROVIDER_OPT = typer.Option(
    "poyo_music",
    "--music-provider",
    help=(
        "Provider for AI music generation when --music-prompt is set. "
        "Default poyo_music (~$0.10/track). ElevenLabs music is disabled: "
        "ElevenLabs is used for voice-over only."
    ),
)


_MUSIC_DURATION_OPT = typer.Option(
    30,
    "--music-duration-seconds",
    help="Duration (seconds) of the AI music track when generated.",
)


_SFX_DURATION_OPT = typer.Option(
    5.0,
    "--sfx-duration-seconds",
    help="Duration (seconds) of each generated SFX clip.",
)


_ALIGN_PROVIDER_OPT = typer.Option(
    "",
    "--align-provider",
    help=(
        "Provider for forced alignment (audio -> word-level timestamps). "
        "No default: ElevenLabs alignment is disabled (ElevenLabs is voice-over only). "
        "Use 'stub_alignment' for offline tests."
    ),
)


_ALIGN_AUDIO_OPT = typer.Option(
    ...,
    "--audio",
    "-a",
    help=(
        "Path to the audio file to align against. Can be the source "
        "video itself (ffmpeg extracts audio), an extracted audio "
        "track, or any standalone MP3/WAV."
    ),
)


_ALIGN_TEXT_OPT = typer.Option(
    ...,
    "--text",
    "-t",
    help="The known transcript text to align to the audio.",
)


_ALIGN_OUTPUT_OPT = typer.Option(
    "alignment.json",
    "--output-name",
    help=(
        "Output file name (relative to <work>/transcript/). Default "
        "is 'alignment.json'. The file is in ElevenLabs alignment "
        "JSON format with 'words' and 'characters' arrays."
    ),
)


_ISOLATE_PROVIDER_OPT = typer.Option(
    "",
    "--isolate-provider",
    help=(
        "Provider for audio isolation / voice extraction. "
        "No default: ElevenLabs isolation is disabled (ElevenLabs is voice-over only). "
        "Use 'stub_isolation' for offline tests."
    ),
)


_ISOLATE_AUDIO_OPT = _ALIGN_AUDIO_OPT  # same semantics


_ISOLATE_OUTPUT_OPT = typer.Option(
    "isolated.mp3",
    "--output-name",
    help="Output file name (relative to <work>/audio/). Default is 'isolated.mp3'.",
)


_ISOLATE_FILE_FORMAT_OPT = typer.Option(
    "other",
    "--file-format",
    help=(
        "ElevenLabs audio-isolation file_format hint (``pcm_s16le_16`` for raw "
        "PCM/WAV, ``other`` for compressed). Default ``other``; a ``.wav`` "
        "--output-name automatically forces ``pcm_s16le_16`` regardless."
    ),
)


_STS_PROVIDER_OPT = typer.Option(
    "",
    "--sts-provider",
    help=(
        "Provider for speech-to-speech / voice conversion. "
        "No default: ElevenLabs speech-to-speech is disabled (ElevenLabs is voice-over only). "
        "Use 'stub_speech_to_speech' for offline tests."
    ),
)


_STS_AUDIO_OPT = _ALIGN_AUDIO_OPT  # same semantics


_STS_VOICE_ID_OPT = typer.Option(
    "",
    "--voice-id",
    "-v",
    help=(
        "ElevenLabs voice id to convert the source audio into. "
        "Defaults to the configured default_voice for the provider."
    ),
)


_STS_MODEL_ID_OPT = typer.Option(
    "",
    "--model-id",
    help=(
        "STS model id (eleven_english_sts_v2 or eleven_multilingual_sts_v2). "
        "Defaults to the configured default_model for the provider."
    ),
)


_STS_OUTPUT_OPT = typer.Option(
    "converted.mp3",
    "--output-name",
    help="Output file name (relative to <work>/audio/). Default is 'converted.mp3'.",
)


_STS_REMOVE_BG_OPT = typer.Option(
    False,
    "--remove-background-noise",
    help="Run audio isolation before the voice conversion.",
)


_STS_OUTPUT_FORMAT_OPT = typer.Option(
    "",
    "--output-format",
    help=(
        "ElevenLabs output-format string (e.g. mp3_44100_128, pcm_44100). "
        "Defaults to a format compatible with --output-ext."
    ),
)


_STS_OUTPUT_EXT_OPT = typer.Option(
    "",
    "--output-ext",
    help="Output file extension (e.g. mp3, wav). Defaults to --output-name's extension.",
)


_STS_SEED_OPT = typer.Option(
    0,
    "--seed",
    help="Optional seed for reproducible generation (provider-dependent).",
)


_STS_STABILITY_OPT = typer.Option(
    0.0,
    "--stability",
    help="Voice settings stability (0.0-1.0). Only sent when non-zero.",
)


_STS_SIMILARITY_OPT = typer.Option(
    0.0,
    "--similarity-boost",
    help="Voice settings similarity_boost (0.0-1.0). Only sent when non-zero.",
)


_BROLL_PROVIDER_OPT = typer.Option(
    "placeholder",
    "--broll-provider",
    help="B-roll provider for raw-footage pipeline (placeholder, fal_ai, stock)",
)


_VOICEOVER_OPT = typer.Option(
    ...,
    "--voiceover",
    "-v",
    help=(
        "Path to an external voiceover audio file (mp3/wav/m4a, e.g. TTS "
        "output). Its audio replaces the source video's audio track so "
        "the result can be fed into `ve run` for any narration-over-"
        "existing-video workflow (Issue A)."
    ),
)


_VOICEOVER_GAIN_OPT = typer.Option(
    0.0,
    "--voiceover-gain-db",
    help="Gain (dB) applied to the voiceover before muxing. Use a positive value for quiet TTS output.",
)


_AUDIO_OUTPUT_NAME_OPT = typer.Option(
    "with_voiceover.mp4",
    "--output-name",
    help="Output file name (relative to <work>/intake/). Default is 'with_voiceover.mp4'.",
)


_TENANT_ID_OPT = typer.Option("default", "--tenant-id", help="Tenant identifier")


_SCRIPT_PATH_ARG = typer.Argument(..., help="Path to a markdown script file")


_ASPECT_RATIO_OPT = typer.Option("16:9", "--aspect-ratio", help="Target aspect ratio")


_MUSIC_MOOD_OPT = typer.Option("", "--music-mood", help="Desired background music mood")


_PLAN_OUT_OPT = typer.Option("scene_plan.json", "--plan-out", help="Output scene plan filename")


_COMFYSTUDIO_PROJECT_DIR_OPT = typer.Option(
    ..., "--project-dir", "-d", help="Directory for the ComfyStudio project"
)


_COMFYSTUDIO_FPS_OPT = typer.Option(30, "--fps", help="Timeline FPS")


_COMFYSTUDIO_WIDTH_OPT = typer.Option(1920, "--width", help="Timeline width")


_COMFYSTUDIO_HEIGHT_OPT = typer.Option(1080, "--height", help="Timeline height")


_FABLECUT_PROJECT_DIR_OPT = typer.Option(
    ..., "--project-dir", "-d", help="Directory for the FableCut project"
)


_FABLECUT_FPS_OPT = typer.Option(30, "--fps", help="Timeline FPS")


_FABLECUT_WIDTH_OPT = typer.Option(1920, "--width", help="Timeline width")


_FABLECUT_HEIGHT_OPT = typer.Option(1080, "--height", help="Timeline height")


_RENDER_FABLECUT_PROJECT_DIR_OPT = typer.Option(
    ...,
    "--project-dir",
    "-d",
    help="Directory containing the edited FableCut project.json and media/",
)


_RENDER_FABLECUT_OUTPUT_NAME_OPT = typer.Option(
    "final.mp4", "--output-name", help="Output video filename (relative to <work>/output/)"
)


_RENDER_FABLECUT_FONT_OPT = typer.Option(
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "--font",
    help="Font file for caption text",
)


_RENDER_FABLECUT_FPS_OPT = typer.Option(None, "--fps", help="Override project FPS")


_RENDER_FABLECUT_WIDTH_OPT = typer.Option(None, "--width", help="Override project width")


_RENDER_FABLECUT_HEIGHT_OPT = typer.Option(None, "--height", help="Override project height")


_STORYTELLER_PROJECT_DIR_OPT = typer.Option(
    None, "--project-dir", help="FableCut project directory to write"
)


_TTS_TEST_TEXT_OPT = typer.Option(..., "--text", help="Text to synthesize")


_TTS_TEST_VOICE_OPT = typer.Option(..., "--voice", help='e.g. "sarvam:shubh" or "elevenlabs:Brian"')


_TTS_TEST_LANGUAGE_OPT = typer.Option(..., "--language", help='e.g. "hi-IN" or "en"')


_TTS_TEST_JOB_ID_OPT = typer.Option("audition", "--job-id", help="Job id used for the output path")


_VERIFY_OPT = typer.Option(
    False,
    "--verify",
    help="Run the requirements verification gate after export.",
)


_REVIEW_OPT = typer.Option(
    True,
    "--review/--skip-review",
    help="Run the post-render review gate after export (default: on).",
)


_REQUIRE_MIN_DURATION_OPT = typer.Option(
    None,
    "--min-duration",
    help="Minimum required output duration in seconds.",
)


_REQUIRE_MAX_DURATION_OPT = typer.Option(
    None,
    "--max-duration",
    help="Maximum required output duration in seconds.",
)


_REQUIRE_ASPECT_OPT = typer.Option(
    "",
    "--require-aspect",
    help="Required aspect ratio (16:9, 9:16, 1:1, 4:3).",
)


_REQUIRE_ELEMENT_OPT = typer.Option(
    [],
    "--require-element",
    help="Required element (captions, music, logo, etc.). Repeatable.",
)


_FORBID_ELEMENT_OPT = typer.Option(
    [],
    "--forbid-element",
    help="Forbidden element (background_music, captions, etc.). Repeatable.",
)


_MUST_MENTION_OPT = typer.Option(
    [],
    "--must-mention",
    help="Phrase that must appear in the transcript. Repeatable.",
)


_JUDGE_CHECK_OPT = typer.Option(
    [],
    "--judge-check",
    help="Subjective check for the LLM judge. Repeatable.",
)


_FROM_MD_OPT = typer.Option(
    ...,
    "--from-md",
    help="Path to a project.md to install into the job's work directory",
)


def _context(
    job_id: str,
    work_root: str = _DEFAULT_WORK_ROOT,
    output_root: str = _DEFAULT_OUTPUT_ROOT,
    *,
    config_path: Path | str | None = None,
) -> JobContext:
    ledger = JobContext._resolve_ledger(config_path)
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
        ledger=ledger,
    )
    ctx.ensure_dirs()
    return ctx


def _resolve_ledger_cli(config_path: Path | None) -> ProjectLedger | NullLedger:
    """Return the configured ledger, or a no-op ledger if disabled."""
    ledger = JobContext._resolve_ledger(config_path)
    if isinstance(ledger, NullLedger):
        typer.echo(
            "Ledger is disabled or no ledger path is configured. "
            "Add a 'ledger:' section to config.yaml to enable.",
            err=True,
        )
        raise typer.Exit(1)
    return ledger


_OTIO_PATH_OPT = typer.Option(
    None, "--otio-path", help="Path to the OpenTimelineIO (.otio) file to write/read."
)


async def _tts_test_synthesize(request: AssetRequest, output_path: Path) -> AssetResult:
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    return await TTSRouter().generate(request, output_path)


_Handler = Any


def _validate_payload_root(value: str, configured: str, label: str) -> None:
    """Keep a queued ``work_root``/``output_root`` inside the data roots (#278).

    ``JobContext`` already makes ``job_id`` one directory component, so this
    closes the other half: a payload cannot choose an arbitrary filesystem
    location. The configured root comes from ``VE_WORK_ROOT``/``VE_OUTPUT_ROOT``
    (or the repository defaults), the same resolution every entry point uses.
    """
    root = Path(value).expanduser().resolve()
    allowed = Path(configured).expanduser().resolve()
    if not root.is_relative_to(allowed):
        raise ValueError(
            f"payload {label} must be inside the configured data root {allowed}; got {root}"
        )


DEFAULT_STALE_SECONDS = 900.0


@contextlib.asynccontextmanager
async def _heartbeat(
    queue: JobQueue, message: JobMessage, stale_seconds: float
) -> AsyncIterator[None]:
    """Refresh ``message``'s lease while the body runs (#275).

    The beat comes three times per stale window (at most every 30 s), so one
    missed beat never lets another worker's ``requeue_stale`` take a live job.
    """
    interval = min(30.0, stale_seconds / 3)

    async def beat() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await queue.heartbeat(message)
            except LeaseLostError:
                # Another worker reclaimed the job (#298): stop beating, the
                # dispatcher will report the lost lease once the handler exits.
                logger.warning("worker_heartbeat_lease_lost", job_id=message.job_id)
                return
            except Exception:  # noqa: BLE001 — a missed beat must not kill the job
                logger.warning("worker_heartbeat_failed", job_id=message.job_id, exc_info=True)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
