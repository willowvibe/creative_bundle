"""Tests for the typed command registry, built-ins, control protocol and MCP tools."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from mcp.types import CallToolRequestParams
from pydantic import BaseModel
from video_editor_engine.commands import (
    Command,
    CommandError,
    CommandExecutionError,
    CommandPermissionError,
    CommandRegistry,
    CommandValidationError,
    DuplicateCommandError,
    UnknownCommandError,
    default_registry,
)
from video_editor_engine.commands.control import _handle_request
from video_editor_mcp.server import _on_call_tool, _on_list_tools
from video_editor_state.trust import TrustLevel


def _run(coro: Any) -> Any:
    """Run a coroutine to completion in a plain (sync) test."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registry behavior
# ---------------------------------------------------------------------------


class _AddParams(BaseModel):
    a: int
    b: int = 1


class _EmptyParams(BaseModel):
    pass


def _add_handler(params: _AddParams) -> dict[str, int]:
    return {"sum": params.a + params.b}


def _add_command() -> Command:
    return Command(
        id="test.add",
        summary="Add two integers",
        category="test",
        params_model=_AddParams,
        handler=_add_handler,
    )


class TestCommandRegistry:
    def test_register_get_and_sorted_list(self) -> None:
        registry = CommandRegistry()
        registry.register(
            Command(
                id="test.zeta",
                summary="z",
                category="test",
                params_model=_EmptyParams,
                handler=lambda _params: {"ok": True},
            )
        )
        registry.register(_add_command())

        assert registry.get("test.add").summary == "Add two integers"
        assert [c.id for c in registry.list_commands()] == ["test.add", "test.zeta"]
        assert len(registry) == 2
        assert "test.add" in registry

    def test_duplicate_registration_raises(self) -> None:
        registry = CommandRegistry()
        registry.register(_add_command())
        with pytest.raises(DuplicateCommandError) as excinfo:
            registry.register(_add_command())
        assert isinstance(excinfo.value, CommandError)
        assert excinfo.value.command_id == "test.add"

    def test_unknown_command_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(UnknownCommandError) as excinfo:
            registry.get("nope")
        assert isinstance(excinfo.value, CommandError)

    def test_unknown_command_execute_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(UnknownCommandError):
            _run(registry.execute("nope"))

    def test_param_validation_failure(self) -> None:
        registry = CommandRegistry()
        registry.register(_add_command())
        with pytest.raises(CommandValidationError) as excinfo:
            _run(registry.execute("test.add", {"a": "not-an-int"}))
        assert isinstance(excinfo.value, CommandError)

    def test_handler_failure(self) -> None:
        registry = CommandRegistry()

        def _boom(_params: _EmptyParams) -> dict[str, Any]:
            raise ValueError("kaboom")

        registry.register(
            Command(
                id="test.boom",
                summary="boom",
                category="test",
                params_model=_EmptyParams,
                handler=_boom,
            )
        )
        with pytest.raises(CommandExecutionError) as excinfo:
            _run(registry.execute("test.boom", {}))
        assert "kaboom" in str(excinfo.value)
        assert isinstance(excinfo.value, CommandError)

    def test_execute_validates_and_returns_result(self) -> None:
        registry = CommandRegistry()
        registry.register(_add_command())
        assert _run(registry.execute("test.add", {"a": 2, "b": 3})) == {"sum": 5}
        # params is optional when the model has defaults / none required
        assert _run(registry.execute("test.add", {"a": 4})) == {"sum": 5}

    def test_execute_awaits_async_handler(self) -> None:
        registry = CommandRegistry()

        async def _async_handler(_params: _EmptyParams) -> dict[str, str]:
            return {"kind": "async"}

        registry.register(
            Command(
                id="test.async",
                summary="async",
                category="test",
                params_model=_EmptyParams,
                handler=_async_handler,
            )
        )
        assert _run(registry.execute("test.async", {})) == {"kind": "async"}

    def test_execute_runs_sync_handlers_off_the_loop(self) -> None:
        """#299: a blocking handler must not stall the MCP stdio loop."""
        registry = CommandRegistry()
        loop_thread = threading.get_ident()
        seen: list[int] = []

        def _sync_handler(_params: _EmptyParams) -> dict[str, str]:
            seen.append(threading.get_ident())
            return {"kind": "sync"}

        registry.register(
            Command(
                id="test.thread",
                summary="thread",
                category="test",
                params_model=_EmptyParams,
                handler=_sync_handler,
            )
        )

        assert _run(registry.execute("test.thread", {})) == {"kind": "sync"}
        assert seen and seen[0] != loop_thread

    def test_execute_dumps_pydantic_results(self) -> None:
        registry = CommandRegistry()

        class _Out(BaseModel):
            name: str
            count: int

        registry.register(
            Command(
                id="test.model",
                summary="model result",
                category="test",
                params_model=_EmptyParams,
                handler=lambda _params: _Out(name="x", count=1),
            )
        )
        result = _run(registry.execute("test.model", {}))
        assert result == {"name": "x", "count": 1}

    def test_command_to_dict_is_json_serializable(self) -> None:
        payload = _add_command().to_dict()
        assert payload["id"] == "test.add"
        assert payload["category"] == "test"
        assert payload["mutates"] is False
        assert payload["spends"] is False
        assert "params_schema" in payload
        json.dumps(payload)  # must not raise


