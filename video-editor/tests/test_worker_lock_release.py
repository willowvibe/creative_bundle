"""A worker that loses the per-job lock must release its claim (#298).

The old loop caught ``JobLockError`` in its generic handler and called
``queue.fail``, burning one retry per attempt on a job it never ran — and
overwriting the owner's error. This test holds the lock and asserts the claim
goes back to the queue untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from video_editor_cli import main
from video_editor_state.locking import job_lock
from video_editor_state.queue import InMemoryJobQueue


def test_a_held_job_lock_returns_the_claim_without_a_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = InMemoryJobQueue()
    monkeypatch.setattr(main, "_queue_instance", queue, raising=False)
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path))

    calls: list[str] = []

    def handler(ctx: object, payload: dict[str, object]) -> None:
        calls.append(str(getattr(ctx, "job_id", "?")))

    monkeypatch.setattr(main, "_HANDLERS", {"music": handler}, raising=False)

    # Fail fast on the held lock instead of waiting out job_lock's 5 s grace.
    from video_editor_cli.commands import queue as queue_commands

    monkeypatch.setattr(queue_commands, "job_lock", lambda path: job_lock(path, timeout=0.0))

    work_root = tmp_path / "work"
    output_root = tmp_path / "output"
    runner = CliRunner()
    enqueued = runner.invoke(
        main.app,
        [
            "enqueue",
            "music",
            "-j",
            "job-lock",
            "--payload",
            json.dumps({"work_root": str(work_root), "output_root": str(output_root)}),
        ],
    )
    assert enqueued.exit_code == 0, enqueued.output

    job_dir = work_root / "job-lock"
    job_dir.mkdir(parents=True, exist_ok=True)
    with job_lock(job_dir):
        result = runner.invoke(main.app, ["worker", "--max-jobs", "1"])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Processing music for job-lock" in result.output
    # The claim is back in the delayed bucket with its retry budget intact.
    released = queue._delayed["job-lock"]
    assert released.retries == 0
    assert queue._failed == []
    assert queue._completed == []
