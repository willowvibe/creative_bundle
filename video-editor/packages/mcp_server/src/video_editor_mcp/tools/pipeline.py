"""Pipeline tools for the MCP server.

These tools wrap the real skills pipeline so AI agents can run full
video generation workflows and individual skills through typed MCP
tool calls.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from video_editor_state.paths import default_queue_db
from video_editor_state.queue import JobMessage, PersistentJobQueue, idempotency_key_for

from video_editor_mcp.context import contained_input_path, job_budget, make_job_ctx


def list_pipeline_tools() -> list[Tool]:
    """Return pipeline tool definitions."""
    return [
        Tool(
            name="run_pipeline",
            description="Run the full skills pipeline on a source video to produce a final rendered MP4",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Path to source video file or markdown script",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Unique job identifier (auto-generated if omitted)",
                    },
                    "template": {
                        "type": "string",
                        "description": "Template name (e.g., explainer_reel, storyteller_hd, musical_video)",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Graphics preset name (default: short_explainer)",
                    },
                    "config_path": {
                        "type": "string",
                        "description": "Path to config.yaml for provider/LLM configuration",
                    },
                    "music_prompt": {
                        "type": "string",
                        "description": "Prompt for AI music generation",
                    },
                    "music_provider": {
                        "type": "string",
                        "description": "Music provider name (poyo_music, ace_step)",
                    },
                    "budget": {
                        "type": "number",
                        "description": "USD budget for generative assets; may only lower the server's mcp.default_budget_usd cap",
                    },
                    "transition": {
                        "type": "string",
                        "description": "Transition style: none, fade, slideleft, slideright, wipeleft, wiperight, radial",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Output video filename (default: final.mp4)",
                    },
                },
                "required": ["source"],
            },
        ),
        Tool(
            name="run_short",
            description=(
                "Price a content pack, record an approval up to approve_usd, and queue "
                "the short build (stills, voice, one render, QA)"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pack_path": {
                        "type": "string",
                        "description": "Path to the pack's short.yaml inside the data roots",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Job identifier (auto-generated if omitted)",
                    },
                    "approve_usd": {
                        "type": "number",
                        "description": "Maximum estimate the caller approves; the job is refused above it",
                    },
                    "final": {
                        "type": "boolean",
                        "description": "Use the final image chain (direct image APIs, no agy)",
                    },
                },
                "required": ["pack_path", "approve_usd"],
            },
        ),
        Tool(
            name="job_status",
            description="Read a queued/running/completed job's queue state and manifest",
            input_schema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job identifier"},
                    "work_root": {
                        "type": "string",
                        "description": "Optional CLI job work root inside the data roots",
                    },
                    "output_root": {
                        "type": "string",
                        "description": "Optional CLI job output root inside the data roots",
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="run_skill",
            description="Run a single skill stage on an existing job",
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Skill name: rough_cut, graphics, captions, music, export, review, verify",
                    },
                    "job_id": {
                        "type": "string",
                        "description": "Job identifier (must already exist with work directory)",
                    },
                    "source": {
                        "type": "string",
                        "description": "Path to source video (required for rough_cut)",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Graphics preset name (for graphics skill)",
                    },
                    "music_path": {
                        "type": "string",
                        "description": "Path to background music file (for music skill)",
                    },
                    "output_name": {
                        "type": "string",
                        "description": "Output filename override",
                    },
                    "config_path": {
                        "type": "string",
                        "description": "Path to config.yaml",
                    },
                    "work_root": {
                        "type": "string",
                        "description": "Work root of a CLI-built job; must be inside the configured data roots",
                    },
                    "output_root": {
                        "type": "string",
                        "description": "Output root of a CLI-built job; must be inside the configured data roots",
                    },
                },
                "required": ["skill", "job_id"],
            },
        ),
    ]


async def call_pipeline_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle pipeline tool calls by dispatching to the real engine."""
    try:
        if name == "run_pipeline":
            return await _handle_run_pipeline(arguments)
        elif name == "run_short":
            return await _handle_run_short(arguments)
        elif name == "job_status":
            return await _handle_job_status(arguments)
        elif name == "run_skill":
            return await _handle_run_skill(arguments)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))],
            is_error=True,
        )
    except Exception as exc:  # noqa: BLE001 - report the failure to the MCP client
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(exc)}))],
            is_error=True,
        )


