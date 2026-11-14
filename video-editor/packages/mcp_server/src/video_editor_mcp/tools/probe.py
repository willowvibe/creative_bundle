"""Media probe tools for the MCP server."""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from video_editor_mcp.context import contained_input_path


def list_probe_tools() -> list[Tool]:
    """Return probe tool definitions."""
    return [
        Tool(
            name="probe_media",
            description="Get technical metadata about a media file",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to media file"},
                },
                "required": ["path"],
            },
        ),
    ]


async def call_probe_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle probe tool calls."""
    if name == "probe_media":
        try:
            from video_editor_ffmpeg.probe import probe_media

            info = probe_media(contained_input_path(arguments["path"], "path"))
            video_codec = info.video_stream.get("codec_name", "") if info.video_stream else ""
            audio_codec = info.audio_stream.get("codec_name", "") if info.audio_stream else ""
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "duration": info.duration,
                                "width": info.width,
                                "height": info.height,
                                "fps": info.fps,
                                "codec": video_codec,
                                "audio_codec": audio_codec,
                                "has_audio": info.has_audio,
                            }
                        ),
                    )
                ]
            )
        except Exception as e:  # noqa: BLE001 - report the failure to the MCP client
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps({"error": str(e)}))],
                is_error=True,
            )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))],
        is_error=True,
    )
