"""A7: repeated enqueues return the existing job instead of running twice."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_state.queue import InMemoryJobQueue, JobMessage, PersistentJobQueue


@pytest.mark.asyncio
async def test_in_memory_enqueue_is_idempotent() -> None:
    queue = InMemoryJobQueue()
    first = await queue.enqueue(
        JobMessage(job_id="a", tenant_id="t", command="run", payload={"x": 1}, idempotency_key="k1")
    )
    second = await queue.enqueue(
        JobMessage(job_id="b", tenant_id="t", command="run", payload={"x": 1}, idempotency_key="k1")
    )
    other = await queue.enqueue(
        JobMessage(job_id="c", tenant_id="t", command="run", payload={"x": 2}, idempotency_key="k2")
    )

    assert first.job_id == second.job_id == "a"
    assert other.job_id == "c"
    assert await queue.size() == 2


@pytest.mark.asyncio
async def test_persistent_enqueue_is_idempotent(tmp_path: Path) -> None:
    queue = PersistentJobQueue(tmp_path / "queue.db")
    first = await queue.enqueue(
        JobMessage(job_id="a", tenant_id="t", command="run", payload={"x": 1}, idempotency_key="k1")
    )
    second = await queue.enqueue(
        JobMessage(job_id="b", tenant_id="t", command="run", payload={"x": 1}, idempotency_key="k1")
    )

    assert first.job_id == second.job_id == "a"
    assert await queue.size() == 1
    status = await queue.status("a")
    assert status is not None and status["status"] == "pending"