async def _handle_run_pipeline(arguments: dict[str, Any]) -> CallToolResult:
    """Queue a full skills pipeline run (A7)."""
    source = arguments["source"]
    source_path = contained_input_path(source, "source")
    if not source_path.exists():
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"error": f"Source file not found: {source}"}),
                )
            ],
            is_error=True,
        )

    ctx = make_job_ctx(arguments.get("job_id"))
    config_path = None
    if arguments.get("config_path"):
        cp = Path(arguments["config_path"])
        if cp.exists():
            config_path = cp

    budget = job_budget(arguments.get("budget"))
    payload: dict[str, Any] = {
        "source": str(source_path),
        "work_root": str(ctx.work_root),
        "output_root": str(ctx.output_root),
        "preset": str(arguments.get("preset", "short_explainer")),
        "template": arguments.get("template"),
        "music_prompt": str(arguments.get("music_prompt", "")),
        "music_provider": str(arguments.get("music_provider", "poyo_music")),
        "music_duration_seconds": int(arguments.get("music_duration_seconds", 30)),
        "transition": str(arguments.get("transition", "none")),
        "transition_duration": float(arguments.get("transition_duration", 0.3)),
        "output_name": arguments.get("output_name"),
        "final": bool(arguments.get("final", False)),
        "review": True,
        "budget": budget.limit,
    }
    if config_path is not None:
        payload["config_path"] = str(config_path)

    queue = PersistentJobQueue(default_queue_db())
    stored = await queue.enqueue(
        JobMessage(
            job_id=ctx.job_id,
            tenant_id="mcp",
            command="run",
            payload=payload,
            idempotency_key=idempotency_key_for("run", payload, tenant_id="mcp"),
        )
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "queued",
                        "job_id": stored.job_id,
                        "queue": default_queue_db(),
                        "hint": "Start a worker with: ve worker",
                        "work_dir": str(ctx.work_root / stored.job_id),
                        "output_dir": str(ctx.output_root / stored.job_id),
                    },
                    default=str,
                ),
            )
        ]
    )


async def _handle_run_short(arguments: dict[str, Any]) -> CallToolResult:
    """Price, approve and queue a content-pack short (#303)."""
    import shutil

    from video_editor_core.content_pack import ContentPackError, load_content_pack
    from video_editor_pipelines.short_pipeline import (
        ShortPreflightError,
        preflight_short,
        price_short,
        save_short_estimate,
    )

    pack_source = contained_input_path(arguments["pack_path"], "pack_path")
    if not pack_source.is_file():
        return _tool_error(f"Content pack not found: {pack_source}")
    raw_approval = arguments.get("approve_usd")
    if raw_approval is None:
        return _tool_error("approve_usd is required: a short build must be approved before it runs")
    try:
        approve_usd = float(raw_approval)
    except (TypeError, ValueError):
        return _tool_error(f"approve_usd must be a number, got {raw_approval!r}")
    if approve_usd < 0:
        return _tool_error("approve_usd must not be negative")

    ctx = make_job_ctx(arguments.get("job_id"))
    pack_copy = ctx.work_dir / "short.yaml"
    shutil.copy2(pack_source, pack_copy)
    final = bool(arguments.get("final", False))
    try:
        pack = load_content_pack(pack_copy)
        preflight_short(pack)
        estimate = price_short(ctx, pack, final=final)
    except (ContentPackError, ShortPreflightError) as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # noqa: BLE001 - a pricing refusal is a tool error
        return _tool_error(str(exc))

    if estimate.total_cost > approve_usd:
        return _tool_error(
            f"estimated cost ${estimate.total_cost:.4f} is above approve_usd "
            f"${approve_usd:.4f}; re-run with a higher approval"
        )
    save_short_estimate(ctx, estimate)

    payload: dict[str, Any] = {
        "pack": str(pack_copy),
        "work_root": str(ctx.work_root),
        "output_root": str(ctx.output_root),
        "final": final,
    }
    queue = PersistentJobQueue(default_queue_db())
    stored = await queue.enqueue(
        JobMessage(
            job_id=ctx.job_id,
            tenant_id="mcp",
            command="short",
            payload=payload,
            idempotency_key=idempotency_key_for("short", payload, tenant_id="mcp"),
        )
    )
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "queued",
                        "job_id": stored.job_id,
                        "command": "short",
                        "approved_usd": approve_usd,
                        "quote_usd": estimate.total_cost,
                        "queue": default_queue_db(),
                        "hint": "Start a worker with: ve worker",
                        "work_dir": str(ctx.work_root / stored.job_id),
                        "output_dir": str(ctx.output_root / stored.job_id),
                    },
                    default=str,
                ),
            )
        ]
    )


def _tool_error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": message}))],
        is_error=True,
    )


