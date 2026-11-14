"""Tests for the multi-tenant in-memory job queue."""

from __future__ import annotations

import sqlite3
from collections import deque
from pathlib import Path

import pytest
from video_editor_state.errors import LeaseLostError
from video_editor_state.queue import InMemoryJobQueue, JobMessage


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.mark.asyncio
async def test_enqueue_and_dequeue(queue: InMemoryJobQueue) -> None:
    msg = JobMessage(job_id="job-1", tenant_id="tenant-a", command="render")
    await queue.enqueue(msg)
    assert await queue.size() == 1

    out = await queue.dequeue()
    assert out is not None
    assert out.job_id == "job-1"
    assert out.tenant_id == "tenant-a"
    assert await queue.size() == 0


@pytest.mark.asyncio
async def test_tenant_isolation(queue: InMemoryJobQueue) -> None:
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
    await queue.enqueue(JobMessage(job_id="j2", tenant_id="t2", command="render"))

    out = await queue.dequeue("t1")
    assert out is not None
    assert out.tenant_id == "t1"
    assert await queue.size() == 1


@pytest.mark.asyncio
async def test_complete_and_fail(queue: InMemoryJobQueue) -> None:
    msg = JobMessage(job_id="job-1", tenant_id="default", command="export")
    await queue.enqueue(msg)
    dequeued = await queue.dequeue()
    assert dequeued is not None

    await queue.complete(dequeued)
    assert await queue.size() == 0

    await queue.enqueue(JobMessage(job_id="job-2", tenant_id="default", command="export"))
    dequeued = await queue.dequeue()
    assert dequeued is not None
    await queue.fail(dequeued, "network error")
    assert await queue.size() == 0


@pytest.mark.asyncio
async def test_round_robin_fairness_with_uneven_loads(queue: InMemoryJobQueue) -> None:
    """Bug #14 regression: a high-volume tenant must not starve low-volume
    ones. With 10 jobs from tenant-a and 1 job from tenant-b, the first 2
    dequeues should give us one from each (round-robin).
    """
    # 10 jobs from tenant-a, 1 from tenant-b.
    for i in range(10):
        await queue.enqueue(JobMessage(job_id=f"a-{i}", tenant_id="a", command="render"))
    await queue.enqueue(JobMessage(job_id="b-0", tenant_id="b", command="render"))

    # First dequeue: should pick tenant-a (it was enqueued first).
    first = await queue.dequeue()
    assert first is not None
    # Second dequeue: must be tenant-b — round-robin should not let a
    # second tenant-a job block tenant-b's only job.
    second = await queue.dequeue()
    assert second is not None
    assert second.tenant_id == "b", (
        f"tenant-b starved: first={first.tenant_id}, second={second.tenant_id}"
    )


@pytest.mark.asyncio
async def test_round_robin_continues_after_drain(queue: InMemoryJobQueue) -> None:
    """Bug #14 regression: round-robin should keep working when one tenant's
    bucket drains mid-rotation. The remaining tenant's jobs should still
    be served.
    """
    await queue.enqueue(JobMessage(job_id="a-1", tenant_id="a", command="render"))
    await queue.enqueue(JobMessage(job_id="b-1", tenant_id="b", command="render"))
    await queue.enqueue(JobMessage(job_id="b-2", tenant_id="b", command="render"))

    # First dequeue: a-1 (was enqueued first).
    first = await queue.dequeue()
    assert first is not None and first.tenant_id == "a"
    # Second: should be b-1, not None (a has nothing left).
    second = await queue.dequeue()
    assert second is not None
    assert second.tenant_id == "b"
    # Third: b-2.
    third = await queue.dequeue()
    assert third is not None
    assert third.tenant_id == "b"
    # Fourth: empty.
    fourth = await queue.dequeue()
    assert fourth is None


def test_global_queue_singleton() -> None:
    from video_editor_cli.main import _get_queue

    q1 = _get_queue()
    q2 = _get_queue()
    assert q1 is q2


