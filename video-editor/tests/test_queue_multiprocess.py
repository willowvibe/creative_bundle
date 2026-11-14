"""PersistentJobQueue claim and lease tests (#274, #275).

The claim test starts real processes: the bug was a select-then-update race
between separate SQLite connections, which threads sharing one queue object
(and its lock) cannot reproduce.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from video_editor_state.errors import LeaseLostError
from video_editor_state.queue import InMemoryJobQueue, JobMessage, PersistentJobQueue

WORKERS = 4
TRIALS = 20


# Each claimant is a fresh interpreter (no fork of the threaded test process).
# It opens its own connection, waits for the shared "go" file, then dequeues.
CLAIMANT = """
import asyncio, os, sys, time
from video_editor_state.queue import PersistentJobQueue
queue = PersistentJobQueue(sys.argv[1])
open(sys.argv[2] + "." + str(os.getpid()), "w").close()
while not os.path.exists(sys.argv[2]):
    time.sleep(0.001)
message = asyncio.run(queue.dequeue())
print("claimed:" + (message.job_id if message else ""))
"""


INITIALISER = """
import sys
from video_editor_state.queue import PersistentJobQueue
PersistentJobQueue(sys.argv[1])
print("initialised")
"""


def test_several_workers_can_initialise_a_fresh_database(tmp_path: Path) -> None:
    """#274/#275: concurrent schema creation must not kill a starting worker.

    Each worker used to race ``CREATE TABLE`` + ``ALTER TABLE`` on a brand-new
    database: losers raised ``database is locked`` or ``duplicate column name:
    heartbeat_at``. The parent creating the DB first (as the claim test does)
    hid the bug.
    """
    for trial in range(5):
        db = tmp_path / f"fresh-{trial}.db"
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", INITIALISER, str(db)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(WORKERS)
        ]
        results = [proc.communicate(timeout=30) for proc in procs]
        failures = [
            (proc.returncode, stdout, stderr)
            for proc, (stdout, stderr) in zip(procs, results, strict=True)
            if proc.returncode != 0
        ]
        assert failures == [], f"trial {trial}: {failures}"
        assert all("initialised" in stdout for stdout, _ in results)


def test_one_job_is_claimed_by_exactly_one_process(tmp_path: Path) -> None:
    for trial in range(TRIALS):
        db = tmp_path / f"queue-{trial}.db"
        go = tmp_path / f"go-{trial}"
        asyncio.run(
            PersistentJobQueue(db).enqueue(
                JobMessage(job_id="only-job", tenant_id="t", command="run")
            )
        )
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", CLAIMANT, str(db), str(go)],
                stdout=subprocess.PIPE,
                text=True,
            )
            for _ in range(WORKERS)
        ]
        deadline = time.monotonic() + 30
        while len(list(tmp_path.glob(f"go-{trial}.*"))) < WORKERS:
            assert time.monotonic() < deadline, "claimants did not start"
            time.sleep(0.01)
        go.touch()
        # structlog also writes to stdout; the claimed id is the last line.
        outputs = [proc.communicate(timeout=30)[0] for proc in procs]
        claims = [(out.strip().splitlines() or [""])[-1] for out in outputs]
        assert claims.count("claimed:only-job") == 1, f"trial {trial}: {claims}"


def _backdate(db: Path, column: str, job_id: str, seconds: float) -> None:
    stamp = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE jobs SET {column} = ? WHERE id = ?", (stamp, job_id))


@pytest.mark.asyncio
async def test_job_without_heartbeat_is_reclaimed(tmp_path: Path) -> None:
    db = tmp_path / "queue.db"
    queue = PersistentJobQueue(db)
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))
    claimed = await queue.dequeue()
    assert claimed is not None
    # The worker that took it died: no heartbeat, started long ago.
    _backdate(db, "started_at", "j1", 3600)

    stale = await queue.requeue_stale(timeout_seconds=900)

    assert [m.job_id for m in stale] == ["j1"]
    again = await PersistentJobQueue(db).dequeue()
    assert again is not None and again.job_id == "j1"


@pytest.mark.asyncio
async def test_heartbeat_keeps_a_long_job(tmp_path: Path) -> None:
    db = tmp_path / "queue.db"
    queue = PersistentJobQueue(db)
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))
    claimed = await queue.dequeue()
    assert claimed is not None
    _backdate(db, "started_at", "j1", 3600)

    await queue.heartbeat(claimed)
    stale = await queue.requeue_stale(timeout_seconds=900)

    assert stale == []
    assert await queue.dequeue() is None


@pytest.mark.asyncio
async def test_a_stale_job_that_exhausted_retries_is_dead_lettered(
    tmp_path: Path,
) -> None:
    db = tmp_path / "queue.db"
    queue = PersistentJobQueue(db)
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run", max_retries=0))
    assert await queue.dequeue() is not None
    _backdate(db, "started_at", "j1", 3600)

    stale = await queue.requeue_stale(timeout_seconds=900)

    assert stale == []
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status, error FROM jobs WHERE id = 'j1'").fetchone()
    assert row == ("dead_letter", "stale lease expired")


@pytest.mark.asyncio
async def test_concurrent_sweeps_requeue_once(tmp_path: Path) -> None:
    db = tmp_path / "queue.db"
    await PersistentJobQueue(db).enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))
    assert await PersistentJobQueue(db).dequeue() is not None
    _backdate(db, "started_at", "j1", 3600)

    first = await PersistentJobQueue(db).requeue_stale(timeout_seconds=900)
    second = await PersistentJobQueue(db).requeue_stale(timeout_seconds=900)

    assert len(first) + len(second) == 1


@pytest.mark.asyncio
async def test_heartbeat_column_is_added_to_an_old_database(tmp_path: Path) -> None:
    db = tmp_path / "queue.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL DEFAULT 'default', "
            "command TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL "
            "DEFAULT 'pending', created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, "
            "error TEXT, retries INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL "
            "DEFAULT 3)"
        )

    queue = PersistentJobQueue(db)
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))

    claimed = await queue.dequeue()
    assert claimed is not None
    await queue.heartbeat(claimed)


@pytest.mark.asyncio
async def test_in_memory_heartbeat_refreshes_the_lease() -> None:
    queue = InMemoryJobQueue()
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))
    claimed = await queue.dequeue()
    assert claimed is not None
    queue._dequeue_times["j1"] -= 3600

    await queue.heartbeat(claimed)

    assert await queue.requeue_stale(timeout_seconds=900) == []


@pytest.mark.asyncio
async def test_persistent_queue_runs_sqlite_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#284: a DB call (and its busy_timeout wait) must not block the loop."""
    import threading

    queue = PersistentJobQueue(tmp_path / "queue.db")
    await queue.enqueue(JobMessage(job_id="j1", tenant_id="t", command="run"))
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real = queue._dequeue_sync

    def spy(tenant_id: str | None = None) -> JobMessage | None:
        seen.append(threading.get_ident())
        return real(tenant_id)

    monkeypatch.setattr(queue, "_dequeue_sync", spy)

    claimed = await queue.dequeue()

    assert claimed is not None and claimed.job_id == "j1"
    assert seen and seen[0] != loop_thread


