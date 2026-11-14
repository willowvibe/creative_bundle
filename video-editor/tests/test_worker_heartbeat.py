"""The worker heartbeat must keep beating while a handler runs (#298).

The event loop is the thing that services ``asyncio.sleep`` inside
``_heartbeat``. A handler that blocks the loop starves the beat, so another
worker reclaims a live job after ``queue.stale_seconds``. These tests pin the
dispatch contract: sync handlers run in a worker thread, and the beat continues.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
import video_editor_cli.main as cli_main
from video_editor_cli.cli_support import _heartbeat
from video_editor_state.queue import JobMessage


class RecordingQueue:
    """Minimal queue that records beats and terminal transitions."""

    def __init__(self) -> None:
        self.beats = 0
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    async def heartbeat(self, message: JobMessage) -> None:
        self.beats += 1

    async def complete(self, message: JobMessage) -> None:
        self.completed.append(message.job_id)

    async def fail(self, message: JobMessage, reason: str) -> None:
        self.failed.append((message.job_id, reason))


@pytest.mark.asyncio
async def test_a_sync_handler_does_not_starve_the_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 0.65 s blocking handler must not stop the 0.1 s beat."""
    loop_thread = threading.get_ident()
    handler_threads: list[int] = []

    def sleepy(ctx: object, payload: dict[str, object]) -> None:
        handler_threads.append(threading.get_ident())
        time.sleep(0.65)

    monkeypatch.setitem(cli_main._HANDLERS, "sleepy", sleepy)
    queue = RecordingQueue()
    message = JobMessage(job_id="job-sleepy", tenant_id="t1", command="sleepy")
    ctx = SimpleNamespace(job_id="job-sleepy", tenant_id="t1")

    async with _heartbeat(queue, message, 0.3):
        status = await cli_main._dispatch_message(queue, message, ctx=ctx)  # type: ignore[arg-type]

    assert status == "completed"
    assert queue.completed == ["job-sleepy"]
    assert queue.failed == []
    # Three beats fit in 0.65 s at a 0.1 s interval. A blocked loop yields 0–1.
    assert queue.beats >= 3, f"heartbeat starved: {queue.beats} beats"
    assert handler_threads and handler_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_async_handlers_are_still_awaited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async handlers keep their own concurrency; nothing regresses."""
    seen: list[str] = []

    async def coroutine_handler(ctx: object, payload: dict[str, object]) -> None:
        await asyncio.sleep(0)
        seen.append(str(payload["value"]))

    monkeypatch.setitem(cli_main._HANDLERS, "coroutine", coroutine_handler)
    queue = RecordingQueue()
    message = JobMessage(
        job_id="job-async", tenant_id="t1", command="coroutine", payload={"value": "ok"}
    )

    status = await cli_main._dispatch_message(
        queue,
        message,
        ctx=SimpleNamespace(job_id="job-async", tenant_id="t1"),  # type: ignore[arg-type]
    )

    assert status == "completed"
    assert seen == ["ok"]