async def _handle_job_status(arguments: dict[str, Any]) -> CallToolResult:
    """Read queue state plus the manifest for a job (A7)."""
    job_id = str(arguments["job_id"])
    queue = PersistentJobQueue(default_queue_db())
    status = await queue.status(job_id)
    if status is None:
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps({"error": f"job not found: {job_id}"}))
            ],
            is_error=True,
        )
    ctx = make_job_ctx(
        job_id,
        work_root=arguments.get("work_root"),
        output_root=arguments.get("output_root"),
    )
    manifest_path = ctx.work_dir / "manifest.json"
    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
    final_candidates = [
        ctx.output_dir / "final.mp4",
        ctx.output_dir / "output" / "final.mp4",
    ]
    final_video = next((str(path) for path in final_candidates if path.exists()), None)
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "job_id": job_id,
                        "queue": status,
                        "manifest": manifest,
                        "final_video": final_video,
                        "work_dir": str(ctx.work_dir),
                        "output_dir": str(ctx.output_dir),
                    },
                    default=str,
                ),
            )
        ]
    )


async def _handle_run_skill(arguments: dict[str, Any]) -> CallToolResult:
    """Run a single skill on an existing job."""
    skill_name = arguments["skill"]
    job_id = arguments["job_id"]

    # #276: the job lives under the data roots' ``mcp/`` subdirectory, where
    # ``run_pipeline`` puts it. ``work_root``/``output_root`` may point at a CLI
    # job instead, but only inside the configured data roots.
    ctx = make_job_ctx(
        job_id,
        work_root=arguments.get("work_root"),
        output_root=arguments.get("output_root"),
    )

    if arguments.get("config_path"):
        cp = Path(arguments["config_path"])
        if not cp.exists():
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"error": f"Config file not found: {arguments['config_path']}"}
                        ),
                    )
                ],
                is_error=True,
            )

    if skill_name == "rough_cut":
        source = arguments.get("source")
        if not source:
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps({"error": "'source' is required for rough_cut skill"}),
                    )
                ],
                is_error=True,
            )
        from rough_cut.rough_cut import RoughCutSkill

        result = await asyncio.to_thread(
            RoughCutSkill(ctx).run, contained_input_path(source, "source")
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "rough_cut", "output": str(result)}
                    ),
                )
            ]
        )

    elif skill_name == "graphics":
        from graphics.graphics import GraphicsSkill
        from video_editor_ffmpeg.render_options import RenderOptions

        result = await asyncio.to_thread(
            GraphicsSkill(ctx, render_options=RenderOptions()).run,
            preset_name=arguments.get("preset", "short_explainer"),
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "graphics", "output": str(result)}
                    ),
                )
            ]
        )

    elif skill_name == "captions":
        from captions.captions import CaptionsSkill
        from graphics.graphics import GraphicsSkill
        from video_editor_ffmpeg.render_options import RenderOptions

        graphics_skill = GraphicsSkill(ctx, render_options=RenderOptions())
        output_name = arguments.get("output_name", "with_captions.mp4")
        result = await asyncio.to_thread(
            CaptionsSkill(ctx, graphics_skill=graphics_skill).run, output_name=output_name
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "captions", "output": str(result)}
                    ),
                )
            ]
        )

    elif skill_name == "music":
        from music.music import MusicSkill

        music_path = (
            contained_input_path(arguments["music_path"], "music_path")
            if arguments.get("music_path")
            else None
        )
        output_name = arguments.get("output_name", "with_music.mp4")
        result = await asyncio.to_thread(
            MusicSkill(ctx, music_path=music_path).run, output_name=output_name
        )
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "music", "output": str(result)}
                    ),
                )
            ]
        )

    elif skill_name == "export":
        from export.export import ExportSkill

        result = await asyncio.to_thread(ExportSkill(ctx).run)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "export", "output": str(result)}
                    ),
                )
            ]
        )

    elif skill_name == "review":
        from review.review import PostRenderReviewSkill

        review_report = await asyncio.to_thread(PostRenderReviewSkill(ctx).run)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"status": "completed", "skill": "review", "output": str(review_report)}
                    ),
                )
            ]
        )

    elif skill_name == "verify":
        from verify.verify import VerifySkill

        report = await VerifySkill(ctx).run()
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "completed",
                            "skill": "verify",
                            "overall": report.overall,
                            "checks": len(report.checks),
                        }
                    ),
                )
            ]
        )

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "error": f"Unknown skill: {skill_name}. Available: rough_cut, graphics, captions, music, export, review, verify"
                    }
                ),
            )
        ],
        is_error=True,
    )
