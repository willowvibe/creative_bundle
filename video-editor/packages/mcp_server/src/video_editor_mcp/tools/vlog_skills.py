"""Vlogger + Tech YouTuber skill tools for the MCP server.

These tools wrap the vlogger and tech-YouTuber skills implemented as
part of the vlogger/tech pipeline plan (Tasks 5–19). Each tool exposes
one skill through a typed MCP tool call so AI agents can drive
individual stages without invoking the full pipeline.

Skills wrapped here:

- ``broll``           — B-roll insertion (``BrollSkill``)
- ``reframe``         — Smart aspect reframing (``ReframeSkill``)
- ``screen_tutorial`` — Screen recording polish (``ScreenTutorialSkill``)
- ``sfx_placement``   — Context-aware SFX insertion (``SfxPlacementSkill``)
- ``chapters``        — YouTube chapter markers (``ChapterSkill``)
- ``shorts_factory``  — Long-form → Shorts (``ShortsFactorySkill``)
- ``thumbnail``       — Thumbnail candidate extraction (``ThumbnailSkill``)
- ``beat_sync``       — Beat-synced cut points (``BeatSyncSkill``)

The wrapper is a thin shim: each handler builds a real ``JobContext``
from the job_id (or generates a fresh temp dir), constructs the skill
with sensible defaults, and forwards the result. Async skills
(``broll``, ``screen_tutorial``, ``sfx_placement``) are awaited;
sync skills are called directly.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from video_editor_config import Settings
from video_editor_state.job import JobContext

from video_editor_mcp.context import contained_input_path, job_budget, make_job_ctx

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def list_vlog_skill_tools() -> list[Tool]:
    """Return vlog/tech skill tool definitions."""
    return [
        Tool(
            name="broll",
            description="Insert B-roll coverage over a base video based on its transcript",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to base video file"},
                    "transcript": {
                        "anyOf": [
                            {"type": "object"},
                            {"type": "string"},
                        ],
                        "description": "Transcript dict (segments/words) or path to transcript JSON",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Output filename (default: with_broll.mp4)",
                    },
                    "providers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Stock providers to try in order (default: pexels, pixabay)",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Optional job id (auto-generated if omitted)",
                    },
                },
                "required": ["video_path", "transcript"],
            },
        ),
        Tool(
            name="reframe",
            description="Smart crop reframe from any aspect ratio to a target vertical format",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to source video"},
                    "output_name": {
                        "type": "string",
                        "description": "Output filename (default: reframed.mp4)",
                    },
                    "target_aspect": {
                        "type": "string",
                        "description": "Target aspect ratio (default: 9:16)",
                    },
                    "enable_zoom": {
                        "type": "boolean",
                        "description": "Enable punch-in zoom (default: true)",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path"],
            },
        ),
        Tool(
            name="screen_tutorial",
            description="Polish a screen recording with zoom-to-cursor, click rings, and keystroke overlays",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to screen recording"},
                    "output_name": {
                        "type": "string",
                        "description": "Output filename (default: polished.mp4)",
                    },
                    "enable_zoom": {
                        "type": "boolean",
                        "description": "Enable zoom-to-cursor (default: true)",
                    },
                    "enable_clicks": {
                        "type": "boolean",
                        "description": "Enable click highlights (default: true)",
                    },
                    "enable_keystrokes": {
                        "type": "boolean",
                        "description": "Enable keystroke overlay (default: true)",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path"],
            },
        ),
        Tool(
            name="sfx_placement",
            description="Place context-aware sound effects into a video based on transcript keywords",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to base video"},
                    "transcript": {
                        "anyOf": [
                            {"type": "object"},
                            {"type": "string"},
                        ],
                        "description": "Transcript dict or path to transcript JSON",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Output filename (default: with_sfx.mp4)",
                    },
                    "provider": {
                        "type": "string",
                        "description": "SFX provider name (default: freesound_sfx)",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path", "transcript"],
            },
        ),
        Tool(
            name="chapters",
            description="Generate YouTube chapter markers from a transcript",
            input_schema={
                "type": "object",
                "properties": {
                    "transcript": {
                        "anyOf": [
                            {"type": "object"},
                            {"type": "string"},
                        ],
                        "description": "Transcript dict (with segments) or path to transcript JSON",
                    },
                    "duration": {
                        "type": "number",
                        "description": "Optional video duration in seconds",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["transcript"],
            },
        ),
        Tool(
            name="shorts_factory",
            description="Slice a long-form video into N vertical Shorts with viral moment detection",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {
                        "type": "string",
                        "description": "Path to long-form source video",
                    },
                    "transcript": {
                        "anyOf": [
                            {"type": "array"},
                            {"type": "object"},
                            {"type": "string"},
                            {"type": "null"},
                        ],
                        "description": "Optional transcript (list of segments or dict) or path to JSON",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of Shorts to produce (default: 4)",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path"],
            },
        ),
        Tool(
            name="thumbnail",
            description="Extract key frames from a final video and pick the best thumbnail candidates",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to final video"},
                    "candidates": {
                        "type": "integer",
                        "description": "Number of candidates (default: 5)",
                    },
                    "title_overlay": {
                        "type": "boolean",
                        "description": "Render title overlay (default: true)",
                    },
                    "title": {"type": "string", "description": "Optional title text for overlay"},
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path"],
            },
        ),
        Tool(
            name="beat_sync",
            description="Detect musical beats and emit beat-synced cut points for montages",
            input_schema={
                "type": "object",
                "properties": {
                    "video_path": {"type": "string", "description": "Path to source video"},
                    "audio_path": {
                        "type": "string",
                        "description": "Optional path to separate audio file",
                    },
                    "generate_cuts": {
                        "type": "boolean",
                        "description": "Generate cut points (default: true)",
                    },
                    "job_id": {"type": "string", "description": "Optional job id"},
                },
                "required": ["video_path"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def call_vlog_skill_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle vlog/tech skill tool calls."""
    try:
        if name == "broll":
            return await _handle_broll(arguments)
        elif name == "reframe":
            return await _handle_reframe(arguments)
        elif name == "screen_tutorial":
            return await _handle_screen_tutorial(arguments)
        elif name == "sfx_placement":
            return await _handle_sfx_placement(arguments)
        elif name == "chapters":
            return await _handle_chapters(arguments)
        elif name == "shorts_factory":
            return await _handle_shorts_factory(arguments)
        elif name == "thumbnail":
            return await _handle_thumbnail(arguments)
        elif name == "beat_sync":
            return await _handle_beat_sync(arguments)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"Unknown vlog-skill tool: {name}"}),
                )
            ],
            is_error=True,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure to the MCP client
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(exc)}))],
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job_ctx(job_id: str | None = None) -> JobContext:
    """Create the job context for one vlog skill call (see ``video_editor_mcp.context``)."""
    return make_job_ctx(job_id, prefix="mcp-vlog")