def test_a_reclaimed_persistent_claim_cannot_touch_the_job(tmp_path: Path) -> None:
    """#298: the lease makes complete/fail/heartbeat refuse a stale owner."""
    db = tmp_path / "queue.db"
    owner = PersistentJobQueue(db)
    sweeper = PersistentJobQueue(db)
    asyncio.run(owner.enqueue(JobMessage(job_id="job-lease", tenant_id="t", command="render")))

    first = asyncio.run(owner.dequeue())
    assert first is not None and first.lease_id

    # A fresh beat keeps the claim.
    asyncio.run(owner.heartbeat(first))
    assert asyncio.run(sweeper.requeue_stale(3600)) == []

    # Force expiry and let the sweeper take it.
    time.sleep(0.02)
    assert [m.job_id for m in asyncio.run(sweeper.requeue_stale(0.0))] == ["job-lease"]
    second = asyncio.run(sweeper.dequeue())
    assert second is not None
    assert second.lease_id != first.lease_id

    with pytest.raises(LeaseLostError):
        asyncio.run(owner.complete(first))
    with pytest.raises(LeaseLostError):
        asyncio.run(owner.heartbeat(first))
    with pytest.raises(LeaseLostError):
        asyncio.run(owner.fail(first, "late error"))

    asyncio.run(sweeper.complete(second))
    state = asyncio.run(sweeper.status("job-lease"))
    assert state is not None and state["status"] == "completed"


def test_persistent_release_does_not_consume_a_retry(tmp_path: Path) -> None:
    """A lock contention must hand the claim back, not burn a retry (#298)."""
    db = tmp_path / "queue.db"
    queue = PersistentJobQueue(db)
    asyncio.run(
        queue.enqueue(JobMessage(job_id="job-lock", tenant_id="t", command="render", max_retries=3))
    )
    first = asyncio.run(queue.dequeue())
    assert first is not None

    asyncio.run(queue.release(first))

    state = asyncio.run(queue.status("job-lock"))
    assert state is not None
    assert state["status"] == "pending"
    assert state["retries"] == 0

    # Skip the short re-queue delay rather than sleeping two seconds.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE jobs SET retry_after = NULL WHERE id = 'job-lock'")
    second = asyncio.run(queue.dequeue())
    assert second is not None and second.lease_id != first.lease_id
    asyncio.run(queue.complete(second))
