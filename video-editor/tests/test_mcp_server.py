"""Tests for the MCP server tools and routing."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolRequestParams
from video_editor_mcp.server import _on_call_tool, _on_list_tools, create_server

# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing tests reach the real asset tools: keep them off disk and off the network.

    The tools now build a real job directory under the data roots and resolve
    through the configured image chain (agy, then paid providers), so without
    this a routing check would generate — and bill — a real asset.
    """
    from video_editor_asset_ingestion.manager import AssetIngestionManager

    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path / "outputs"))

    async def disabled(self: object, requests: object) -> object:
        raise RuntimeError("asset providers are disabled in MCP routing tests")

    monkeypatch.setattr(AssetIngestionManager, "resolve", disabled)


class TestMCPServer:
    def test_server_creation(self):
        """Server should be creatable without errors."""
        server = create_server()
        assert server is not None
        assert server.name == "video-editor"

    @pytest.mark.anyio
    async def test_high_level_server_registers_catalog_tools(self):
        """The MCPServer registers every catalog tool with its schema."""
        server = create_server()
        tools = await server.list_tools()
        assert len(tools) == 23
        run_pipeline = next(t for t in tools if t.name == "run_pipeline")
        assert run_pipeline.input_schema["required"] == ["source"]
        assert "source" in run_pipeline.input_schema["properties"]


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestToolListing:
    """Verify all expected tools are registered."""

    @pytest.mark.anyio
    async def test_all_tools_registered(self):
        """Every tool group should contribute its tools."""
        result = await _on_list_tools(None, None)
        names = {t.name for t in result.tools}
        expected = {
            "run_pipeline",
            "run_short",
            "job_status",
            "run_skill",
            "generate_image",
            "generate_tts",
            "generate_music",
            "list_templates",
            "select_template",
            "probe_media",
            # Typed command registry tools
            "list_commands",
            "run_command",
            "project_status",
            "cost_report",
            "list_projects",
            # Vlogger + tech YouTuber skill tools (Task 20)
            "broll",
            "reframe",
            "screen_tutorial",
            "sfx_placement",
            "chapters",
            "shorts_factory",
            "thumbnail",
            "beat_sync",
        }
        assert names == expected, f"Missing: {expected - names}, Extra: {names - expected}"

    @pytest.mark.anyio
    async def test_list_tools_returns_all(self):
        """The MCP list_tools handler should return all tools."""
        result = await _on_list_tools(None, None)
        names = {t.name for t in result.tools}
        assert "run_pipeline" in names
        assert "probe_media" in names
        assert "generate_image" in names
        assert "beat_sync" in names  # new in Task 20
        assert "run_command" in names  # typed command registry
        assert "run_short" in names  # content-pack short (#303)
        assert len(result.tools) == 23

    @pytest.mark.anyio
    async def test_each_tool_has_description(self):
        """Every tool should have a non-empty description."""
        result = await _on_list_tools(None, None)
        for tool in result.tools:
            assert tool.description, f"Tool {tool.name} has no description"

    @pytest.mark.anyio
    async def test_each_tool_has_input_schema(self):
        """Every tool should have an input_schema."""
        result = await _on_list_tools(None, None)
        for tool in result.tools:
            assert tool.input_schema is not None, f"Tool {tool.name} has no input_schema"
            assert "properties" in tool.input_schema, f"Tool {tool.name} schema missing properties"


# ---------------------------------------------------------------------------
# Tool call routing
# ---------------------------------------------------------------------------


class TestToolRouting:
    """Verify tool calls route to the correct handler group."""

    @pytest.mark.anyio
    async def test_unknown_tool_returns_error(self):
        """Calling an unknown tool should return an error result."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="nonexistent_tool", arguments={}),
        )
        assert result.is_error is True
        text = json.loads(result.content[0].text)
        assert "Unknown tool" in text["error"]

    @pytest.mark.anyio
    async def test_probe_media_routes_to_probe_handler(self):
        """probe_media should route to the probe handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="probe_media", arguments={"path": "/nonexistent"}),
        )
        # Should get an error about missing file, not "Unknown tool"
        text = json.loads(result.content[0].text)
        assert "error" in text
        assert "Unknown tool" not in text["error"]

    @pytest.mark.anyio
    async def test_list_templates_routes_to_template_handler(self):
        """list_templates should route to the template handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="list_templates", arguments={}),
        )
        assert result.is_error is False
        text = json.loads(result.content[0].text)
        assert "templates" in text

    @pytest.mark.anyio
    async def test_select_template_routes_to_template_handler(self):
        """select_template should route to the template handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="select_template", arguments={"source": "/tmp/test.mp4"}),
        )
        # May error if file doesn't exist, but should not be "Unknown tool"
        text = json.loads(result.content[0].text)
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_project_status_routes_to_ledger_handler(self):
        """project_status should route to the ledger handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="project_status", arguments={"project_id": "nonexistent"}),
        )
        text = json.loads(result.content[0].text)
        # Should get "not_found" from the real ledger, not "Unknown tool"
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_cost_report_routes_to_ledger_handler(self):
        """cost_report should route to the ledger handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="cost_report", arguments={}),
        )
        assert result.is_error is False
        text = json.loads(result.content[0].text)
        assert isinstance(text, list)  # cost_report returns a list

    @pytest.mark.anyio
    async def test_list_projects_routes_to_ledger_handler(self):
        """list_projects should route to the ledger handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="list_projects", arguments={}),
        )
        assert result.is_error is False
        text = json.loads(result.content[0].text)
        assert "projects" in text
        assert "count" in text

    @pytest.mark.anyio
    async def test_generate_image_routes_to_asset_handler(self):
        """generate_image should route to the asset handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="generate_image", arguments={"prompt": "test"}),
        )
        text = json.loads(result.content[0].text)
        # Should not be "Unknown tool" — may error on missing API key, but routing works
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_generate_tts_routes_to_asset_handler(self):
        """generate_tts should route to the asset handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="generate_tts", arguments={"text": "hello"}),
        )
        text = json.loads(result.content[0].text)
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_generate_music_routes_to_asset_handler(self):
        """generate_music should route to the asset handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="generate_music", arguments={"prompt": "test"}),
        )
        text = json.loads(result.content[0].text)
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_run_pipeline_routes_to_pipeline_handler(self):
        """run_pipeline should route to the pipeline handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="run_pipeline",
                arguments={"source": "/nonexistent/video.mp4"},
            ),
        )
        text = json.loads(result.content[0].text)
        # Should error about missing file, not "Unknown tool"
        assert "Unknown tool" not in text.get("error", "")

    @pytest.mark.anyio
    async def test_run_skill_routes_to_pipeline_handler(self):
        """run_skill should route to the pipeline handler."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="run_skill",
                arguments={"skill": "rough_cut", "job_id": "test-job"},
            ),
        )
        text = json.loads(result.content[0].text)
        assert "Unknown tool" not in text.get("error", "")