def test_enqueue_rejects_malformed_json_with_clean_error() -> None:
    """Regression: ``ve enqueue --payload '{bad json'`` used to surface a raw
    Python JSONDecodeError traceback. It should now exit cleanly with code 1
    and a single readable error line."""
    from typer.testing import CliRunner
    from video_editor_cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "enqueue",
            "render",
            "-j",
            "test-job",
            "--payload",
            "{bad json",
        ],
    )
    assert result.exit_code == 1
    # No raw traceback in combined output.
    assert "Traceback" not in result.output
    assert "not valid JSON" in result.output


def test_enqueue_rejects_non_object_payload() -> None:
    """Regression: --payload that decodes to a list/scalar should be rejected
    with a clear error, not crash later in the queue layer."""
    from typer.testing import CliRunner
    from video_editor_cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "enqueue",
            "render",
            "-j",
            "test-job",
            "--payload",
            "[1, 2, 3]",
        ],
    )
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_enqueue_accepts_valid_json() -> None:
    """Sanity: well-formed JSON object payload enqueues cleanly."""
    from typer.testing import CliRunner
    from video_editor_cli.main import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "enqueue",
            "render",
            "-j",
            "valid-job",
            "--payload",
            '{"key": "value"}',
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Enqueued" in result.output


def test_enqueue_warns_about_in_memory_queue() -> None:
    """Regression: ``ve enqueue`` should warn the user (once per process)
    that the queue is in-memory and won't survive across processes."""
    import video_editor_cli.main as cli_main
    from typer.testing import CliRunner
    from video_editor_cli.main import app

    # Reset the warn-once flag so the warning fires in this test.
    cli_main._in_memory_warned = False
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "enqueue",
            "render",
            "-j",
            "warn-job",
            "--payload",
            "{}",
        ],
    )
    assert result.exit_code == 0
    assert "in-memory" in result.output


class TestPublicCountMethods:
    """Bug #28 regression: the worker must track processed / failed
    counts via public ``JobQueue`` methods (``pending_count``,
    ``completed_count``, ``failed_count``) rather than reaching into
    private ``_completed`` / ``_failed`` lists. A persistent backend
    (Redis/Postgres) can implement the same interface without
    breaking the CLI.
    """

    @pytest.mark.asyncio
    async def test_pending_count_matches_size(self, queue: InMemoryJobQueue) -> None:
        await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await queue.enqueue(JobMessage(job_id="j2", tenant_id="t1", command="render"))
        await queue.enqueue(JobMessage(job_id="j3", tenant_id="t2", command="render"))
        assert await queue.pending_count() == 3
        assert await queue.pending_count() == await queue.size()

    @pytest.mark.asyncio
    async def test_pending_count_filters_by_tenant(self, queue: InMemoryJobQueue) -> None:
        await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await queue.enqueue(JobMessage(job_id="j2", tenant_id="t2", command="render"))
        await queue.enqueue(JobMessage(job_id="j3", tenant_id="t2", command="render"))
        assert await queue.pending_count("t1") == 1
        assert await queue.pending_count("t2") == 2
        assert await queue.pending_count("missing") == 0

    @pytest.mark.asyncio
    async def test_completed_count_starts_at_zero(self, queue: InMemoryJobQueue) -> None:
        assert await queue.completed_count() == 0

    @pytest.mark.asyncio
    async def test_completed_count_tracks_complete_calls(self, queue: InMemoryJobQueue) -> None:
        msg1 = JobMessage(job_id="j1", tenant_id="t1", command="render")
        msg2 = JobMessage(job_id="j2", tenant_id="t1", command="render")
        await queue.enqueue(msg1)
        await queue.enqueue(msg2)
        d1 = await queue.dequeue()
        d2 = await queue.dequeue()
        assert d1 is not None and d2 is not None
        await queue.complete(d1)
        assert await queue.completed_count() == 1
        await queue.complete(d2)
        assert await queue.completed_count() == 2

    @pytest.mark.asyncio
    async def test_completed_count_filters_by_tenant(self, queue: InMemoryJobQueue) -> None:
        await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await queue.enqueue(JobMessage(job_id="j2", tenant_id="t2", command="render"))
        d1 = await queue.dequeue("t1")
        d2 = await queue.dequeue("t2")
        assert d1 is not None and d2 is not None
        await queue.complete(d1)
        await queue.complete(d2)
        assert await queue.completed_count("t1") == 1
        assert await queue.completed_count("t2") == 1
        assert await queue.completed_count() == 2

    @pytest.mark.asyncio
    async def test_failed_count_tracks_fail_calls(self, queue: InMemoryJobQueue) -> None:
        # max_retries=0 so a single fail immediately dead-letters.
        await queue.enqueue(
            JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=0)
        )
        await queue.enqueue(
            JobMessage(job_id="j2", tenant_id="t2", command="render", max_retries=0)
        )
        d1 = await queue.dequeue("t1")
        d2 = await queue.dequeue("t2")
        assert d1 is not None and d2 is not None
        await queue.fail(d1, "boom")
        assert await queue.failed_count() == 1
        await queue.fail(d2, "also boom")
        assert await queue.failed_count() == 2

    @pytest.mark.asyncio
    async def test_failed_count_filters_by_tenant(self, queue: InMemoryJobQueue) -> None:
        await queue.enqueue(
            JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=0)
        )
        await queue.enqueue(
            JobMessage(job_id="j2", tenant_id="t2", command="render", max_retries=0)
        )
        d1 = await queue.dequeue("t1")
        d2 = await queue.dequeue("t2")
        assert d1 is not None and d2 is not None
        await queue.fail(d1, "boom-t1")
        await queue.fail(d2, "boom-t2")
        assert await queue.failed_count("t1") == 1
        assert await queue.failed_count("t2") == 1
        assert await queue.failed_count() == 2

    @pytest.mark.asyncio
    async def test_completed_and_failed_counts_are_independent(
        self, queue: InMemoryJobQueue
    ) -> None:
        """A complete() call must not increment failed_count and vice versa."""
        await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await queue.enqueue(
            JobMessage(job_id="j2", tenant_id="t1", command="render", max_retries=0)
        )
        d1 = await queue.dequeue()
        d2 = await queue.dequeue()
        assert d1 is not None and d2 is not None
        await queue.complete(d1)
        await queue.fail(d2, "nope")
        assert await queue.completed_count() == 1
        assert await queue.failed_count() == 1
        assert await queue.pending_count() == 0