# ---------------------------------------------------------------------------
# Built-in commands
# ---------------------------------------------------------------------------


class TestBuiltinCommands:
    def test_builtins_registered(self) -> None:
        ids = {c.id for c in default_registry.list_commands()}
        assert {"probe.media", "templates.list", "pricing.cheapest", "ticks.info"} <= ids

    def test_ticks_info(self) -> None:
        from video_editor_core.ticks import frames_for_ticks, seconds_to_ticks

        result = _run(default_registry.execute("ticks.info", {"seconds": 1.5, "fps": 30}))
        assert result["ticks"] == seconds_to_ticks(1.5)
        assert result["frames"] == frames_for_ticks(seconds_to_ticks(1.5), 30)
        assert result["ticks_per_second"] == 254_016_000_000

    def test_templates_list(self) -> None:
        result = _run(default_registry.execute("templates.list", {}))
        assert result["count"] >= 1
        assert result["templates"]
        assert {"name", "pipeline", "required_inputs"} <= set(result["templates"][0])

    def test_pricing_cheapest_found(self) -> None:
        result = _run(
            default_registry.execute(
                "pricing.cheapest", {"model": "gpt-image-2", "asset_type": "image"}
            )
        )
        assert result["found"] is True
        assert result["provider"]
        assert result["unit_cost"] >= 0

    def test_pricing_cheapest_not_found(self) -> None:
        result = _run(
            default_registry.execute(
                "pricing.cheapest", {"model": "definitely-not-a-model", "asset_type": "image"}
            )
        )
        assert result["found"] is False

    def test_probe_media_builtin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import video_editor_ffmpeg.probe as probe_module

        class _FakeInfo:
            path = "/tmp/x.mp4"
            duration = 1.0
            width = 320
            height = 240
            fps = 30.0
            has_audio = True
            video_stream = {"codec_name": "h264"}
            audio_stream = {"codec_name": "aac"}

        monkeypatch.setattr(probe_module, "probe_media", lambda _path: _FakeInfo())
        result = _run(default_registry.execute("probe.media", {"path": "/tmp/x.mp4"}))
        assert result["width"] == 320
        assert result["video_codec"] == "h264"
        assert result["has_audio"] is True


# ---------------------------------------------------------------------------
# Control protocol
# ---------------------------------------------------------------------------


