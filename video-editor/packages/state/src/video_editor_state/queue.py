"""Multi-tenant job queue abstractions.

This module provides a minimal queue layer that can later be backed by Redis,
PostgreSQL, or another persistent store.  The in-memory implementation is
useful for single-process local development and tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from video_editor_state.errors import LeaseLostError

logger = structlog.get_logger(__name__)


def _is_locked(exc: sqlite3.OperationalError) -> bool:
    """Return True for SQLite's transient contention errors."""
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message


def idempotency_key_for(
    command: str, payload: dict[str, Any], *, tenant_id: str = "default"
) -> str:
    """Stable key for callers that want duplicate enqueues to collapse."""
    encoded = json.dumps(
        {"tenant_id": tenant_id, "command": command, "payload": payload},
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class JobMessage:
    """A single unit of queueable work."""

    job_id: str
    tenant_id: str
    command: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    priority: int = 0
    retries: int = 0
    max_retries: int = 3
    #: Caller-supplied stable key; duplicate enqueues return the first job.
    idempotency_key: str | None = None
    #: Identifies the worker that currently owns this claim (#298). Set by
    #: ``dequeue``; ``heartbeat``/``complete``/``fail`` must present the same
    #: value, so a worker whose lease expired cannot mutate the row.
    lease_id: str | None = None
    # Internal scheduling field used by the in-memory queue to implement
    # exponential backoff. ``None`` means the message is immediately available.
    available_at: float | None = None


class JobQueue(ABC):
    """Abstract interface for a multi-tenant job queue."""

    @abstractmethod
    async def enqueue(self, message: JobMessage) -> JobMessage:
        """Add a message, returning the existing job for a duplicate key."""

    @abstractmethod
    async def dequeue(self, tenant_id: str | None = None) -> JobMessage | None:
        """Fetch the next message, optionally scoped to a tenant."""

    @abstractmethod
    async def complete(self, message: JobMessage) -> None:
        """Mark a message as completed."""

    @abstractmethod
    async def fail(self, message: JobMessage, reason: str) -> None:
        """Mark a message as failed."""

    @abstractmethod
    async def size(self, tenant_id: str | None = None) -> int:
        """Return the number of pending messages."""

    @abstractmethod
    async def pending_count(self, tenant_id: str | None = None) -> int:
        """Return the number of pending (un-dispatched) messages.

        Equivalent to :meth:`size`; provided for callers that want to
        distinguish "in-flight" state from "succeeded" / "failed" state
        without poking at private attributes.
        """

    @abstractmethod
    async def completed_count(self, tenant_id: str | None = None) -> int:
        """Return the number of messages marked complete since queue start.

        ``tenant_id`` filters by tenant when provided; ``None`` (the
        default) returns the global count across all tenants.
        """

    @abstractmethod
    async def failed_count(self, tenant_id: str | None = None) -> int:
        """Return the number of messages marked failed since queue start.

        ``tenant_id`` filters by tenant when provided; ``None`` (the
        default) returns the global count across all tenants.
        """

    @abstractmethod
    async def dead_letter_count(self, tenant_id: str | None = None) -> int:
        """Return the number of messages that exhausted retries."""

    @abstractmethod
    async def requeue_stale(self, timeout_seconds: float = 300.0) -> list[JobMessage]:
        """Return messages that were dequeued but not completed/failed
        within ``timeout_seconds`` back to the pending queue.
        """

    async def status(self, job_id: str) -> dict[str, Any] | None:  # noqa: B027
        """Return a job's queue state, or ``None`` when it is unknown."""

    async def heartbeat(self, message: JobMessage) -> None:  # noqa: B027
        """Extend an in-flight message's lease so ``requeue_stale`` keeps it.

        A worker calls this periodically while a job runs (#275). The default
        is a no-op for queues that have no lease.
        """

    async def release(self, message: JobMessage) -> None:  # noqa: B027
        """Return a claim without consuming a retry (#298).

        Called when the worker dequeued a job whose per-job lock is held by a
        live worker: the handler never ran, so counting a failure would burn a
        retry and overwrite the owner's error. The default does nothing;
        backends that track claims implement it.
        """


class InMemoryJobQueue(JobQueue):
    """FIFO in-memory queue with per-tenant round-robin fairness.

    Internally we keep a per-tenant ``deque`` plus a **rotation order** that
    remembers the order in which tenants last received a job. On dequeue we
    advance the rotation so each tenant is visited in turn; if a tenant has
    no pending work we skip it and move on. This prevents a high-volume
    tenant from starving low-volume ones (head-of-line blocking).
    """

    def __init__(self) -> None:
        self._pending: dict[str, deque[JobMessage]] = {}
        self._rotation: list[str] = []  # tenants in the order they were last served
        self._cursor: int = 0
        self._completed: list[JobMessage] = []
        self._failed: list[tuple[JobMessage, str]] = []
        self._dead_letter: list[tuple[JobMessage, str]] = []
        self._in_flight: dict[str, JobMessage] = {}
        # Parallel to ``_in_flight``: records the wall-clock time at which
        # each in-flight message was dequeued. ``requeue_stale`` measures
        # staleness from this dequeue time, NOT from ``JobMessage.created_at``
        # (which is the enqueue time). Without this, a message that sat
        # pending for 10 minutes under load and was dequeued 1 second ago
        # would be requeued while a worker is actively processing it —
        # causing duplicate side effects (double FAL submissions, double
        # ffmpeg renders, double billing). See audit finding C1.
        self._dequeue_times: dict[str, float] = {}
        self._delayed: dict[str, JobMessage] = {}
        # Set of job_ids that have reached a terminal state (completed or
        # failed after exhausting retries). Prevents a racing/buggy caller
        # from moving an already-completed job into _delayed/_failed and
        # causing duplicate processing after the backoff expires.
        self._terminal: set[str] = set()
        self._idempotency: dict[str, JobMessage] = {}
        self._lock = asyncio.Lock()

    def _tenants_in_order(self) -> list[str]:
        """Return tenants in round-robin order.

        Tenants that have been served live in ``_rotation`` in the order they
        were last served. Any new tenant (with pending work but not yet in the
        rotation) is appended at the end so it gets a turn too.
        """
        # Newly-arrived tenants that haven't been rotated through yet.
        pending_tenants = set(self._pending.keys())
        rotation_set = set(self._rotation)
        new_tenants = sorted(pending_tenants - rotation_set)
        return self._rotation + new_tenants

    def _backoff_seconds(self, retries: int) -> float:
        """Exponential backoff: 1s, 2s, 4s, ... capped at 60s."""
        return min(2.0 ** max(0, retries - 1), 60.0)

    def _move_ready_delayed(self) -> None:
        """Promote delayed messages whose backoff has expired to pending."""
        now = datetime.now(UTC).timestamp()
        ready: list[JobMessage] = []
        for job_id, msg in list(self._delayed.items()):
            if msg.available_at is not None and msg.available_at <= now:
                ready.append(msg)
                self._delayed.pop(job_id, None)
        for msg in ready:
            bucket = self._pending.setdefault(msg.tenant_id, deque())
            bucket.append(msg)
            if msg.tenant_id not in self._rotation:
                self._rotation.append(msg.tenant_id)

    def _claim(self, message: JobMessage) -> JobMessage:
        """Take ownership of ``message`` and return this claim's copy (#298).

        The returned copy carries a fresh ``lease_id`` and is the object later
        passed to ``heartbeat``/``complete``/``fail``. A copy matters: after a
        stale reclaim the queue would otherwise mutate the same ``JobMessage``
        the old worker still holds, and its ``lease_id`` would look current.
        """
        claimed = replace(message, lease_id=uuid.uuid4().hex, available_at=None)
        self._in_flight[claimed.job_id] = claimed
        self._dequeue_times[claimed.job_id] = datetime.now(UTC).timestamp()
        return claimed

    def _require_lease(self, message: JobMessage) -> None:
        """Raise when ``message`` is no longer the current claim."""
        if self._in_flight.get(message.job_id) is not message:
            raise LeaseLostError(f"job {message.job_id} is no longer owned by this worker")

    async def enqueue(self, message: JobMessage) -> JobMessage:
        async with self._lock:
            existing = (
                self._idempotency.get(message.idempotency_key)
                if message.idempotency_key is not None
                else None
            )
            if existing is not None:
                logger.info(
                    "job_enqueue_duplicate",
                    job_id=existing.job_id,
                    requested_job_id=message.job_id,
                    idempotency_key=message.idempotency_key,
                )
                return existing
            if message.idempotency_key is not None:
                self._idempotency[message.idempotency_key] = message
            bucket = self._pending.setdefault(message.tenant_id, deque())
            bucket.append(message)
        logger.info("job_enqueued", job_id=message.job_id, tenant_id=message.tenant_id)
        return message

    async def dequeue(self, tenant_id: str | None = None) -> JobMessage | None:
        async with self._lock:
            self._move_ready_delayed()
            if tenant_id is not None:
                # Caller asked for a specific tenant — bypass round-robin
                # and use the FIFO order for that bucket only.
                bucket = self._pending.get(tenant_id)
                if not bucket:
                    return None
                msg = bucket.popleft()
                claimed = self._claim(msg)
                if not bucket:
                    self._pending.pop(tenant_id, None)
                    if tenant_id in self._rotation:
                        self._rotation.remove(tenant_id)
                        if self._cursor >= len(self._rotation):
                            self._cursor = 0
                logger.info("job_dequeued", job_id=claimed.job_id, tenant_id=claimed.tenant_id)
                return claimed

            # Round-robin across tenants that have pending work.
            order = self._tenants_in_order()
            n = len(order)
            for _ in range(n):
                if not order:
                    return None
                idx = self._cursor % n
                t = order[idx]
                bucket = self._pending.get(t)
                if not bucket:
                    # Tenant drained between _tenants_in_order() and now —
                    # rotate them out and try again.
                    order.pop(idx)
                    if t in self._rotation:
                        self._rotation.remove(t)
                    if not order:
                        return None
                    n -= 1
                    if self._cursor >= n:
                        self._cursor = 0
                    continue
                msg = bucket.popleft()
                # Move this tenant to the back of the rotation order so
                # the next call serves the next tenant first.
                if t in self._rotation:
                    self._rotation.remove(t)
                self._rotation.append(t)
                if not bucket:
                    self._pending.pop(t, None)
                    # If the bucket drained, the rotation entry is harmless
                    # (next call will skip via the empty-bucket branch).
                # Advance cursor so the NEXT call serves a different tenant.
                self._cursor = (self._cursor + 1) % max(1, n)
                claimed = self._claim(msg)
                logger.info("job_dequeued", job_id=claimed.job_id, tenant_id=claimed.tenant_id)
                return claimed
            return None

    async def complete(self, message: JobMessage) -> None:
        async with self._lock:
            if message.job_id in self._terminal:
                logger.warning(
                    "job_complete_ignored_already_terminal",
                    job_id=message.job_id,
                    tenant_id=message.tenant_id,
                )
                return
            self._require_lease(message)
            self._terminal.add(message.job_id)
            self._completed.append(message)
            self._in_flight.pop(message.job_id, None)
            self._dequeue_times.pop(message.job_id, None)
            self._delayed.pop(message.job_id, None)
        logger.info("job_completed", job_id=message.job_id, tenant_id=message.tenant_id)

    async def fail(self, message: JobMessage, reason: str) -> None:
        async with self._lock:
            if message.job_id in self._terminal:
                logger.warning(
                    "job_fail_ignored_already_terminal",
                    job_id=message.job_id,
                    tenant_id=message.tenant_id,
                    reason=reason,
                )
                return
            self._require_lease(message)
            self._in_flight.pop(message.job_id, None)
            self._dequeue_times.pop(message.job_id, None)
            if message.retries < message.max_retries:
                message.retries += 1
                delay = self._backoff_seconds(message.retries)
                message.available_at = datetime.now(UTC).timestamp() + delay
                self._delayed[message.job_id] = message
                logger.warning(
                    "job_retry_scheduled",
                    job_id=message.job_id,
                    tenant_id=message.tenant_id,
                    retries=message.retries,
                    delay=delay,
                    reason=reason,
                )
            else:
                self._terminal.add(message.job_id)
                self._failed.append((message, reason))
                self._dead_letter.append((message, reason))
                logger.warning(
                    "job_failed",
                    job_id=message.job_id,
                    tenant_id=message.tenant_id,
                    reason=reason,
                )

    async def size(self, tenant_id: str | None = None) -> int:
        async with self._lock:
            self._move_ready_delayed()
            if tenant_id is None:
                return sum(len(b) for b in self._pending.values())
            bucket = self._pending.get(tenant_id)
            return len(bucket) if bucket else 0

    async def pending_count(self, tenant_id: str | None = None) -> int:
        return await self.size(tenant_id)

    async def completed_count(self, tenant_id: str | None = None) -> int:
        async with self._lock:
            if tenant_id is None:
                return len(self._completed)
            return sum(1 for m in self._completed if m.tenant_id == tenant_id)

    async def failed_count(self, tenant_id: str | None = None) -> int:
        async with self._lock:
            if tenant_id is None:
                return len(self._failed)
            return sum(1 for m, _ in self._failed if m.tenant_id == tenant_id)

    async def dead_letter_count(self, tenant_id: str | None = None) -> int:
        async with self._lock:
            if tenant_id is None:
                return len(self._dead_letter)
            return sum(1 for m, _ in self._dead_letter if m.tenant_id == tenant_id)

    async def heartbeat(self, message: JobMessage) -> None:
        async with self._lock:
            self._require_lease(message)
            self._dequeue_times[message.job_id] = datetime.now(UTC).timestamp()

    async def release(self, message: JobMessage) -> None:
        """Return the claim to ``pending`` without consuming a retry (#298)."""
        async with self._lock:
            if self._in_flight.get(message.job_id) is not message:
                logger.warning(
                    "job_release_ignored_lease_lost",
                    job_id=message.job_id,
                    tenant_id=message.tenant_id,
                )
                return
            self._in_flight.pop(message.job_id, None)
            self._dequeue_times.pop(message.job_id, None)
            message.lease_id = None
            message.available_at = datetime.now(UTC).timestamp() + 2.0
            self._delayed[message.job_id] = message
        logger.info("job_released", job_id=message.job_id, tenant_id=message.tenant_id)

    async def requeue_stale(self, timeout_seconds: float = 300.0) -> list[JobMessage]:
        """Return in-flight messages older than ``timeout_seconds`` to
        the pending queue. Helps recover jobs lost to a worker crash
        between dequeue and complete/fail.

        ``timeout_seconds`` is measured from the **dequeue** time (when a
        worker took the message), not the enqueue time (``created_at``).
        A message that sat pending for an hour under load and was
        dequeued 1 second ago is NOT stale; a message dequeued 10
        minutes ago with no complete/fail IS stale.
        """
        cutoff = datetime.now(UTC).timestamp() - timeout_seconds
        stale: list[JobMessage] = []
        async with self._lock:
            self._move_ready_delayed()
            for job_id, msg in list(self._in_flight.items()):
                dequeued_at = self._dequeue_times.get(job_id)
                if dequeued_at is None:
                    # Defensive: no recorded dequeue time (e.g. a message
                    # placed in _in_flight by an older code path). Treat
                    # it as stale only if its enqueue time is old.
                    dequeued_at = datetime.fromisoformat(msg.created_at).timestamp()
                if dequeued_at < cutoff:
                    self._in_flight.pop(job_id, None)
                    self._dequeue_times.pop(job_id, None)
                    if msg.retries < msg.max_retries:
                        msg.retries += 1
                        msg.lease_id = None
                        stale.append(msg)
                        bucket = self._pending.setdefault(msg.tenant_id, deque())
                        bucket.append(msg)
                        if msg.tenant_id not in self._rotation:
                            self._rotation.append(msg.tenant_id)
                    else:
                        self._terminal.add(job_id)
                        self._dead_letter.append((msg, "stale lease expired"))
                        logger.error(
                            "job_stale_dead_lettered",
                            job_id=job_id,
                            tenant_id=msg.tenant_id,
                            retries=msg.retries,
                        )
        for msg in stale:
            logger.warning(
                "job_requeued_stale",
                job_id=msg.job_id,
                tenant_id=msg.tenant_id,
            )
        return stale


class PersistentJobQueue(JobQueue):
    """SQLite-backed job queue that survives process restarts.

    Uses a single persistent connection with WAL journal mode for concurrent
    read/write safety.  The public methods are ``async`` to conform to the
    :class:`JobQueue` ABC; each runs its ``_*_sync`` twin in
    ``asyncio.to_thread`` so SQLite (and a ``busy_timeout`` wait) never blocks
    the event loop.  A ``threading.Lock`` serialises all DB access, so the
    queue is safe from multiple asyncio tasks and pool threads.

    Implements per-tenant round-robin fairness (matching
    :class:`InMemoryJobQueue`) and exponential backoff for retries.
    """

    def __init__(self, db_path: Path | str = "outputs/queue.db"):
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _backoff_seconds(retries: int) -> float:
        """Exponential backoff: 1s, 2s, 4s, ... capped at 60s."""
        return min(2.0 ** max(0, retries - 1), 60.0)

    def _get_conn(self) -> sqlite3.Connection:
        """Return the persistent connection, creating it on first access.

        Uses WAL journal mode so concurrent readers don't block writers.
        A single connection is safe because all access is serialised by
        ``self._lock``. The busy timeout is set *before* switching the journal
        mode: a fresh database opened by several workers at once otherwise
        fails the WAL switch with ``database is locked`` before it can wait.
        """
        if self._conn is None:
            # ``check_same_thread=False``: the async methods run the DB work in
            # ``asyncio.to_thread`` pool threads; ``self._lock`` serialises them.
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA busy_timeout=30000")
            for attempt in range(8):
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if not _is_locked(exc) or attempt == 7:
                        conn.close()
                        raise
                    time.sleep(0.05 * (2**attempt))
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        """Create or migrate the schema, safely from several processes.

        ``CREATE TABLE IF NOT EXISTS`` plus ``ALTER TABLE`` is racy when N
        workers start on a fresh database: two processes can both see a missing
        column and the loser raises ``duplicate column name``. The transaction
        is therefore retried under a short backoff, and the new columns are
        declared for fresh databases (the ``ALTER`` is only for old ones).
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_conn()
        with self._lock:
            for attempt in range(8):
                try:
                    conn.execute("BEGIN IMMEDIATE")
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
                            max_retries INTEGER NOT NULL DEFAULT 3,
                            retry_after TEXT,
                            heartbeat_at TEXT,
                            lease_id TEXT,
                            idempotency_key TEXT
                        )
                    """)
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
                    # Old databases predate these columns. Must run BEFORE the
                    # partial index that references retry_after.
                    if "retry_after" not in columns:
                        conn.execute("ALTER TABLE jobs ADD COLUMN retry_after TEXT")
                    if "heartbeat_at" not in columns:
                        conn.execute("ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT")
                    if "lease_id" not in columns:
                        conn.execute("ALTER TABLE jobs ADD COLUMN lease_id TEXT")
                    if "idempotency_key" not in columns:
                        conn.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_jobs_status
                        ON jobs(status, created_at)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_jobs_pending_retry
                        ON jobs(status, retry_after) WHERE status = 'pending'
                    """)
                    conn.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
                        ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL
                    """)
                    # Round-robin tracking: records the last time each tenant was served.
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS tenant_served (
                            tenant_id TEXT PRIMARY KEY,
                            last_served_at TEXT NOT NULL
                        )
                    """)
                    conn.commit()
                    return
                except sqlite3.OperationalError as exc:
                    conn.rollback()
                    message = str(exc).lower()
                    lost_migration = "duplicate column name" in message
                    if (not lost_migration and not _is_locked(exc)) or attempt == 7:
                        raise
                    time.sleep(0.05 * (2**attempt))

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> JobMessage:
        return JobMessage(
            job_id=row["id"],
            tenant_id=row["tenant_id"],
            command=row["command"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            retries=row["retries"],
            max_retries=row["max_retries"],
            idempotency_key=row["idempotency_key"],
            lease_id=row["lease_id"],
        )

    # ------------------------------------------------------------------
    #  JobQueue ABC implementation
    # ------------------------------------------------------------------

    def _enqueue_sync(self, message: JobMessage) -> JobMessage:
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            conn.execute(
                "INSERT OR IGNORE INTO jobs (id, tenant_id, command, payload_json, "
                "status, created_at, retries, max_retries, idempotency_key) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    message.job_id,
                    message.tenant_id,
                    message.command,
                    json.dumps(message.payload),
                    message.created_at or datetime.now(UTC).isoformat(),
                    message.retries,
                    message.max_retries,
                    message.idempotency_key,
                ),
            )
            if message.idempotency_key is not None:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?",
                    (message.idempotency_key,),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (message.job_id,)).fetchone()
            conn.commit()
        assert row is not None
        existing = self._row_to_message(row)
        if existing.job_id != message.job_id:
            logger.info(
                "job_enqueue_duplicate_persistent",
                job_id=existing.job_id,
                requested_job_id=message.job_id,
            )
        else:
            logger.info(
                "job_enqueued_persistent",
                job_id=message.job_id,
                tenant_id=message.tenant_id,
            )
        return existing

    def _dequeue_sync(self, tenant_id: str | None = None) -> JobMessage | None:
        """Claim the next ready job atomically (#274).

        The claim is one ``UPDATE … WHERE status = 'pending' … RETURNING``
        inside ``BEGIN IMMEDIATE``. A select-then-update let every worker that
        read the row before the first write claim it too: four processes on one
        job each ran it. The status guard makes a lost race return no row, and
        the immediate transaction keeps the claim and the round-robin bookkeeping
        together.
        """
        now = datetime.now(UTC).isoformat()
        lease = uuid.uuid4().hex
        if tenant_id is not None:
            # Caller asked for a specific tenant — bypass round-robin.
            pick = (
                "SELECT id FROM jobs WHERE tenant_id = :tenant AND status = 'pending' "
                "AND (retry_after IS NULL OR retry_after <= :now) "
                "ORDER BY created_at ASC LIMIT 1"
            )
        else:
            # Round-robin: pick the tenant least-recently served that has
            # a ready (retry_after expired) pending job.
            pick = (
                "SELECT j.id FROM jobs j "
                "LEFT JOIN tenant_served ts ON ts.tenant_id = j.tenant_id "
                "WHERE j.status = 'pending' "
                "AND (j.retry_after IS NULL OR j.retry_after <= :now) "
                "ORDER BY COALESCE(ts.last_served_at, '1970-01-01T00:00:00') ASC, "
                "j.created_at ASC LIMIT 1"
            )
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = :now, "
                    "heartbeat_at = NULL, lease_id = :lease "
                    f"WHERE id = ({pick}) AND status = 'pending' "
                    "RETURNING *",
                    {"now": now, "tenant": tenant_id, "lease": lease},
                ).fetchone()
                if row is not None:
                    # Record this tenant as served for round-robin tracking.
                    conn.execute(
                        "INSERT INTO tenant_served (tenant_id, last_served_at) VALUES (?, ?) "
                        "ON CONFLICT(tenant_id) DO UPDATE SET last_served_at = ?",
                        (row["tenant_id"], now, now),
                    )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            if row is None:
                return None
            msg = self._row_to_message(row)
        logger.info(
            "job_dequeued_persistent",
            job_id=msg.job_id,
            tenant_id=msg.tenant_id,
        )
        return msg

    def _complete_sync(self, message: JobMessage) -> None:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE jobs SET status = 'completed', completed_at = ?, lease_id = NULL "
                "WHERE id = ? AND status = 'running' AND lease_id = ?",
                (datetime.now(UTC).isoformat(), message.job_id, message.lease_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                # A duplicate complete (after a previous successful one) stays
                # idempotent; anything else means another worker owns the job.
                already = conn.execute(
                    "SELECT 1 FROM jobs WHERE id = ? AND status = 'completed'",
                    (message.job_id,),
                ).fetchone()
                if already is not None:
                    logger.info(
                        "job_complete_ignored_already_completed",
                        job_id=message.job_id,
                        tenant_id=message.tenant_id,
                    )
                    return
                raise LeaseLostError(f"job {message.job_id} is no longer owned by this worker")
        logger.info(
            "job_completed_persistent",
            job_id=message.job_id,
            tenant_id=message.tenant_id,
        )

    def _fail_sync(self, message: JobMessage, reason: str) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT retries, max_retries, status, lease_id FROM jobs WHERE id = ?",
                    (message.job_id,),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    logger.warning(
                        "job_fail_unknown_id",
                        job_id=message.job_id,
                        reason=reason,
                    )
                    return
                if row["status"] != "running" or row["lease_id"] != message.lease_id:
                    # Another worker reclaimed the job after this lease
                    # expired; it must not consume a retry or overwrite the
                    # owner's error (#298).
                    conn.rollback()
                    raise LeaseLostError(f"job {message.job_id} is no longer owned by this worker")
                current_retries = row["retries"]
                max_retries = row["max_retries"]
                if current_retries < max_retries:
                    delay = self._backoff_seconds(current_retries + 1)
                    retry_after = datetime.now(UTC).timestamp() + delay
                    retry_after_iso = datetime.fromtimestamp(retry_after, tz=UTC).isoformat()
                    conn.execute(
                        "UPDATE jobs SET status = 'pending', retries = retries + 1, "
                        "started_at = NULL, heartbeat_at = NULL, lease_id = NULL, "
                        "error = ?, retry_after = ? WHERE id = ?",
                        (reason, retry_after_iso, message.job_id),
                    )
                    logger.warning(
                        "job_retry_scheduled_persistent",
                        job_id=message.job_id,
                        tenant_id=message.tenant_id,
                        retries=current_retries + 1,
                        delay=delay,
                        reason=reason,
                    )
                else:
                    conn.execute(
                        "UPDATE jobs SET status = 'dead_letter', completed_at = ?, "
                        "lease_id = NULL, error = ? WHERE id = ?",
                        (datetime.now(UTC).isoformat(), reason, message.job_id),
                    )
                    logger.warning(
                        "job_failed_persistent",
                        job_id=message.job_id,
                        tenant_id=message.tenant_id,
                        reason=reason,
                    )
                conn.commit()
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise

    def _size_sync(self, tenant_id: str | None = None) -> int:
        now = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            if tenant_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'pending' "
                    "AND (retry_after IS NULL OR retry_after <= ?)",
                    (tenant_id, now),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'pending' "
                    "AND (retry_after IS NULL OR retry_after <= ?)",
                    (now,),
                ).fetchone()
            return row[0] if row else 0

    def _pending_count_sync(self, tenant_id: str | None = None) -> int:
        return self._size_sync(tenant_id)

    def _completed_count_sync(self, tenant_id: str | None = None) -> int:
        with self._lock:
            conn = self._get_conn()
            if tenant_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'completed'",
                    (tenant_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'completed'",
                ).fetchone()
            return row[0] if row else 0

    def _failed_count_sync(self, tenant_id: str | None = None) -> int:
        with self._lock:
            conn = self._get_conn()
            if tenant_id is not None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE tenant_id = ? AND status = 'dead_letter'",
                    (tenant_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status = 'dead_letter'",
                ).fetchone()
            return row[0] if row else 0

    def _dead_letter_count_sync(self, tenant_id: str | None = None) -> int:
        return self._failed_count_sync(tenant_id)

    def _heartbeat_sync(self, message: JobMessage) -> None:
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE jobs SET heartbeat_at = ? "
                "WHERE id = ? AND status = 'running' AND lease_id = ?",
                (datetime.now(UTC).isoformat(), message.job_id, message.lease_id),
            )
            conn.commit()
            if cursor.rowcount == 0:
                raise LeaseLostError(f"job {message.job_id} is no longer owned by this worker")

    def _release_sync(self, message: JobMessage) -> None:
        """Return the claim to ``pending`` without consuming a retry (#298).

        The short ``retry_after`` gives the live lock holder time to finish
        before another worker picks the job up again.
        """
        retry_after = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE jobs SET status = 'pending', lease_id = NULL, "
                "started_at = NULL, heartbeat_at = NULL, retry_after = ? "
                "WHERE id = ? AND status = 'running' AND lease_id = ?",
                (
                    datetime.fromtimestamp(datetime.now(UTC).timestamp() + 2.0, tz=UTC).isoformat(),
                    message.job_id,
                    message.lease_id,
                ),
            )
            conn.commit()
        if cursor.rowcount == 0:
            logger.warning(
                "job_release_ignored_lease_lost",
                job_id=message.job_id,
                tenant_id=message.tenant_id,
            )
        else:
            logger.info(
                "job_released",
                job_id=message.job_id,
                tenant_id=message.tenant_id,
                retry_after=retry_after,
            )

    def _requeue_stale_sync(self, timeout_seconds: float = 300.0) -> list[JobMessage]:
        """Return running jobs whose lease expired to the pending queue (#275).

        The lease is the last heartbeat, or the start time for a worker that
        never sent one. One guarded ``UPDATE … RETURNING`` reclaims them, so two
        workers sweeping at once cannot both requeue (and later both run) a job.
        """
        cutoff = datetime.now(UTC).timestamp() - timeout_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "UPDATE jobs SET "
                    "status = CASE WHEN retries < max_retries "
                    "THEN 'pending' ELSE 'dead_letter' END, "
                    "retries = CASE WHEN retries < max_retries "
                    "THEN retries + 1 ELSE retries END, "
                    "started_at = NULL, heartbeat_at = NULL, lease_id = NULL, "
                    "completed_at = CASE WHEN retries < max_retries "
                    "THEN NULL ELSE :now END, "
                    "error = 'stale lease expired' "
                    "WHERE status = 'running' "
                    "AND COALESCE(heartbeat_at, started_at) < :cutoff "
                    "RETURNING *",
                    {"now": datetime.now(UTC).isoformat(), "cutoff": cutoff_iso},
                ).fetchall()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            stale = [self._row_to_message(row) for row in rows if row["status"] == "pending"]
            dead = [row for row in rows if row["status"] == "dead_letter"]
        for msg in stale:
            logger.warning(
                "job_requeued_stale_persistent",
                job_id=msg.job_id,
                tenant_id=msg.tenant_id,
            )
        for row in dead:
            logger.error(
                "job_stale_dead_lettered",
                job_id=row["id"],
                tenant_id=row["tenant_id"],
                retries=row["retries"],
            )
        return stale

    def _status_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._get_conn()
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, tenant_id, command, status, error, retries, max_retries, "
                "created_at, started_at, completed_at, heartbeat_at "
                "FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    #  Async interface (#284): every DB call runs in a worker thread, so a
    #  busy SQLite lock never stalls the worker's or the MCP server's loop.
    # ------------------------------------------------------------------

    async def enqueue(self, message: JobMessage) -> JobMessage:
        return await asyncio.to_thread(self._enqueue_sync, message)

    async def dequeue(self, tenant_id: str | None = None) -> JobMessage | None:
        return await asyncio.to_thread(self._dequeue_sync, tenant_id)

    async def complete(self, message: JobMessage) -> None:
        return await asyncio.to_thread(self._complete_sync, message)

    async def fail(self, message: JobMessage, reason: str) -> None:
        return await asyncio.to_thread(self._fail_sync, message, reason)

    async def size(self, tenant_id: str | None = None) -> int:
        return await asyncio.to_thread(self._size_sync, tenant_id)

    async def pending_count(self, tenant_id: str | None = None) -> int:
        return await asyncio.to_thread(self._pending_count_sync, tenant_id)

    async def completed_count(self, tenant_id: str | None = None) -> int:
        return await asyncio.to_thread(self._completed_count_sync, tenant_id)

    async def failed_count(self, tenant_id: str | None = None) -> int:
        return await asyncio.to_thread(self._failed_count_sync, tenant_id)

    async def dead_letter_count(self, tenant_id: str | None = None) -> int:
        return await asyncio.to_thread(self._dead_letter_count_sync, tenant_id)

    async def status(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._status_sync, job_id)

    async def heartbeat(self, message: JobMessage) -> None:
        return await asyncio.to_thread(self._heartbeat_sync, message)

    async def release(self, message: JobMessage) -> None:
        return await asyncio.to_thread(self._release_sync, message)

    async def requeue_stale(self, timeout_seconds: float = 300.0) -> list[JobMessage]:
        return await asyncio.to_thread(self._requeue_stale_sync, timeout_seconds)