# ---------------------------------------------------------------------------
# Tool input validation
# ---------------------------------------------------------------------------


class TestToolInputValidation:
    """Verify tools handle missing/invalid inputs gracefully."""

    @pytest.mark.anyio
    async def test_project_status_missing_id(self):
        """project_status without project_id should return an error."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="project_status", arguments={}),
        )
        text = json.loads(result.content[0].text)
        assert "error" in text

    @pytest.mark.anyio
    async def test_probe_media_missing_path(self):
        """probe_media without path should return an error."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="probe_media", arguments={}),
        )
        assert result.is_error is True

    @pytest.mark.anyio
    async def test_run_skill_unknown_skill_name(self):
        """run_skill with an unknown skill name should return an error."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="run_skill",
                arguments={"skill": "nonexistent_skill", "job_id": "test"},
            ),
        )
        text = json.loads(result.content[0].text)
        assert "error" in text
        assert "Unknown skill" in text["error"]

    @pytest.mark.anyio
    async def test_run_skill_missing_source_for_rough_cut(self):
        """run_skill rough_cut without source should return an error."""
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="run_skill",
                arguments={"skill": "rough_cut", "job_id": "test"},
            ),
        )
        text = json.loads(result.content[0].text)
        assert "error" in text
        assert "source" in text["error"].lower()


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    """Verify tool input schemas declare required fields correctly."""

    @pytest.mark.anyio
    async def _get_tools(self) -> dict:
        result = await _on_list_tools(None, None)
        return {t.name: t for t in result.tools}

    @pytest.mark.anyio
    async def test_generate_image_requires_prompt(self):
        tools = await self._get_tools()
        schema = tools["generate_image"].input_schema
        assert "prompt" in schema["required"]

    @pytest.mark.anyio
    async def test_generate_tts_requires_text(self):
        tools = await self._get_tools()
        schema = tools["generate_tts"].input_schema
        assert "text" in schema["required"]

    @pytest.mark.anyio
    async def test_generate_music_requires_prompt(self):
        tools = await self._get_tools()
        schema = tools["generate_music"].input_schema
        assert "prompt" in schema["required"]

    @pytest.mark.anyio
    async def test_run_pipeline_requires_source(self):
        tools = await self._get_tools()
        schema = tools["run_pipeline"].input_schema
        assert "source" in schema["required"]

    @pytest.mark.anyio
    async def test_run_skill_requires_skill_and_job_id(self):
        tools = await self._get_tools()
        schema = tools["run_skill"].input_schema
        assert "skill" in schema["required"]
        assert "job_id" in schema["required"]

    @pytest.mark.anyio
    async def test_project_status_requires_project_id(self):
        tools = await self._get_tools()
        schema = tools["project_status"].input_schema
        assert "project_id" in schema["required"]

    @pytest.mark.anyio
    async def test_probe_media_requires_path(self):
        tools = await self._get_tools()
        schema = tools["probe_media"].input_schema
        assert "path" in schema["required"]

    @pytest.mark.anyio
    async def test_select_template_requires_source(self):
        tools = await self._get_tools()
        schema = tools["select_template"].input_schema
        assert "source" in schema["required"]


# ---------------------------------------------------------------------------
# stdio integration
# ---------------------------------------------------------------------------


class TestMCPStdioIntegration:
    """End-to-end stdio test against the installed ``video-editor-mcp`` entry point."""

    @pytest.fixture
    def server_path(self) -> Path | None:
        return shutil.which("video-editor-mcp")

    @pytest.mark.anyio
    async def test_stdio_list_tools(self, server_path: Path | None) -> None:
        """The MCP server over stdio should respond to initialize + tools/list."""
        if server_path is None:
            pytest.skip("video-editor-mcp console script not installed")

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "video_editor_mcp.server",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            assert proc.stdin is not None
            assert proc.stdout is not None

            async def _send(message: dict) -> dict:
                payload = json.dumps(message) + "\n"
                proc.stdin.write(payload.encode())
                await proc.stdin.drain()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
                return json.loads(line.decode())

            init_response = await _send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                }
            )
            assert "result" in init_response, init_response

            tools_response = await _send(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            )
            assert "result" in tools_response, tools_response
            tools = tools_response["result"]["tools"]
            names = {t["name"] for t in tools}
            assert "run_pipeline" in names
            assert "probe_media" in names
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