class TestRetryAndDeadLetter:
    """Bug #71 regression: retry with exponential backoff + DLQ after
    ``max_retries`` is exhausted."""

    @pytest.mark.asyncio
    async def test_fail_retries_then_dead_letters(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=2)
        await queue.enqueue(msg)

        # First attempt fails → retry 1 (delayed).
        first = await queue.dequeue()
        assert first is not None
        await queue.fail(first, "transient")
        assert first.retries == 1
        assert await queue.size() == 0
        assert await queue.failed_count() == 0
        assert await queue.dead_letter_count() == 0

        # Backoff is short enough that moving the clock forward lets it retry.
        import datetime as dt
        from datetime import UTC

        first.available_at = (dt.datetime.now(UTC) - dt.timedelta(seconds=1)).timestamp()
        second = await queue.dequeue()
        assert second is not None
        assert second.job_id == "j1"
        assert second.retries == 1

        # Second attempt fails → retry 2.
        await queue.fail(second, "transient again")
        second.available_at = (dt.datetime.now(UTC) - dt.timedelta(seconds=1)).timestamp()
        third = await queue.dequeue()
        assert third is not None
        assert third.retries == 2

        # Third attempt fails → dead letter.
        await queue.fail(third, "permanent")
        assert await queue.size() == 0
        assert await queue.failed_count() == 1
        assert await queue.dead_letter_count() == 1

    @pytest.mark.asyncio
    async def test_retry_delay_increases(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=3)
        await queue.enqueue(msg)
        first = await queue.dequeue()
        assert first is not None
        before = first.available_at or 0
        await queue.fail(first, "boom")
        assert first.retries == 1
        # First retry delay is 1s (2^(1-1)).
        assert (first.available_at or 0) > before + 0.5