def _load_transcript(value: Any) -> Any:
    """Accept a transcript dict/list or a path to a JSON transcript.

    - If ``value`` is a string that exists as a file, load and parse it.
    - If ``value`` is a string that doesn't exist, raise ``FileNotFoundError``.
    - Otherwise return ``value`` as-is.
    """
    if isinstance(value, str):
        p = contained_input_path(value, "transcript")
        if p.exists():
            return json.loads(p.read_text())
        raise FileNotFoundError(f"Transcript file not found: {value}")
    return value


def _ok(text: str) -> CallToolResult:
    """Build a successful CallToolResult with serialized JSON text."""
    return CallToolResult(content=[TextContent(type="text", text=text)])


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_broll(arguments: dict[str, Any]) -> CallToolResult:
    """Insert B-roll clips over a base video."""
    from broll.broll import BrollSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")
    transcript = _load_transcript(arguments["transcript"])

    ctx = _make_job_ctx(arguments.get("job_id"))
    kwargs: dict[str, Any] = {}
    if arguments.get("providers"):
        kwargs["providers"] = list(arguments["providers"])
    if arguments.get("output_name"):
        kwargs["output_name"] = arguments["output_name"]

    # #277: b-roll may reach paid generators; run it under the job budget.
    from video_editor_asset_ingestion.manager import AssetIngestionManager

    kwargs["manager"] = AssetIngestionManager(
        ctx,
        config=Settings.load().config.assets,
        budget=job_budget(arguments.get("budget")),
    )
    skill = BrollSkill(ctx, **kwargs)
    result = await skill.run(video_path, transcript)
    return _ok(
        json.dumps({"status": "completed", "skill": "broll", "result": str(result)}, default=str)
    )


async def _handle_reframe(arguments: dict[str, Any]) -> CallToolResult:
    """Smart crop reframe to a vertical aspect ratio."""
    from reframe.reframe import ReframeSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")

    ctx = _make_job_ctx(arguments.get("job_id"))
    kwargs: dict[str, Any] = {}
    if "target_aspect" in arguments:
        kwargs["target_aspect"] = arguments["target_aspect"]
    if "enable_zoom" in arguments:
        kwargs["enable_zoom"] = bool(arguments["enable_zoom"])

    skill = ReframeSkill(ctx, **kwargs) if kwargs else ReframeSkill(ctx)
    output_name = arguments.get("output_name", "reframed.mp4")
    result = await asyncio.to_thread(skill.run, video_path, output_name=output_name)
    return _ok(
        json.dumps({"status": "completed", "skill": "reframe", "output": str(result)}, default=str)
    )


async def _handle_screen_tutorial(arguments: dict[str, Any]) -> CallToolResult:
    """Polish a screen recording with zoom/clicks/keystrokes."""
    from screen_tutorial.screen_tutorial import ScreenTutorialSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")

    ctx = _make_job_ctx(arguments.get("job_id"))
    kwargs: dict[str, Any] = {}
    for key in ("enable_zoom", "enable_clicks", "enable_keystrokes"):
        if key in arguments:
            kwargs[key] = bool(arguments[key])

    skill = ScreenTutorialSkill(ctx, **kwargs) if kwargs else ScreenTutorialSkill(ctx)
    output_name = arguments.get("output_name", "polished.mp4")
    result = await asyncio.to_thread(skill.run, video_path, output_name=output_name)
    return _ok(
        json.dumps(
            {"status": "completed", "skill": "screen_tutorial", "result": result}, default=str
        )
    )


