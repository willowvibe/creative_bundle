"""A7: MCP run_pipeline queues a job and job_status can read it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_mcp.tools.pipeline import call_pipeline_tool


@pytest.fixture(autouse=True)
def _roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setenv("VE_QUEUE_DB", str(tmp_path / "queue.db"))


@pytest.mark.asyncio
async def test_run_pipeline_queues_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"not a real video")

    first = await call_pipeline_tool("run_pipeline", {"source": str(source)})
    second = await call_pipeline_tool("run_pipeline", {"source": str(source)})

    assert not first.is_error, first.content[0].text
    first_body = json.loads(first.content[0].text)
    second_body = json.loads(second.content[0].text)
    assert first_body["status"] == "queued"
    assert first_body["job_id"] == second_body["job_id"]


@pytest.mark.asyncio
async def test_job_status_reads_the_queue(tmp_path: Path) -> None:
    source = tmp_path / "input.mp4"
    source.write_bytes(b"not a real video")
    queued = await call_pipeline_tool("run_pipeline", {"source": str(source)})
    job_id = json.loads(queued.content[0].text)["job_id"]

    status = await call_pipeline_tool("job_status", {"job_id": job_id})

    assert not status.is_error, status.content[0].text
    body = json.loads(status.content[0].text)
    assert body["queue"]["id"] == job_id
    assert body["queue"]["status"] == "pending"
    assert body["final_video"] is None