class TestInFlightTracking:
    """Bug #60 regression: dequeued messages must stay in an in-flight set
    until completed or failed, and stale messages must be requeueable.
    """

    @pytest.mark.asyncio
    async def test_dequeue_adds_to_in_flight(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        assert dequeued.job_id in queue._in_flight

    @pytest.mark.asyncio
    async def test_complete_removes_from_in_flight(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.complete(dequeued)
        assert dequeued.job_id not in queue._in_flight

    @pytest.mark.asyncio
    async def test_fail_removes_from_in_flight(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.fail(dequeued, "boom")
        assert dequeued.job_id not in queue._in_flight

    @pytest.mark.asyncio
    async def test_requeue_stale_uses_dequeue_time_not_enqueue_time(
        self,
        queue: InMemoryJobQueue,
    ) -> None:
        """C1 regression: ``requeue_stale`` must measure staleness from the
        **dequeue** time, not the enqueue time (``created_at``). A message
        that sat pending for 10 minutes under load and was dequeued 1
        second ago is NOT stale — requeueing it would cause a second worker
        to process the same job (duplicate FAL submissions, double billing).
        """
        import datetime as dt
        from datetime import UTC

        # A message enqueued 600s ago (old created_at), but just dequeued.
        old_time = (dt.datetime.now(UTC) - dt.timedelta(seconds=600)).isoformat()
        msg = JobMessage(
            job_id="not-stale",
            tenant_id="t1",
            command="render",
            created_at=old_time,
        )
        queue._pending.setdefault("t1", deque()).append(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        assert dequeued.job_id == "not-stale"
        # The dequeue time was just recorded.
        assert dequeued.job_id in queue._dequeue_times

        # Even though created_at is 600s old, the message was dequeued
        # ~now, so requeue_stale(300) must NOT touch it.
        stale = await queue.requeue_stale(timeout_seconds=300.0)
        assert stale == []
        assert await queue.size() == 0
        assert "not-stale" in queue._in_flight  # still being processed

    @pytest.mark.asyncio
    async def test_requeue_stale_returns_genuinely_stale_messages(
        self,
        queue: InMemoryJobQueue,
    ) -> None:
        """C1 positive case: a message whose **dequeue** time is older
        than the timeout (worker crashed mid-process) IS requeued."""
        msg = JobMessage(job_id="truly-stale", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None

        # Simulate a worker that dequeued 600s ago and never completed.
        import datetime as dt
        from datetime import UTC

        queue._dequeue_times[msg.job_id] = (
            dt.datetime.now(UTC) - dt.timedelta(seconds=600)
        ).timestamp()

        stale = await queue.requeue_stale(timeout_seconds=300.0)
        assert len(stale) == 1
        assert stale[0].job_id == "truly-stale"
        assert await queue.size() == 1
        assert "truly-stale" not in queue._in_flight
        assert "truly-stale" not in queue._dequeue_times


class TestTerminalIdempotency:
    """C8 regression: ``complete`` and ``fail`` must be idempotent. A
    message that already reached a terminal state must not be moved back
    into ``_delayed`` / ``_failed``, which would cause duplicate processing
    after backoff expires."""

    @pytest.mark.asyncio
    async def test_complete_is_idempotent(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.complete(dequeued)
        assert await queue.completed_count() == 1

        # Second complete() must be ignored.
        await queue.complete(dequeued)
        assert await queue.completed_count() == 1
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_fail_after_complete_is_ignored(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render")
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.complete(dequeued)

        # A racing fail() must not delay the already-completed job.
        await queue.fail(dequeued, "late error")
        assert await queue.completed_count() == 1
        assert await queue.failed_count() == 0
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_complete_after_exhausted_fail_is_ignored(self, queue: InMemoryJobQueue) -> None:
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=0)
        await queue.enqueue(msg)
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.fail(dequeued, "permanent")
        assert await queue.failed_count() == 1

        # A late complete() must not add the same job to _completed.
        await queue.complete(dequeued)
        assert await queue.failed_count() == 1
        assert await queue.completed_count() == 0
        assert await queue.size() == 0

    @pytest.mark.asyncio
    async def test_retry_allowed_then_terminal(self, queue: InMemoryJobQueue) -> None:
        """A retried (delayed) message is not terminal; completing it after
        retry succeeds is allowed."""
        msg = JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=1)
        await queue.enqueue(msg)
        first = await queue.dequeue()
        assert first is not None
        await queue.fail(first, "transient")
        assert first.job_id in queue._delayed

        # A retried (delayed) message is not terminal. Completing the *old*
        # claim is refused now that each dequeue owns a lease (#298)...
        with pytest.raises(LeaseLostError):
            await queue.complete(first)

        # ...and completing the next claim succeeds.
        first.available_at = 0.0
        second = await queue.dequeue()
        assert second is not None
        await queue.complete(second)
        assert await queue.completed_count() == 1
        assert await queue.failed_count() == 0
        assert second.job_id in queue._terminal


# ---------------------------------------------------------------------------
#  Lease ownership (#298)
# ---------------------------------------------------------------------------


class TestLeaseOwnership:
    @pytest.mark.asyncio
    async def test_a_reclaimed_job_cannot_be_completed_by_the_old_worker(
        self, queue: InMemoryJobQueue
    ) -> None:
        await queue.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        first = await queue.dequeue()
        assert first is not None and first.lease_id

        queue._dequeue_times[first.job_id] -= 3600
        assert [m.job_id for m in await queue.requeue_stale(1.0)] == ["j1"]
        second = await queue.dequeue()
        assert second is not None
        assert second.lease_id != first.lease_id

        with pytest.raises(LeaseLostError):
            await queue.complete(first)
        with pytest.raises(LeaseLostError):
            await queue.heartbeat(first)
        with pytest.raises(LeaseLostError):
            await queue.fail(first, "late")

        await queue.complete(second)
        assert await queue.completed_count() == 1
        assert await queue.failed_count() == 0

    @pytest.mark.asyncio
    async def test_release_returns_the_claim_without_consuming_a_retry(
        self, queue: InMemoryJobQueue
    ) -> None:
        await queue.enqueue(
            JobMessage(job_id="j1", tenant_id="t1", command="render", max_retries=3)
        )
        first = await queue.dequeue()
        assert first is not None
        await queue.release(first)

        assert first.job_id not in queue._in_flight
        assert first.job_id in queue._delayed
        assert first.retries == 0
        first.available_at = 0.0
        second = await queue.dequeue()
        assert second is not None and second.lease_id != first.lease_id
        await queue.complete(second)
        assert await queue.completed_count() == 1


# ---------------------------------------------------------------------------
#  PersistentJobQueue tests
# ---------------------------------------------------------------------------


class TestPersistentJobQueue:
    """Tests for the SQLite-backed PersistentJobQueue."""

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_complete(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        msg = JobMessage(
            job_id="job-1",
            tenant_id="default",
            command="run",
            payload={"source": "test.mp4"},
        )
        await q.enqueue(msg)
        assert await q.size() == 1

        dequeued = await q.dequeue()
        assert dequeued is not None
        assert dequeued.job_id == "job-1"
        assert dequeued.command == "run"
        assert dequeued.payload == {"source": "test.mp4"}

        await q.complete(dequeued)
        assert await q.completed_count() == 1
        assert await q.size() == 0

    @pytest.mark.asyncio
    async def test_survives_reopen(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q1 = PersistentJobQueue(db)
        await q1.enqueue(JobMessage(job_id="job-1", tenant_id="default", command="run", payload={}))
        assert await q1.size() == 1

        # Reopen — the pending job must still be there.
        q2 = PersistentJobQueue(db)
        assert await q2.size() == 1

        dequeued = await q2.dequeue()
        assert dequeued is not None
        assert dequeued.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_fail_and_retry(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        msg = JobMessage(
            job_id="job-1",
            tenant_id="default",
            command="run",
            payload={},
            max_retries=2,
        )
        await q.enqueue(msg)

        # First attempt fails → retry 1 (back to pending with backoff).
        d1 = await q.dequeue()
        assert d1 is not None
        await q.fail(d1, "transient error")
        # Job is pending but has retry_after set — size() filters these out.
        assert await q.size() == 0  # not immediately available
        assert await q.failed_count() == 0
        assert await q.dead_letter_count() == 0

        # Manually clear retry_after to simulate backoff expiry.
        with q._lock:
            q._get_conn().execute("UPDATE jobs SET retry_after = NULL WHERE id = ?", ("job-1",))
            q._get_conn().commit()

        # Second attempt.
        d2 = await q.dequeue()
        assert d2 is not None
        assert d2.job_id == "job-1"
        assert d2.retries == 1

        # Second attempt fails → retry 2.
        await q.fail(d2, "transient again")
        assert await q.size() == 0  # backoff active

        # Clear retry_after again.
        with q._lock:
            q._get_conn().execute("UPDATE jobs SET retry_after = NULL WHERE id = ?", ("job-1",))
            q._get_conn().commit()

        # Third attempt.
        d3 = await q.dequeue()
        assert d3 is not None
        assert d3.retries == 2

        # Third attempt fails → dead letter (max_retries=2 exhausted).
        await q.fail(d3, "permanent")
        assert await q.size() == 0
        assert await q.failed_count() == 1
        assert await q.dead_letter_count() == 1

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await q.enqueue(JobMessage(job_id="j2", tenant_id="t2", command="render"))

        # Dequeue scoped to t1.
        out = await q.dequeue("t1")
        assert out is not None
        assert out.tenant_id == "t1"
        assert await q.size() == 1  # j2 still pending

    @pytest.mark.asyncio
    async def test_count_methods(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)

        # Enqueue 3 jobs across 2 tenants.
        await q.enqueue(JobMessage(job_id="j1", tenant_id="t1", command="render"))
        await q.enqueue(JobMessage(job_id="j2", tenant_id="t1", command="render"))
        await q.enqueue(JobMessage(job_id="j3", tenant_id="t2", command="render"))

        assert await q.pending_count() == 3
        assert await q.pending_count("t1") == 2
        assert await q.pending_count("t2") == 1
        assert await q.pending_count("missing") == 0

        # Complete j1.
        d1 = await q.dequeue("t1")
        assert d1 is not None
        await q.complete(d1)
        assert await q.completed_count() == 1
        assert await q.completed_count("t1") == 1
        assert await q.completed_count("t2") == 0

        # Fail j3 (max_retries=0 → immediate dead letter).
        d3 = await q.dequeue("t2")
        assert d3 is not None
        # Override max_retries to 0 so fail() dead-letters immediately.
        with q._lock, sqlite3.connect(str(q._db_path)) as conn:
            conn.execute("UPDATE jobs SET max_retries = 0 WHERE id = ?", (d3.job_id,))
            conn.commit()
        await q.fail(d3, "boom")
        assert await q.failed_count() == 1
        assert await q.failed_count("t2") == 1
        assert await q.failed_count("t1") == 0

    @pytest.mark.asyncio
    async def test_requeue_stale(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="job-1", tenant_id="default", command="run", payload={}))
        d = await q.dequeue()
        assert d is not None

        # Manually backdate the started_at to simulate a stale job.
        import datetime as dt
        from datetime import UTC

        stale_time = (dt.datetime.now(UTC) - dt.timedelta(seconds=600)).isoformat()
        with q._lock, sqlite3.connect(str(q._db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET started_at = ? WHERE id = ?",
                (stale_time, "job-1"),
            )
            conn.commit()

        stale = await q.requeue_stale(timeout_seconds=300.0)
        assert len(stale) == 1
        assert stale[0].job_id == "job-1"
        assert await q.size() == 1

    @pytest.mark.asyncio
    async def test_dequeue_none_when_empty(self, tmp_path: Path) -> None:
        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        assert await q.dequeue() is None
        assert await q.size() == 0

    @pytest.mark.asyncio
    async def test_fail_unknown_job_is_noop(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        # Should not raise.
        await q.fail(
            JobMessage(job_id="nonexistent", tenant_id="default", command="x"),
            "reason",
        )
        assert await q.failed_count() == 0

    @pytest.mark.asyncio
    async def test_complete_and_fail_independent(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="j1", tenant_id="default", command="render"))
        await q.enqueue(
            JobMessage(
                job_id="j2",
                tenant_id="default",
                command="render",
                max_retries=0,
            )
        )

        d1 = await q.dequeue()
        d2 = await q.dequeue()
        assert d1 is not None and d2 is not None
        await q.complete(d1)
        await q.fail(d2, "nope")

        assert await q.completed_count() == 1
        assert await q.failed_count() == 1
        assert await q.pending_count() == 0


# ---------------------------------------------------------------------------
#  PersistentJobQueue — new features (WAL, round-robin, backoff)
# ---------------------------------------------------------------------------


class TestPersistentJobQueueWAL:
    """Tests for WAL mode and persistent connection."""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        with q._lock:
            row = q._get_conn().execute("PRAGMA journal_mode").fetchone()
        assert row is not None
        assert row[0].upper() == "WAL"

    @pytest.mark.asyncio
    async def test_persistent_connection_reused(self, tmp_path: Path) -> None:
        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        conn1 = q._get_conn()
        conn2 = q._get_conn()
        assert conn1 is conn2  # same connection object

    @pytest.mark.asyncio
    async def test_busy_timeout_set(self, tmp_path: Path) -> None:
        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        with q._lock:
            row = q._get_conn().execute("PRAGMA busy_timeout").fetchone()
        assert row is not None
        assert int(row[0]) == 30000


class TestPersistentJobQueueBackoff:
    """Tests for exponential retry backoff."""

    @pytest.mark.asyncio
    async def test_retry_sets_retry_after(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="j1", tenant_id="default", command="run", max_retries=3))
        d = await q.dequeue()
        assert d is not None
        await q.fail(d, "error")

        # Job should be pending but with retry_after set.
        with q._lock:
            row = (
                q._get_conn()
                .execute("SELECT status, retry_after FROM jobs WHERE id = ?", ("j1",))
                .fetchone()
            )
        assert row is not None
        assert row["status"] == "pending"
        assert row["retry_after"] is not None  # backoff timestamp set

    @pytest.mark.asyncio
    async def test_backoff_delays_dequeue(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="j1", tenant_id="default", command="run", max_retries=3))
        d = await q.dequeue()
        assert d is not None
        await q.fail(d, "error")

        # Job should NOT be immediately available for dequeue.
        result = await q.dequeue()
        assert result is None  # retry_after hasn't expired yet

    @pytest.mark.asyncio
    async def test_backoff_increases_with_retries(self, tmp_path: Path) -> None:
        from datetime import UTC, datetime

        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)
        await q.enqueue(JobMessage(job_id="j1", tenant_id="default", command="run", max_retries=5))

        # First fail: backoff = 1s.
        d1 = await q.dequeue()
        assert d1 is not None
        await q.fail(d1, "e1")
        with q._lock:
            row = (
                q._get_conn()
                .execute("SELECT retry_after FROM jobs WHERE id = ?", ("j1",))
                .fetchone()
            )
        assert row is not None
        t1 = datetime.fromisoformat(row["retry_after"])

        # Clear retry_after, dequeue, fail again: backoff = 2s.
        with q._lock:
            q._get_conn().execute("UPDATE jobs SET retry_after = NULL WHERE id = ?", ("j1",))
            q._get_conn().commit()
        d2 = await q.dequeue()
        assert d2 is not None
        await q.fail(d2, "e2")
        with q._lock:
            row = (
                q._get_conn()
                .execute("SELECT retry_after FROM jobs WHERE id = ?", ("j1",))
                .fetchone()
            )
        assert row is not None
        t2 = datetime.fromisoformat(row["retry_after"])

        # Second backoff should be ~2x the first (1s → 2s).
        diff1 = (t1 - datetime.now(UTC)).total_seconds()
        diff2 = (t2 - datetime.now(UTC)).total_seconds()
        # Both should be positive (in the future).
        assert diff1 > 0
        assert diff2 > 0
        # Second delay should be roughly double the first.
        assert abs(diff2 - 2 * diff1) < 1.0  # allow 1s tolerance

    @pytest.mark.asyncio
    async def test_backoff_capped_at_60s(self, tmp_path: Path) -> None:
        """Backoff should cap at 60 seconds even after many retries."""
        from video_editor_state.queue import PersistentJobQueue

        # retries=100 → 2^99 seconds, but capped at 60.
        delay = PersistentJobQueue._backoff_seconds(100)
        assert delay == 60.0

        # retries=1 → 2^0 = 1s.
        delay = PersistentJobQueue._backoff_seconds(1)
        assert delay == 1.0

        # retries=6 → 2^5 = 32s.
        delay = PersistentJobQueue._backoff_seconds(6)
        assert delay == 32.0


class TestPersistentJobQueueRoundRobin:
    """Tests for per-tenant round-robin fairness."""

    @pytest.mark.asyncio
    async def test_round_robin_serves_tenants_fairly(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)

        # Enqueue 3 jobs per tenant across 3 tenants.
        for i in range(3):
            for t in ("t1", "t2", "t3"):
                await q.enqueue(JobMessage(job_id=f"{t}-{i}", tenant_id=t, command="run"))

        # Dequeue 9 jobs — should alternate across tenants.
        tenants_seen: list[str] = []
        for _ in range(9):
            d = await q.dequeue()
            assert d is not None
            tenants_seen.append(d.tenant_id)
            await q.complete(d)

        # Each tenant should appear 3 times.
        assert tenants_seen.count("t1") == 3
        assert tenants_seen.count("t2") == 3
        assert tenants_seen.count("t3") == 3

        # The first 3 dequeues should be from 3 different tenants.
        assert len(set(tenants_seen[:3])) == 3

    @pytest.mark.asyncio
    async def test_round_robin_new_tenant_gets_turn(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)

        # t1 has 5 jobs, t2 has 1 job.
        for i in range(5):
            await q.enqueue(JobMessage(job_id=f"t1-{i}", tenant_id="t1", command="run"))
        await q.enqueue(JobMessage(job_id="t2-0", tenant_id="t2", command="run"))

        # Dequeue all — t2 should get served before t1's remaining 4 jobs.
        tenants: list[str] = []
        for _ in range(6):
            d = await q.dequeue()
            assert d is not None
            tenants.append(d.tenant_id)
            await q.complete(d)

        # t2 should appear within the first 2 dequeues.
        assert "t2" in tenants[:2]

    @pytest.mark.asyncio
    async def test_scoped_dequeue_bypasses_round_robin(self, tmp_path: Path) -> None:
        from video_editor_state.queue import JobMessage, PersistentJobQueue

        db = tmp_path / "queue.db"
        q = PersistentJobQueue(db)

        await q.enqueue(JobMessage(job_id="t1-0", tenant_id="t1", command="run"))
        await q.enqueue(JobMessage(job_id="t2-0", tenant_id="t2", command="run"))

        # Scoped dequeue for t2 should return t2's job.
        d = await q.dequeue("t2")
        assert d is not None
        assert d.tenant_id == "t2"


class TestPersistentJobQueueSchemaMigration:
    """Tests for schema migration (retry_after column)."""

    @pytest.mark.asyncio
    async def test_old_db_gets_retry_after_column(self, tmp_path: Path) -> None:
        import sqlite3

        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"

        # Create an old-schema DB (no retry_after column).
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                command TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error TEXT,
                retries INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3
            )
        """)
        conn.commit()
        conn.close()

        # Opening with PersistentJobQueue should add the column.
        q = PersistentJobQueue(db)
        with q._lock:
            # Verify retry_after column exists.
            q._get_conn().execute("SELECT retry_after FROM jobs LIMIT 0")

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        from video_editor_state.queue import PersistentJobQueue

        db = tmp_path / "queue.db"
        # Open twice — second open should not fail on ALTER TABLE.
        q1 = PersistentJobQueue(db)
        q2 = PersistentJobQueue(db)
        # Both should work.
        assert q1 is not None
        assert q2 is not None