class TestControlProtocol:
    def test_ping(self) -> None:
        response = _run(_handle_request({"method": "engine.ping"}))
        assert response == {"ok": True, "result": "pong"}

    def test_commands_catalog(self) -> None:
        response = _run(_handle_request({"method": "engine.commands"}))
        assert response["ok"] is True
        ids = {item["id"] for item in response["result"]}
        assert "probe.media" in ids
        assert all("params_schema" in item for item in response["result"])

    def test_execute_ok(self) -> None:
        request = {
            "method": "engine.execute",
            "params": {"command": "ticks.info", "params": {"seconds": 2.0, "fps": 25}},
        }
        response = _run(_handle_request(request))
        assert response["ok"] is True
        assert response["result"]["ticks"] == 254_016_000_000 * 2

    def test_execute_unknown_command(self) -> None:
        request = {"method": "engine.execute", "params": {"command": "nope"}}
        response = _run(_handle_request(request))
        assert response["ok"] is False
        assert "Unknown command" in response["error"]

    def test_execute_validation_error(self) -> None:
        request = {"method": "engine.execute", "params": {"command": "probe.media", "params": {}}}
        response = _run(_handle_request(request))
        assert response["ok"] is False
        assert "Invalid params" in response["error"]

    def test_execute_missing_command(self) -> None:
        response = _run(_handle_request({"method": "engine.execute", "params": {}}))
        assert response["ok"] is False
        assert "command" in response["error"]

    def test_malformed_line_does_not_raise(self) -> None:
        response = _run(_handle_request("{this is not json"))
        assert response == {"ok": False, "error": "invalid JSON"}

    def test_non_utf8_line(self) -> None:
        response = _run(_handle_request(b"\xff\xfe\x00"))
        assert response["ok"] is False
        assert "UTF-8" in response["error"]

    def test_non_object_request(self) -> None:
        response = _run(_handle_request("123"))
        assert response == {"ok": False, "error": "request must be a JSON object"}

    def test_unknown_method(self) -> None:
        response = _run(_handle_request({"method": "engine.nope"}))
        assert response["ok"] is False
        assert "unknown method" in response["error"]

    def test_params_not_an_object(self) -> None:
        response = _run(_handle_request({"method": "engine.execute", "params": [1, 2]}))
        assert response["ok"] is False
        assert "params must be a JSON object" in response["error"]


# ---------------------------------------------------------------------------
# MCP command tools
# ---------------------------------------------------------------------------


class TestMCPCommandTools:
    def test_tools_registered(self) -> None:
        result = _run(_on_list_tools(None, None))
        names = {tool.name for tool in result.tools}
        assert {"list_commands", "run_command"} <= names

    def test_list_commands_tool(self) -> None:
        result = _run(
            _on_call_tool(None, CallToolRequestParams(name="list_commands", arguments={}))
        )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        ids = {item["id"] for item in payload["commands"]}
        assert "probe.media" in ids

    def test_run_command_tool(self) -> None:
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={
                        "command": "ticks.info",
                        "params_json": json.dumps({"seconds": 1.0}),
                    },
                ),
            )
        )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["result"]["ticks"] == 254_016_000_000

    def test_run_command_unknown_is_error(self) -> None:
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(name="run_command", arguments={"command": "nope"}),
            )
        )
        assert result.is_error is True
        assert "Unknown command" in json.loads(result.content[0].text)["error"]

    def test_run_command_bad_params_json_is_error(self) -> None:
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={"command": "ticks.info", "params_json": "{not json"},
                ),
            )
        )
        assert result.is_error is True
        assert "not valid JSON" in json.loads(result.content[0].text)["error"]

    def test_run_command_refuses_a_mutating_command(self) -> None:
        """#299: a write must go through a guarded tool, not the registry."""
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={
                        "command": "qa.run",
                        "params_json": json.dumps({"job_id": "j1"}),
                    },
                ),
            )
        )
        assert result.is_error is True
        assert "not available over MCP" in json.loads(result.content[0].text)["error"]

    def test_run_command_refuses_a_spending_command(self) -> None:
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={
                        "command": "assets.resolve",
                        "params_json": json.dumps({"job_id": "j1"}),
                    },
                ),
            )
        )
        assert result.is_error is True
        error = json.loads(result.content[0].text)["error"]
        assert "not available over MCP" in error
        assert "spends money" in error

    def test_run_command_refuses_config_path(self) -> None:
        """A caller must not choose the config that selects providers/keys."""
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={
                        "command": "project.status",
                        "params_json": json.dumps(
                            {"job_id": "j1", "config_path": "/tmp/evil.yaml"}
                        ),
                    },
                ),
            )
        )
        assert result.is_error is True
        assert "config_path is not accepted" in json.loads(result.content[0].text)["error"]

    def test_run_command_refuses_a_root_outside_the_data_roots(self) -> None:
        result = _run(
            _on_call_tool(
                None,
                CallToolRequestParams(
                    name="run_command",
                    arguments={
                        "command": "project.status",
                        "params_json": json.dumps({"job_id": "j1", "work_root": "/tmp"}),
                    },
                ),
            )
        )
        assert result.is_error is True
        assert "inside the configured data roots" in json.loads(result.content[0].text)["error"]

    def test_list_commands_marks_side_effects(self) -> None:
        result = _run(
            _on_call_tool(None, CallToolRequestParams(name="list_commands", arguments={}))
        )
        payload = json.loads(result.content[0].text)
        by_id = {item["id"]: item for item in payload["commands"]}
        assert by_id["assets.resolve"]["mutates"] is True
        assert by_id["assets.resolve"]["spends"] is True
        assert by_id["ticks.info"]["mutates"] is False
        assert by_id["ticks.info"]["spends"] is False


