"""Queue/MCP parity for the short and musical production paths (#303, A11)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from video_editor_cli.payloads import PAYLOAD_SCHEMAS
from video_editor_mcp.tools import pipeline as pipeline_tools
from video_editor_pipelines import short_pipeline
from video_editor_pipelines.handlers import HANDLERS
from video_editor_state.job import JobContext


class _FakeQueue:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def enqueue(self, message: Any) -> Any:
        self.messages.append(message)
        return message


def test_the_production_paths_have_worker_handlers_and_payloads() -> None:
    assert {"short", "musical"} <= set(HANDLERS)
    assert {"short", "musical"} <= set(PAYLOAD_SCHEMAS)


@pytest.mark.asyncio
async def test_run_short_refuses_an_estimate_above_the_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = JobContext(job_id="job-short", work_root=tmp_path / "work", output_root=tmp_path / "out")
    ctx.ensure_dirs()
    pack = tmp_path / "short.yaml"
    pack.write_text("job_id: demo\n", encoding="utf-8")
    queue = _FakeQueue()

    monkeypatch.setattr(pipeline_tools, "make_job_ctx", lambda job_id=None: ctx)
    monkeypatch.setattr(
        pipeline_tools, "contained_input_path", lambda value, label="path": Path(value)
    )
    monkeypatch.setattr(pipeline_tools, "PersistentJobQueue", lambda path: queue)
    monkeypatch.setattr(
        short_pipeline,
        "price_short",
        lambda ctx, pack, **kwargs: short_pipeline.ShortEstimate(
            path=ctx.work_dir / "plan" / "cost_estimate.json",
            total_cost=1.5,
            items=[],
            voice_id="voice",
        ),
    )
    monkeypatch.setattr(short_pipeline, "preflight_short", lambda pack: object())
    monkeypatch.setattr(
        "video_editor_core.content_pack.load_content_pack", lambda path: SimpleNamespace()
    )

    result = await pipeline_tools._handle_run_short({"pack_path": str(pack), "approve_usd": 1.0})

    assert result.is_error is True
    assert "above approve_usd" in result.content[0].text
    assert queue.messages == []


@pytest.mark.asyncio
async def test_run_short_queues_the_job_when_the_approval_covers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = JobContext(job_id="job-short", work_root=tmp_path / "work", output_root=tmp_path / "out")
    ctx.ensure_dirs()
    pack = tmp_path / "short.yaml"
    pack.write_text("job_id: demo\n", encoding="utf-8")
    queue = _FakeQueue()
    saved: list[Path] = []

    monkeypatch.setattr(pipeline_tools, "make_job_ctx", lambda job_id=None: ctx)
    monkeypatch.setattr(
        pipeline_tools, "contained_input_path", lambda value, label="path": Path(value)
    )
    monkeypatch.setattr(pipeline_tools, "PersistentJobQueue", lambda path: queue)
    monkeypatch.setattr(short_pipeline, "preflight_short", lambda pack: object())
    monkeypatch.setattr(
        "video_editor_core.content_pack.load_content_pack", lambda path: SimpleNamespace()
    )
    monkeypatch.setattr(
        short_pipeline,
        "price_short",
        lambda ctx, pack, **kwargs: short_pipeline.ShortEstimate(
            path=ctx.work_dir / "plan" / "cost_estimate.json",
            total_cost=1.5,
            items=[],
            voice_id="voice",
        ),
    )

    def _save(ctx: JobContext, estimate: short_pipeline.ShortEstimate) -> Path:
        saved.append(estimate.path)
        estimate.path.parent.mkdir(parents=True, exist_ok=True)
        estimate.path.write_text("{}", encoding="utf-8")
        return estimate.path

    monkeypatch.setattr(short_pipeline, "save_short_estimate", _save)

    result = await pipeline_tools._handle_run_short({"pack_path": str(pack), "approve_usd": 2.0})

    assert result.is_error is False
    assert saved == [ctx.work_dir / "plan" / "cost_estimate.json"]
    assert len(queue.messages) == 1
    message = queue.messages[0]
    assert message.command == "short"
    assert message.payload["pack"] == str(ctx.work_dir / "short.yaml")
    assert (ctx.work_dir / "short.yaml").exists()
