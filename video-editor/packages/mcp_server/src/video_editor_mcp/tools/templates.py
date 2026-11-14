"""Template tools for the MCP server."""

import json
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool

from video_editor_mcp.context import contained_input_path


def list_template_tools() -> list[Tool]:
    """Return template tool definitions."""
    return [
        Tool(
            name="list_templates",
            description="List all available video templates",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="select_template",
            description="Select the best template for a given source",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Path to source file"},
                    "template_name": {
                        "type": "string",
                        "description": "Optional explicit template name",
                    },
                },
                "required": ["source"],
            },
        ),
    ]


async def call_template_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle template tool calls."""
    try:
        from video_editor_core.templates import TemplateRouter

        router = TemplateRouter()
    except Exception as e:  # noqa: BLE001 - report the failure to the MCP client
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(e)}))],
            is_error=True,
        )

    if name == "list_templates":
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"templates": router.template_names}),
                )
            ]
        )
    elif name == "select_template":
        template_name = arguments.get("template_name")
        try:
            source = contained_input_path(arguments.get("source", ""), "source")
            result = router.select(source=source, template_name=template_name)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "template": result.template_name,
                                "pipeline_kind": result.pipeline_kind,
                                "preset": result.preset,
                                "aspect_ratio": result.aspect_ratio,
                                "providers": result.providers,
                                "notes": result.notes,
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
