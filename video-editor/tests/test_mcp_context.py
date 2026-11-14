"""MCP job roots and spending limits (#276, #277)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from video_editor_config import McpConfig
from video_editor_mcp import context
from video_editor_mcp.context import (
    McpRequestError,
    check_call_cap,
    job_budget,
    make_job_ctx,
)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    work, out = tmp_path / "projects", tmp_path / "outputs"
    monkeypatch.setenv("VE_WORK_ROOT", str(work))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(out))
    return work, out


def test_jobs_live_under_the_mcp_subdirectory(roots: tuple[Path, Path]) -> None:
    work, out = roots

    ctx = make_job_ctx()

    assert ctx.work_dir.parent == (work / "mcp").resolve()
    assert ctx.output_dir.parent == (out / "mcp").resolve()
    assert ctx.work_dir.is_dir()


@pytest.mark.parametrize("job_id", ["../escape", "a/b", ".."])
def test_traversal_job_ids_are_refused(roots: tuple[Path, Path], job_id: str) -> None:
    with pytest.raises(McpRequestError):
        make_job_ctx(job_id)


def test_roots_outside_the_data_roots_are_refused(roots: tuple[Path, Path], tmp_path: Path) -> None:
    with pytest.raises(McpRequestError, match="work_root"):
        make_job_ctx("job-1", work_root=str(tmp_path / "elsewhere"))
    with pytest.raises(McpRequestError, match="output_root"):
        make_job_ctx("job-1", output_root="/etc")


def test_a_cli_job_inside_the_data_roots_is_reachable(roots: tuple[Path, Path]) -> None:
    work, out = roots

    ctx = make_job_ctx("cli-job", work_root=str(work), output_root=str(out))

    assert ctx.work_dir == work.resolve() / "cli-job"


def test_the_budget_defaults_to_the_configured_cap() -> None:
    cfg = McpConfig(default_budget_usd=2.0)

    assert job_budget(config=cfg).limit == 2.0
    assert job_budget(0.25, config=cfg).limit == 0.25
    # A caller cannot raise the cap.
    assert job_budget(50, config=cfg).limit == 2.0


@pytest.mark.parametrize("bad", ["lots", -1])
def test_a_bad_budget_is_refused(bad: Any) -> None:
    with pytest.raises(McpRequestError):
        job_budget(bad, config=McpConfig())


def test_calls_above_the_cap_need_approval() -> None:
    cfg = McpConfig(per_call_cap_usd=0.5)

    check_call_cap(None, None, config=cfg)  # unpriced: the job budget bounds it
    check_call_cap(0.4, None, config=cfg)
    with pytest.raises(McpRequestError, match="approve_usd"):
        check_call_cap(0.9, None, config=cfg)
    with pytest.raises(McpRequestError):
        check_call_cap(0.9, 0.5, config=cfg)
    check_call_cap(0.9, 0.9, config=cfg)


def test_input_media_outside_the_data_roots_is_refused(
    roots: tuple[Path, Path], tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")

    with pytest.raises(McpRequestError, match="data roots"):
        context.contained_input_path(outside, "video_path")


def test_input_media_inside_the_data_roots_is_accepted(
    roots: tuple[Path, Path],
) -> None:
    work, _ = roots
    inside = work / "input.mp4"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"x")

    assert context.contained_input_path(inside, "video_path") == inside.resolve()


@pytest.mark.asyncio
async def test_generate_image_over_the_cap_is_refused_before_spending(
    roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_mcp.tools import assets

    monkeypatch.setattr(assets, "mcp_config", lambda: McpConfig(per_call_cap_usd=0.01))
    monkeypatch.setattr(assets, "estimate_request_usd", lambda manager, request: 0.2)

    async def must_not_resolve(self: Any, requests: Any) -> Any:
        raise AssertionError("generation ran despite the cap")

    monkeypatch.setattr(assets.AssetIngestionManager, "resolve", must_not_resolve)

    result = await assets.call_asset_tool("generate_image", {"prompt": "a red circle"})

    assert result.is_error
    assert "approve_usd" in json.loads(result.content[0].text)["error"]


@pytest.mark.asyncio
async def test_generate_image_runs_under_a_budget(
    roots: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_asset_ingestion.models import AssetResult, AssetType
    from video_editor_mcp.tools import assets

    seen: dict[str, Any] = {}
    monkeypatch.setattr(assets, "mcp_config", lambda: McpConfig(default_budget_usd=0.75))
    monkeypatch.setattr(assets, "estimate_request_usd", lambda manager, request: 0.02)

    async def fake_resolve(self: Any, requests: Any) -> list[AssetResult]:
        seen["limit"] = self.budget.limit
        return [
            AssetResult(
                asset_id=requests[0].asset_id,
                asset_type=AssetType.IMAGE,
                local_path="/x.png",
                scene_id="mcp",
                metadata={"provider": "poyo_ai"},
            )
        ]

    monkeypatch.setattr(assets.AssetIngestionManager, "resolve", fake_resolve)

    result = await assets.call_asset_tool("generate_image", {"prompt": "a red circle"})

    assert not result.is_error, result.content[0].text
    body = json.loads(result.content[0].text)
    assert body["estimate_usd"] == 0.02
    assert seen["limit"] == 0.75


@pytest.mark.asyncio
async def test_run_skill_refuses_a_traversal_job_id(roots: tuple[Path, Path]) -> None:
    from video_editor_mcp.tools.pipeline import call_pipeline_tool

    result = await call_pipeline_tool("run_skill", {"skill": "export", "job_id": "../../x"})

    assert result.is_error
    assert "job_id" in json.loads(result.content[0].text)["error"]


def test_context_module_reads_the_mcp_section(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mcp:\n  default_budget_usd: 3.5\n  per_call_cap_usd: 0.1\n")

    cfg = context.mcp_config(config)

    assert cfg.default_budget_usd == 3.5
    assert cfg.per_call_cap_usd == 0.1
