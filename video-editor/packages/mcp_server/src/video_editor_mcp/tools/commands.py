"""Command registry tools for the MCP server.

Exposes the typed engine command registry (``video_editor_engine.commands``) as
two generic MCP tools, so any registered command can be discovered and invoked
without a bespoke wrapper:

* ``list_commands`` — the catalog of command ids + param schemas.
* ``run_command`` — execute one command by id with a JSON params object.

``run_command`` is restricted to read-only commands (#299). A command that
declares ``mutates`` or ``spends`` must go through a guarded MCP tool
(``make_job_ctx`` / ``job_budget`` / ``_safe_filename``), not this generic
surface; the client also cannot choose ``config_path``.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import CallToolResult, TextContent, Tool


def list_command_tools() -> list[Tool]:
    """Return the command registry tool definitions."""
    return [
        Tool(
            name="list_commands",
            description="List every typed engine command with its id, category and param schema",
            input_schema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="run_command",
            description="Execute a typed engine command by id with JSON-encoded params",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command id, e.g. 'probe.media' or 'pricing.cheapest'",
                    },
                    "params_json": {
                        "type": "string",
                        "description": "JSON object string of command parameters (default '{}')",
                    },
                },
                "required": ["command"],
            },
        ),
    ]


def _error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps({"error": message}))],
        is_error=True,
    )


async def call_command_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Handle command registry tool calls."""
    from video_editor_engine.commands import CommandError, default_registry
    from video_editor_state.trust import TrustLevel

    if name == "list_commands":
        commands = [command.to_dict() for command in default_registry.list_commands()]
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"commands": commands}))]
        )

    if name == "run_command":
        command_id = arguments.get("command")
        if not isinstance(command_id, str) or not command_id:
            return _error("command is required")

        raw_params = arguments.get("params_json") or "{}"
        if isinstance(raw_params, str):
            try:
                params = json.loads(raw_params)
            except json.JSONDecodeError as exc:
                return _error(f"params_json is not valid JSON: {exc}")
        elif isinstance(raw_params, dict):
            params = raw_params
        else:
            return _error("params_json must be a JSON object string")

        if not isinstance(params, dict):
            return _error("params_json must decode to a JSON object")

        try:
            command = default_registry.get(command_id)
        except CommandError as exc:
            return _error(str(exc))

        if command.mutates or command.spends:
            reason = "spends money" if command.spends else "writes to the job"
            return _error(
                f"{command_id} is not available over MCP: it {reason}. "
                "Use the guarded tool for this capability instead."
            )
        if "config_path" in params:
            return _error(
                "config_path is not accepted over MCP; the server chooses the "
                "configuration so a caller cannot redirect providers or keys"
            )

        try:
            result = await default_registry.execute(command_id, params, trust=TrustLevel.MCP)
        except CommandError as exc:
            return _error(str(exc))
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"result": result}))]
        )

    return _error(f"Unknown tool: {name}")
