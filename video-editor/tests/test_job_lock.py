"""A7: two processes cannot hold one job's render lock."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_state.locking import JobLockError, job_lock


def test_a_second_context_cannot_take_the_same_job_lock(tmp_path: Path) -> None:
    with (
        job_lock(tmp_path / "work"),
        pytest.raises(JobLockError),
        job_lock(tmp_path / "work", timeout=0),
    ):
        pass


def test_the_lock_is_released(tmp_path: Path) -> None:
    with job_lock(tmp_path / "work", timeout=0):
        pass
    with job_lock(tmp_path / "work", timeout=0):
        pass
