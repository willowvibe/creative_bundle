"""Tests for the vlogger + tech YouTuber MCP tool wrappers.

These tests verify that the new MCP tool group
(:mod:`video_editor_mcp.tools.vlog_skills`) exposes every new skill
with the correct schema, dispatches calls to the right handler, and
correctly forwards arguments to the underlying skill constructors.

We mock the skill modules so the tests run without ffmpeg, GPUs, or
real provider APIs — we're verifying the *wrapper* contract, not the
skills themselves.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolRequestParams
from video_editor_mcp.server import _on_call_tool, _on_list_tools
from video_editor_mcp.tools import vlog_skills

# ---------------------------------------------------------------------------
# Skill stubs
# ---------------------------------------------------------------------------


def _make_skill_module(name: str, run_value: Any = None) -> tuple[types.ModuleType, MagicMock]:
    """Build a fake skill module so ``from <name>.<name> import …`` works.

    Returns a tuple of (module, sentinel). The sentinel is a single
    MagicMock instance whose ``.run`` is either AsyncMock (async skills)
    or MagicMock (sync skills). ``__init__`` wires ``self.run`` to the
    sentinel's run and records args/kwargs on the sentinel.
    """
    cls_name = _SKILL_CLASS_NAME[name]
    sentinel = MagicMock(name=f"{cls_name}.sentinel")
    if name in {"broll", "sfx_placement"}:
        run_attr = AsyncMock(return_value=run_value or {"ok": True})
    else:
        run_attr = MagicMock(return_value=run_value if run_value is not None else Path("/tmp/out"))
    sentinel.run = run_attr

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:  # noqa: N807
        # Copy the sentinel's ``run`` (mock or asyncmock) onto the instance
        # so that ``skill.run(...)`` calls our tracked mock.
        self.run = run_attr  # type: ignore[attr-defined]
        sentinel.init_args = args
        sentinel.init_kwargs = kwargs
        sentinel.init_count = getattr(sentinel, "init_count", 0) + 1

    cls = type(cls_name, (), {"__init__": __init__})
    module = types.ModuleType(f"{name}.{name}")
    setattr(module, cls_name, cls)
    return module, sentinel


# Actual exported class names (not just ``name.capitalize()``).
_SKILL_CLASS_NAME: dict[str, str] = {
    "broll": "BrollSkill",
    "reframe": "ReframeSkill",
    "screen_tutorial": "ScreenTutorialSkill",
    "sfx_placement": "SfxPlacementSkill",
    "chapters": "ChapterSkill",
    "shorts_factory": "ShortsFactorySkill",
    "thumbnail": "ThumbnailSkill",
    "beat_sync": "BeatSyncSkill",
}


def _install_fake_skill_modules(monkeypatch: Any) -> dict[str, Any]:
    """Install fake ``<name>.<name>`` modules so imports succeed.

    Returns a dict mapping the lowercase skill name to a tuple
    ``(cls, sentinel)``. Tests can assert against ``sentinel.init_args`` /
    ``sentinel.init_kwargs`` / ``sentinel.run`` instead of the class
    object directly — MagicMock class attributes don't support
    ``assert_called_once`` on the class.

    The fake entries are removed from ``sys.modules`` when the fixture
    tears down via ``monkeypatch`` so a subsequent test that imports the
    real ``thumbnail`` package (for example) isn't shadowed.
    """
    skills: dict[str, Any] = {}
    for name in (
        "broll",
        "reframe",
        "screen_tutorial",
        "sfx_placement",
        "chapters",
        "shorts_factory",
        "thumbnail",
        "beat_sync",
    ):
        run_value: Any = None
        if name == "chapters":
            run_value = {"chapters": [{"start": 0.0, "title": "Intro"}]}
        elif name == "thumbnail":
            run_value = {"thumbnail": "/tmp/thumb.jpg"}
        elif name == "beat_sync":
            run_value = {"beats": [1.0, 2.0]}
        elif name == "shorts_factory":
            run_value = {"shorts": ["short_001"]}
        elif name == "screen_tutorial":
            run_value = {"output": "/tmp/polished.mp4"}
        elif name in {"broll", "sfx_placement"}:
            run_value = {"output": "/tmp/x.mp4", "moments": []}

        module, sentinel = _make_skill_module(name, run_value=run_value)
        if name == "shorts_factory":
            cfg_cls = type("ShortsFactoryConfig", (), {"__init__": MagicMock(return_value=None)})
            module.ShortsFactoryConfig = cfg_cls
        if name == "beat_sync":
            cfg_cls = type("BeatSyncConfig", (), {"__init__": MagicMock(return_value=None)})
            module.BeatSyncConfig = cfg_cls

        # Install into both ``<name>.<name>`` AND ``<name>`` so the
        # ``from <name>.<name> import …`` statements resolve.
        parent = types.ModuleType(name)
        parent.__path__ = []  # mark as package
        # Restore the original sys.modules entries when the test ends.
        monkeypatch.setitem(sys.modules, name, parent)
        monkeypatch.setitem(sys.modules, f"{name}.{name}", module)
        setattr(parent, name, module)
        cls = getattr(module, _SKILL_CLASS_NAME[name])
        # Reset the sentinel between fixtures; each test gets its own.
        skills[name] = {"cls": cls, "sentinel": sentinel}
    return skills


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _temp_data_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP jobs live under the data roots; keep test jobs out of the real ``projects/``."""
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path / "outputs"))


