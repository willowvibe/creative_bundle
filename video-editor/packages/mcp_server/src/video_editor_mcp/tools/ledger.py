"""Project ledger tools for the MCP server.

These tools wrap the real ``ProjectLedger`` so AI agents can query
project status and cost reports through typed MCP tool calls.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool
from video_editor_state.ledger import ProjectLedger


def list_ledger_tools() -> list[Tool]:
    """Return ledger tool definitions."""
    return [
        Tool(
            name="project_status",
            description="Get the full status of a project including jobs, costs, issues, and artifacts",
            input_schema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project identifier"},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="cost_report",
            description="Get aggregated cost report grouped by provider and task type",
            input_schema={
                "type": "object",
                "properties": {
                    "provider": {
                        "type": "string",
                        "description": "Filter by provider name (e.g., poyo_ai, elevenlabs_tts)",
                    },
                    "task_type": {
                        "type": "string",
                        "description": "Filter by task type (e.g., image, tts, music)",
                    },
                    "template": {
                        "type": "string",
                        "description": "Filter by project template name",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 date to filter costs from (e.g., 2026-07-01)",
                    },
                },
            },
        ),
        Tool(
            name="list_projects",
            description="List projects in the ledger with optional status and date filters",
            input_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by project status (active, completed, failed)",
                    },
                    "since": {
                        "type": "string",
                        "description": "ISO 8601 date to filter projects from",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of projects to return (default 20)",
                    },
                },
            },
        ),
    ]


def _get_ledger() -> ProjectLedger:
    """Return a ProjectLedger connected to the default database."""
    return ProjectLedger("outputs/projects.db")


async def call_ledger_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle ledger tool calls by dispatching to the real ProjectLedger."""
    try:
        ledger = _get_ledger()

        if name == "project_status":
            project_id = arguments.get("project_id", "")
            if not project_id:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps({"error": "project_id is required"}),
                        )
                    ],
                    is_error=True,
                )
            project = ledger.get_project(project_id)
            if project is None:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps({"status": "not_found", "project_id": project_id}),
                        )
                    ]
                )
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(project, default=str))]
            )

        elif name == "cost_report":
            cost_kwargs: dict[str, Any] = {}
            if arguments.get("provider"):
                cost_kwargs["provider"] = arguments["provider"]
            if arguments.get("task_type"):
                cost_kwargs["task_type"] = arguments["task_type"]
            if arguments.get("template"):
                cost_kwargs["template"] = arguments["template"]
            if arguments.get("since"):
                cost_kwargs["since"] = arguments["since"]
            report = ledger.cost_report(**cost_kwargs)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(report, default=str))]
            )

        elif name == "list_projects":
            list_kwargs: dict[str, Any] = {}
            if arguments.get("status"):
                list_kwargs["status"] = arguments["status"]
            if arguments.get("since"):
                list_kwargs["since"] = arguments["since"]
            list_kwargs["limit"] = arguments.get("limit", 20)
            projects = ledger.list_projects(**list_kwargs)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"projects": projects, "count": len(projects)}, default=str
                        ),
                    )
                ]
            )

        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))],
            is_error=True,
        )

    except Exception as exc:  # noqa: BLE001 - report the failure to the MCP client
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(exc)}))],
            is_error=True,
        )
