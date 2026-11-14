"""Asset generation tools for the MCP server.

These tools wrap the real ``AssetIngestionManager`` so AI agents can
generate images, TTS, and music through typed MCP tool calls.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_config import Settings

from video_editor_mcp.context import (
    McpRequestError,
    check_call_cap,
    estimate_request_usd,
    job_budget,
    make_job_ctx,
    mcp_config,
)


def list_asset_tools() -> list[Tool]:
    """Return asset tool definitions."""
    return [
        Tool(
            name="generate_image",
            description="Generate an image from a text prompt using configured AI providers",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image generation prompt"},
                    "provider": {
                        "type": "string",
                        "description": "Provider name (poyo_ai, fal_ai, pexels). Defaults to scored auto-selection.",
                    },
                    "width": {"type": "integer", "description": "Image width (default 1920)"},
                    "height": {"type": "integer", "description": "Image height (default 1080)"},
                    "approve_usd": {
                        "type": "number",
                        "description": "Approve a generation estimated above the server's per-call cap (USD)",
                    },
                },
                "required": ["prompt"],
            },
        ),
        Tool(
            name="generate_tts",
            description="Generate speech audio from text using configured TTS providers",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak"},
                    "voice_id": {"type": "string", "description": "Voice identifier"},
                    "language": {
                        "type": "string",
                        "description": "Language code (en, hi, ja, etc.). Used for provider routing.",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Provider name override. Defaults to tts_router for automatic selection.",
                    },
                    "approve_usd": {
                        "type": "number",
                        "description": "Approve a generation estimated above the server's per-call cap (USD)",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="generate_music",
            description="Generate background music from a style prompt",
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Music style description"},
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Duration in seconds (default 30)",
                    },
                    "provider": {
                        "type": "string",
                        "description": "Provider name (poyo_music, ace_step; ElevenLabs is voice-over only). Defaults to scored auto-selection.",
                    },
                    "approve_usd": {
                        "type": "number",
                        "description": "Approve a generation estimated above the server's per-call cap (USD)",
                    },
                },
                "required": ["prompt"],
            },
        ),
    ]


def _result_provider(result: Any) -> str:
    """Return the resolved provider name from an AssetResult.

    AssetResult stores the provider in metadata; this helper centralises the
    lookup so callers don't repeat the fallback logic.
    """
    metadata = getattr(result, "metadata", {}) or {}
    return str(metadata.get("provider", "unknown"))


def _error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": message}))],
        is_error=True,
    )


def _build_request(name: str, arguments: dict[str, Any], job_id: str) -> AssetRequest | None:
    """Translate one asset tool call into the request the manager resolves."""
    if name == "generate_image":
        return AssetRequest(
            asset_id=f"mcp-img-{uuid.uuid4().hex[:8]}",
            job_id=job_id,
            scene_id="mcp",
            asset_type=AssetType.IMAGE,
            prompt=arguments["prompt"],
            provider=arguments.get("provider", "auto"),
            params={
                "width": arguments.get("width", 1920),
                "height": arguments.get("height", 1080),
            },
        )
    if name == "generate_tts":
        params: dict[str, Any] = {}
        if arguments.get("voice_id"):
            params["voice_id"] = arguments["voice_id"]
        if arguments.get("language"):
            params["language"] = arguments["language"]
        return AssetRequest(
            asset_id=f"mcp-tts-{uuid.uuid4().hex[:8]}",
            job_id=job_id,
            scene_id="mcp",
            asset_type=AssetType.TTS,
            prompt=arguments["text"],
            provider=arguments.get("provider", "tts_router"),
            params=params,
        )
    if name == "generate_music":
        return AssetRequest(
            asset_id=f"mcp-music-{uuid.uuid4().hex[:8]}",
            job_id=job_id,
            scene_id="mcp",
            asset_type=AssetType.MUSIC,
            prompt=arguments["prompt"],
            provider=arguments.get("provider", "auto"),
            params={
                "duration_seconds": arguments.get("duration_seconds", 30),
            },
        )
    return None


async def call_asset_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle asset tool calls by dispatching to the real AssetIngestionManager.

    Every call runs under the MCP job budget and the per-call cap (#277): the
    request is priced before anything is generated, a price above the cap needs
    ``approve_usd``, and the estimate is returned with the result.
    """
    try:
        config = Settings.load().config.assets
        limits = mcp_config()
    except Exception as exc:  # noqa: BLE001 - report the failure to the MCP client
        return _error(f"Config load failed: {exc}")

    try:
        ctx = make_job_ctx()
        request = _build_request(name, arguments, ctx.job_id)
        if request is None:
            return _error(f"Unknown tool: {name}")
        manager = AssetIngestionManager(ctx, config=config, budget=job_budget(config=limits))
        estimate = estimate_request_usd(manager, request)
        check_call_cap(estimate, arguments.get("approve_usd"), config=limits)

        result = (await manager.resolve([request]))[0]
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "generated",
                            "provider": _result_provider(result),
                            "local_path": result.local_path,
                            "duration_ms": result.duration_ms,
                            "estimate_usd": estimate,
                            "metadata": result.metadata,
                        }
                    ),
                )
            ]
        )
    except McpRequestError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - report the failure to the MCP client
        return _error(str(exc))