# ---------------------------------------------------------------------------
# Trust policy (A10)
# ---------------------------------------------------------------------------


class TestTrustPolicy:
    def _command(self, *, mutates: bool = False, spends: bool = False) -> Command:
        return Command(
            id="test.policy",
            summary="policy",
            category="test",
            params_model=_EmptyParams,
            handler=lambda _params: {"ok": True},
            mutates=mutates,
            spends=spends,
        )

    def test_mcp_refuses_a_mutating_command(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command(mutates=True))
        with pytest.raises(CommandPermissionError, match="read-only"):
            _run(registry.execute("test.policy", {}, trust=TrustLevel.MCP))

    def test_control_refuses_a_spending_command(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command(spends=True))
        with pytest.raises(CommandPermissionError, match="cannot approve spend"):
            _run(registry.execute("test.policy", {}, trust=TrustLevel.CONTROL))

    def test_control_allows_a_mutating_command(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command(mutates=True))
        assert _run(registry.execute("test.policy", {}, trust=TrustLevel.CONTROL)) == {"ok": True}

    def test_untrusted_cannot_choose_config_path(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command())
        with pytest.raises(CommandPermissionError, match="config_path"):
            _run(
                registry.execute(
                    "test.policy", {"config_path": "/tmp/evil.yaml"}, trust=TrustLevel.MCP
                )
            )

    def test_untrusted_root_must_be_inside_the_data_roots(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command())
        with pytest.raises(CommandPermissionError, match="inside the configured data roots"):
            _run(registry.execute("test.policy", {"work_root": "/tmp"}, trust=TrustLevel.MCP))

    def test_untrusted_plan_name_must_be_a_bare_filename(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command())
        with pytest.raises(CommandPermissionError, match="bare filename"):
            _run(
                registry.execute(
                    "test.policy", {"plan_out_name": "../../x.json"}, trust=TrustLevel.CONTROL
                )
            )

    def test_cli_keeps_full_access(self) -> None:
        registry = CommandRegistry()
        registry.register(self._command(mutates=True, spends=True))
        assert _run(
            registry.execute("test.policy", {"config_path": "/tmp/x.yaml"}, trust=TrustLevel.CLI)
        ) == {"ok": True}


class TestControlPolicy:
    def test_control_refuses_a_spending_command(self) -> None:
        response = _run(
            _handle_request(
                {
                    "method": "engine.execute",
                    "params": {"command": "assets.resolve", "params": {"job_id": "j1"}},
                }
            )
        )
        assert response["ok"] is False
        assert "cannot approve spend" in response["error"]

    def test_control_refuses_config_path(self) -> None:
        response = _run(
            _handle_request(
                {
                    "method": "engine.execute",
                    "params": {
                        "command": "project.status",
                        "params": {"job_id": "j1", "config_path": "/tmp/x.yaml"},
                    },
                }
            )
        )
        assert response["ok"] is False
        assert "config_path" in response["error"]