async def _handle_sfx_placement(arguments: dict[str, Any]) -> CallToolResult:
    """Place SFX into a video based on transcript keywords."""
    from sfx_placement.sfx_placement import SfxPlacementSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")
    transcript = _load_transcript(arguments["transcript"])

    ctx = _make_job_ctx(arguments.get("job_id"))
    kwargs: dict[str, Any] = {}
    if arguments.get("provider"):
        kwargs["provider"] = arguments["provider"]
    if arguments.get("output_name"):
        kwargs["output_name"] = arguments["output_name"]

    # SFX generation is paid (ElevenLabs today, earshot later): run it under
    # the job budget, like b-roll (#277 follow-up).
    from video_editor_asset_ingestion.manager import AssetIngestionManager

    kwargs["manager"] = AssetIngestionManager(
        ctx,
        config=Settings.load().config.assets,
        budget=job_budget(arguments.get("budget")),
    )
    skill = SfxPlacementSkill(ctx, **kwargs)
    result = await skill.run(video_path, transcript)
    return _ok(
        json.dumps(
            {"status": "completed", "skill": "sfx_placement", "result": str(result)}, default=str
        )
    )


async def _handle_chapters(arguments: dict[str, Any]) -> CallToolResult:
    """Generate YouTube chapter markers from a transcript."""
    from chapters.chapters import ChapterSkill

    transcript = _load_transcript(arguments["transcript"])
    ctx = _make_job_ctx(arguments.get("job_id"))
    skill = ChapterSkill(ctx)

    kwargs: dict[str, Any] = {}
    if "duration" in arguments:
        kwargs["duration"] = float(arguments["duration"])

    result = await asyncio.to_thread(skill.run, transcript, **kwargs)
    return _ok(
        json.dumps({"status": "completed", "skill": "chapters", "result": result}, default=str)
    )


async def _handle_shorts_factory(arguments: dict[str, Any]) -> CallToolResult:
    """Slice a long-form video into N vertical Shorts."""
    from shorts_factory.shorts_factory import ShortsFactoryConfig, ShortsFactorySkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")

    transcript = None
    if arguments.get("transcript") is not None:
        transcript = _load_transcript(arguments["transcript"])

    ctx = _make_job_ctx(arguments.get("job_id"))
    config_kwargs: dict[str, Any] = {}
    if "count" in arguments:
        config_kwargs["count"] = int(arguments["count"])
    config = ShortsFactoryConfig(**config_kwargs) if config_kwargs else ShortsFactoryConfig()
    skill = ShortsFactorySkill(config=config)

    output_dir = Path(ctx.output_dir) / "shorts_factory"
    output_dir.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        skill.run,
        video_path,
        transcript,
        output_dir,
        count=config_kwargs.get("count"),
    )
    return _ok(
        json.dumps(
            {"status": "completed", "skill": "shorts_factory", "result": result}, default=str
        )
    )


async def _handle_thumbnail(arguments: dict[str, Any]) -> CallToolResult:
    """Extract thumbnail candidates from a final video."""
    from thumbnail.thumbnail import ThumbnailSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")

    ctx = _make_job_ctx(arguments.get("job_id"))
    skill = ThumbnailSkill()

    run_kwargs: dict[str, Any] = {"video_path": video_path}
    if "candidates" in arguments:
        run_kwargs["candidates"] = int(arguments["candidates"])
    if "title_overlay" in arguments:
        run_kwargs["title_overlay"] = bool(arguments["title_overlay"])
    if arguments.get("title"):
        run_kwargs["title"] = arguments["title"]

    result = await asyncio.to_thread(skill.run, ctx, **run_kwargs)
    return _ok(
        json.dumps({"status": "completed", "skill": "thumbnail", "result": result}, default=str)
    )


async def _handle_beat_sync(arguments: dict[str, Any]) -> CallToolResult:
    """Detect musical beats and emit cut points."""
    from beat_sync.beat_sync import BeatSyncConfig, BeatSyncSkill

    video_path = contained_input_path(arguments["video_path"], "video_path")
    if not video_path.exists():
        return _err(f"Video not found: {video_path}")

    ctx = _make_job_ctx(arguments.get("job_id"))
    config_kwargs: dict[str, Any] = {}
    if "generate_cuts" in arguments:
        config_kwargs["generate_cuts"] = bool(arguments["generate_cuts"])
    config = BeatSyncConfig(**config_kwargs) if config_kwargs else BeatSyncConfig()
    skill = BeatSyncSkill(config=config)

    run_kwargs: dict[str, Any] = {"video_path": video_path}
    if arguments.get("audio_path"):
        run_kwargs["audio_path"] = contained_input_path(arguments["audio_path"], "audio_path")

    result = await asyncio.to_thread(skill.run, ctx, **run_kwargs)
    return _ok(
        json.dumps({"status": "completed", "skill": "beat_sync", "result": result}, default=str)
    )


def _err(message: str) -> CallToolResult:
    """Build an error CallToolResult."""
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": message}))],
        is_error=True,
    )