@pytest.fixture
def fake_skills(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install fake skill modules and yield the mapping of name → (class, sentinel)."""
    return _install_fake_skill_modules(monkeypatch)


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    """Create a placeholder video file so existence checks pass."""
    p = tmp_path / "input.mp4"
    p.write_bytes(b"\x00")
    return p


@pytest.fixture
def fake_transcript_path(tmp_path: Path) -> Path:
    """Create a placeholder transcript JSON file."""
    p = tmp_path / "transcript.json"
    p.write_text(json.dumps({"segments": [{"text": "hi", "start": 0.0, "end": 1.0}]}))
    return p


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------


class TestVlogToolListing:
    """Verify all new skills are exposed as MCP tools."""

    @pytest.mark.anyio
    async def test_all_vlog_tools_registered(self) -> None:
        tools = {t.name for t in vlog_skills.list_vlog_skill_tools()}
        expected = {
            "broll",
            "reframe",
            "screen_tutorial",
            "sfx_placement",
            "chapters",
            "shorts_factory",
            "thumbnail",
            "beat_sync",
        }
        assert expected.issubset(tools), f"Missing: {expected - tools}"

    @pytest.mark.anyio
    async def test_vlog_tools_appear_in_server_list(self) -> None:
        result = await _on_list_tools(None, None)
        names = {t.name for t in result.tools}
        for tool in (
            "broll",
            "reframe",
            "screen_tutorial",
            "sfx_placement",
            "chapters",
            "shorts_factory",
            "thumbnail",
            "beat_sync",
        ):
            assert tool in names, f"{tool} missing from server tool list"

    @pytest.mark.anyio
    async def test_each_vlog_tool_has_schema(self) -> None:
        for tool in vlog_skills.list_vlog_skill_tools():
            assert tool.description
            assert tool.input_schema is not None
            assert "properties" in tool.input_schema


# ---------------------------------------------------------------------------
# Per-tool required-fields checks
# ---------------------------------------------------------------------------


class TestVlogToolSchemas:
    """Each tool should declare its required inputs."""

    def _get(self, name: str) -> Any:
        return next(t for t in vlog_skills.list_vlog_skill_tools() if t.name == name)

    def test_broll_requires_video_and_transcript(self) -> None:
        assert set(self._get("broll").input_schema["required"]) == {"video_path", "transcript"}

    def test_reframe_requires_video(self) -> None:
        assert self._get("reframe").input_schema["required"] == ["video_path"]

    def test_screen_tutorial_requires_video(self) -> None:
        assert self._get("screen_tutorial").input_schema["required"] == ["video_path"]

    def test_sfx_placement_requires_video_and_transcript(self) -> None:
        assert set(self._get("sfx_placement").input_schema["required"]) == {
            "video_path",
            "transcript",
        }

    def test_chapters_requires_transcript(self) -> None:
        assert self._get("chapters").input_schema["required"] == ["transcript"]

    def test_shorts_factory_requires_video(self) -> None:
        assert self._get("shorts_factory").input_schema["required"] == ["video_path"]

    def test_thumbnail_requires_video(self) -> None:
        assert self._get("thumbnail").input_schema["required"] == ["video_path"]

    def test_beat_sync_requires_video(self) -> None:
        assert self._get("beat_sync").input_schema["required"] == ["video_path"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestVlogToolErrors:
    """Missing input files should produce a structured error result."""

    @pytest.mark.anyio
    async def test_broll_missing_video(self, fake_skills: dict[str, Any], tmp_path: Path) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="broll",
                arguments={"video_path": str(tmp_path / "missing.mp4"), "transcript": {}},
            ),
        )
        assert result.is_error is True
        assert "Video not found" in result.content[0].text

    @pytest.mark.anyio
    async def test_reframe_missing_video(self, fake_skills: dict[str, Any]) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="reframe", arguments={"video_path": "/no/such.mp4"}),
        )
        assert result.is_error is True

    @pytest.mark.anyio
    async def test_beat_sync_missing_video(self, fake_skills: dict[str, Any]) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="beat_sync", arguments={"video_path": "/no/such.mp4"}),
        )
        assert result.is_error is True

    @pytest.mark.anyio
    async def test_transcript_path_load(
        self, fake_skills: dict[str, Any], fake_video: Path, tmp_path: Path
    ) -> None:
        """A string transcript that is a path should be loaded."""
        fake_skills["broll"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="broll",
                arguments={
                    "video_path": str(fake_video),
                    "transcript": str(tmp_path / "missing.json"),
                },
            ),
        )
        assert result.is_error is True
        assert "Transcript file not found" in result.content[0].text


# ---------------------------------------------------------------------------
# Per-tool dispatch (with mocked skills)
# ---------------------------------------------------------------------------


class TestVlogToolDispatch:
    """Each tool should construct the right skill and call its ``run``."""

    @pytest.mark.anyio
    async def test_broll_dispatches(self, fake_skills: dict[str, Any], fake_video: Path) -> None:
        sentinel = fake_skills["broll"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="broll",
                arguments={"video_path": str(fake_video), "transcript": {"segments": []}},
            ),
        )
        assert result.is_error is False
        # Init recorded: first arg is JobContext.
        assert len(sentinel.init_args) == 1
        assert sentinel.init_args[0].job_id.startswith("mcp-vlog-")
        # ``run`` is async so it returns a coroutine; just check it was awaited.
        sentinel.run.assert_awaited()

    @pytest.mark.anyio
    async def test_reframe_dispatches(self, fake_skills: dict[str, Any], fake_video: Path) -> None:
        sentinel = fake_skills["reframe"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="reframe", arguments={"video_path": str(fake_video)}),
        )
        assert result.is_error is False
        assert len(sentinel.init_args) == 1
        # ReframeSkill is sync, run is called (not awaited).
        sentinel.run.assert_called_once()
        run_kwargs = sentinel.run.call_args.kwargs
        assert run_kwargs["output_name"] == "reframed.mp4"

    @pytest.mark.anyio
    async def test_sync_skills_run_off_the_event_loop_thread(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        """#282: a minutes-long sync render must not block the stdio server."""
        import threading

        loop_thread = threading.get_ident()
        seen: list[int] = []
        fake_skills["reframe"]["sentinel"].run.side_effect = lambda *a, **k: (
            seen.append(threading.get_ident()) or Path("/tmp/out")
        )

        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="reframe", arguments={"video_path": str(fake_video)}),
        )

        assert result.is_error is False
        assert seen and seen[0] != loop_thread

    @pytest.mark.anyio
    async def test_screen_tutorial_dispatches(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        sentinel = fake_skills["screen_tutorial"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="screen_tutorial",
                arguments={"video_path": str(fake_video), "enable_zoom": False},
            ),
        )
        assert result.is_error is False
        # enable_zoom should have been forwarded as a constructor kwarg.
        assert sentinel.init_kwargs == {"enable_zoom": False}
        # ScreenTutorialSkill.run is sync (returns dict[str, Any]).
        sentinel.run.assert_called_once()

    @pytest.mark.anyio
    async def test_sfx_placement_dispatches(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        sentinel = fake_skills["sfx_placement"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="sfx_placement",
                arguments={"video_path": str(fake_video), "transcript": {"segments": []}},
            ),
        )
        assert result.is_error is False
        assert len(sentinel.init_args) == 1
        sentinel.run.assert_awaited()
        from video_editor_asset_ingestion.manager import AssetIngestionManager

        manager = sentinel.init_kwargs["manager"]
        assert isinstance(manager, AssetIngestionManager)
        assert manager.budget is not None

    @pytest.mark.anyio
    async def test_chapters_dispatches(self, fake_skills: dict[str, Any]) -> None:
        sentinel = fake_skills["chapters"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="chapters",
                arguments={"transcript": {"segments": []}, "duration": 60.0},
            ),
        )
        assert result.is_error is False
        # ChapterSkill took a JobContext in init.
        assert len(sentinel.init_args) == 1
        sentinel.run.assert_called_once_with(
            {"segments": []},
            duration=60.0,
        )

    @pytest.mark.anyio
    async def test_shorts_factory_dispatches(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        sentinel = fake_skills["shorts_factory"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="shorts_factory",
                arguments={"video_path": str(fake_video), "count": 3},
            ),
        )
        assert result.is_error is False
        # ShortsFactorySkill is dataclass — no positional args, only config kwarg.
        assert sentinel.init_args == ()
        assert "config" in sentinel.init_kwargs

    @pytest.mark.anyio
    async def test_thumbnail_dispatches(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        sentinel = fake_skills["thumbnail"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="thumbnail",
                arguments={"video_path": str(fake_video), "candidates": 4},
            ),
        )
        assert result.is_error is False
        # ThumbnailSkill is a dataclass, no constructor kwargs.
        assert sentinel.init_args == ()
        assert sentinel.init_kwargs == {}
        # The run() should receive video_path + candidates.
        run_kwargs = sentinel.run.call_args.kwargs
        assert run_kwargs["video_path"] == fake_video
        assert run_kwargs["candidates"] == 4

    @pytest.mark.anyio
    async def test_beat_sync_dispatches(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        sentinel = fake_skills["beat_sync"]["sentinel"]
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="beat_sync", arguments={"video_path": str(fake_video)}),
        )
        assert result.is_error is False
        # BeatSyncSkill is dataclass — only config kwarg.
        assert sentinel.init_args == ()
        assert "config" in sentinel.init_kwargs
        run_kwargs = sentinel.run.call_args.kwargs
        assert run_kwargs["video_path"] == fake_video

    @pytest.mark.anyio
    async def test_unknown_vlog_tool_returns_error(self) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="not_a_skill", arguments={}),
        )
        assert result.is_error is True
        assert "Unknown tool" in result.content[0].text


# ---------------------------------------------------------------------------
# Response format
# ---------------------------------------------------------------------------


class TestVlogToolResponseFormat:
    """Each successful call should return JSON with a status field."""

    @pytest.mark.anyio
    async def test_broll_response_shape(
        self, fake_skills: dict[str, Any], fake_video: Path
    ) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(
                name="broll",
                arguments={"video_path": str(fake_video), "transcript": {"segments": []}},
            ),
        )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "completed"
        assert payload["skill"] == "broll"

    @pytest.mark.anyio
    async def test_chapters_response_shape(self, fake_skills: dict[str, Any]) -> None:
        result = await _on_call_tool(
            None,
            CallToolRequestParams(name="chapters", arguments={"transcript": {"segments": []}}),
        )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["skill"] == "chapters"
        assert "result" in payload
