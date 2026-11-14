"""Per-job lock file so two workers cannot render into one directory (A7)."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from pathlib import Path

try:  # pragma: no cover - Windows has no fcntl
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class JobLockError(RuntimeError):
    """Another process currently holds this job's lock."""


@contextlib.contextmanager
def job_lock(work_dir: Path | str, *, timeout: float = 5.0) -> Iterator[Path]:
    """Hold ``work_dir/.lock`` for the duration of a job.

    ``flock`` is released automatically when the process dies, so a crashed
    worker cannot leave a permanent lock. ``timeout=0`` checks and raises
    immediately; the default waits briefly for a finishing duplicate.
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    path = work / ".lock"
    handle = path.open("a+")
    try:
        if fcntl is not None:
            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise JobLockError(f"job lock is held: {path}") from None
                    time.sleep(0.05)
        yield path
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
