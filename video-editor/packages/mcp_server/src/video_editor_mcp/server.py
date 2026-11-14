"""MCP server exposing the video editor engine as typed tools.

Built on the modern high-level ``mcp.server.mcpserver.MCPServer`` (mcp 2.x).
The tool catalog + dispatch live in ``_build_tool_list`` / ``_dispatch_tool``
(also used by the low-level ``_on_list_tools`` / ``_on_call_tool`` compatibility
entry points that tests exercise); ``create_server`` registers each catalog
tool as a typed handler whose signature is derived from the tool's JSON schema.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import structlog
from mcp.server.mcpserver import MCPServer
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from video_editor_mcp.tools.assets import call_asset_tool, list_asset_tools
from video_editor_mcp.tools.commands import call_command_tool, list_command_tools
from video_editor_mcp.tools.ledger import call_ledger_tool, list_ledger_tools
from video_editor_mcp.tools.pipeline import call_pipeline_tool, list_pipeline_tools
from video_editor_mcp.tools.probe import call_probe_tool, list_probe_tools
from video_editor_mcp.tools.templates import call_template_tool, list_template_tools
from video_editor_mcp.tools.vlog_skills import (
    call_vlog_skill_tool,
    list_vlog_skill_tools,
)

logger = structlog.get_logger(__name__)

SERVER_NAME = "video-editor"

_PIPELINE_TOOLS = ("run_pipeline", "run_skill")
_ASSET_TOOLS = ("generate_image", "generate_tts", "generate_music")
_TEMPLATE_TOOLS = ("list_templates", "select_template")
_COMMAND_TOOLS = ("list_commands", "run_command")
_LEDGER_TOOLS = ("project_status", "cost_report", "list_projects")
_VLOG_SKILL_TOOLS = (
    "broll",
    "reframe",
    "screen_tutorial",
    "sfx_placement",
    "chapters",
    "dubbing",
    "shorts_factory",
    "thumbnail",
    "beat_sync",
)


def _build_tool_list() -> list[Tool]:
    """Aggregate tools from all tool groups."""
    tools: list[Tool] = []
    tools.extend(list_pipeline_tools())
    tools.extend(list_asset_tools())
    tools.extend(list_template_tools())
    tools.extend(list_command_tools())
    tools.extend(list_probe_tools())
    tools.extend(list_ledger_tools())
    tools.extend(list_vlog_skill_tools())
    return tools


async def _on_list_tools(
    ctx: Any,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    return ListToolsResult(tools=_build_tool_list())


async def _dispatch_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Route a tool call to its handler group."""
    if name in _PIPELINE_TOOLS:
        return await call_pipeline_tool(name, arguments)
    if name in _ASSET_TOOLS:
        return await call_asset_tool(name, arguments)
    if name in _TEMPLATE_TOOLS:
        return await call_template_tool(name, arguments)
    if name in _COMMAND_TOOLS:
        return await call_command_tool(name, arguments)
    if name == "probe_media":
        return await call_probe_tool(name, arguments)
    if name in _LEDGER_TOOLS:
        return await call_ledger_tool(name, arguments)
    if name in _VLOG_SKILL_TOOLS:
        return await call_vlog_skill_tool(name, arguments)

    return CallToolResult(
        content=[TextContent(type="text", text=f'{{"error": "Unknown tool: {name}"}}')],
        is_error=True,
    )


async def _on_call_tool(
    ctx: Any,
    params: CallToolRequestParams,
) -> CallToolResult:
    return await _dispatch_tool(params.name, params.arguments or {})


# ---------------------------------------------------------------------------
# High-level server (MCPServer)
# ---------------------------------------------------------------------------

_PY_TYPES: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _signature_from_schema(tool: Tool) -> inspect.Signature:
    """Build an ``inspect.Signature`` for a tool from its JSON input schema."""
    schema = tool.input_schema or {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    parameters: list[inspect.Parameter] = []
    for prop_name, spec in properties.items():
        annotation = _PY_TYPES.get(spec.get("type"), Any)
        if prop_name in required:
            parameters.append(
                inspect.Parameter(prop_name, inspect.Parameter.KEYWORD_ONLY, annotation=annotation)
            )
        else:
            parameters.append(
                inspect.Parameter(
                    prop_name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                    default=spec.get("default", None),
                )
            )
    return inspect.Signature(parameters)


def _make_tool_handler(name: str, tool: Tool) -> Callable[..., Any]:
    """Create an async handler for ``name`` with its schema-derived signature."""

    async def _handler(**kwargs: Any) -> str:
        result = await _dispatch_tool(name, kwargs)
        return "".join(getattr(item, "text", "") for item in result.content)

    _handler.__name__ = name
    _handler.__doc__ = tool.description or ""
    _handler.__signature__ = _signature_from_schema(tool)  # type: ignore[attr-defined]
    return _handler


def create_server() -> MCPServer:
    """Build the MCP server with all catalog tools registered."""
    server = MCPServer(SERVER_NAME)
    for tool in _build_tool_list():
        server.add_tool(
            _make_tool_handler(tool.name, tool),
            name=tool.name,
            description=tool.description,
            structured_output=False,
        )
    return server


async def main_async() -> None:
    """Run the MCP server over stdio."""
    await create_server().run_stdio_async()


def main() -> None:
    """Entry point for the video-editor-mcp console script."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
