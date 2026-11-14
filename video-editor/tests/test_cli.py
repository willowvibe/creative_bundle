"""Tests for the ``ve`` CLI commands.

Most skills and pipelines have their own dedicated test files. This file
covers the *command* surface — the typer CLI — specifically:
- argument validation
- error messages
- flag interactions

Session C added coverage for ``ve run`` b-roll flag handling (Bug #25).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.processors import JSONRenderer
from typer.testing import CliRunner
from video_editor_asset_ingestion.scene_assets import (
    isolation_request as _real_isolation_request,
)
from video_editor_asset_ingestion.scene_assets import (
    speech_to_speech_request as _real_speech_to_speech_request,
)
from video_editor_state.job import JobContext
from video_editor_state.queue import InMemoryJobQueue

from tests.conftest import _require_ffmpeg


@pytest.fixture
def cli_runner() -> CliRunner:
    """Standard typer CLI runner."""
    return CliRunner()


@pytest.fixture
def source_file(tmp_path: Path, tmp_media: Path) -> Path:
    """A single-file source path (not a directory)."""
    dest = tmp_path / "input.mp4"
    dest.write_bytes(tmp_media.read_bytes())
    return dest


@pytest.fixture
def transcript_file(tmp_path: Path) -> Path:
    """A minimal transcript JSON file."""
    transcript = {
        "language": "en",
        "word_timestamps": True,
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.95},
            {"word": "world", "start": 0.5, "end": 1.0, "probability": 0.95},
        ],
    }
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


@pytest.fixture
def fresh_queue(monkeypatch: pytest.MonkeyPatch) -> InMemoryJobQueue:
    """Reset the module-level queue singleton so tests don't leak state."""
    from video_editor_cli import main

    q = InMemoryJobQueue()
    monkeypatch.setattr(main, "_queue_instance", q, raising=False)
    return q


@pytest.fixture(autouse=True)
def _tmp_data_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make each test's ``tmp_path`` the configured data root (#278).

    Worker payloads in this file name roots under ``tmp_path``; treating that
    directory as the configured data root keeps the real path-containment check
    active without writing into the repository's ``projects/``/``outputs/``.
    Tests that exercise the default-root logic override these deliberately.
    """
    monkeypatch.setenv("VE_WORK_ROOT", str(tmp_path))
    monkeypatch.setenv("VE_OUTPUT_ROOT", str(tmp_path))


class TestRunCommandBrollProvider:
    """Bug #25 regression: --broll-provider on a file source should warn
    rather than silently no-op."""

    def test_broll_provider_warns_on_file_source(
        self,
        cli_runner: CliRunner,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """When --broll-provider is set on a file source (v2 path), emit a
        clear warning instead of silently ignoring the flag.
        """
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source_file),
                "-j",
                "broll-warn-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--transcript",
                str(transcript_file),
                "--broll-provider",
                "fal_ai",
            ],
        )
        assert result.exit_code == 0, result.output
        # Warning was printed to stderr.
        assert "broll-provider" in result.output.lower()
        assert "v2 skills pipeline" in result.output

    def test_no_broll_provider_no_warning(
        self,
        cli_runner: CliRunner,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """When --broll-provider is omitted, no warning is printed."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source_file),
                "-j",
                "no-broll-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--transcript",
                str(transcript_file),
            ],
        )
        # The pipeline may or may not succeed (it depends on the test
        # environment) but no b-roll warning should fire.
        assert "broll-provider" not in result.output.lower()


class TestRunCommandForceFlag:
    """Bug #115 regression: ``--force`` on a file source (v2 skills path)
    should warn instead of silently no-oping.
    """

    def test_force_flag_warns_on_file_source(
        self,
        cli_runner: CliRunner,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """When --force is set on a file source (v2 path), emit a clear warning."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source_file),
                "-j",
                "force-warn-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--transcript",
                str(transcript_file),
                "--force",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "--force" in result.output.lower()
        assert "v2 skills pipeline" in result.output

    def test_no_force_flag_no_warning(
        self,
        cli_runner: CliRunner,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """When --force is omitted, no force warning is printed."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(source_file),
                "-j",
                "no-force-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--transcript",
                str(transcript_file),
            ],
        )
        assert "v2 skills pipeline" not in result.output or "--force" not in result.output.lower()


class TestRunCommandSourceValidation:
    """Error paths for the ``ve run`` source argument."""

    def test_missing_source_errors(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A non-existent source path should exit with a clear error."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "run",
                str(tmp_path / "does_not_exist.mp4"),
                "-j",
                "missing-source-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 1
        assert "does not exist" in result.output


class TestEnqueueCommandValidation:
    """Cross-checks the enqueue path used by the worker layer."""

    def test_enqueue_missing_required_args_errors(
        self,
        cli_runner: CliRunner,
    ) -> None:
        """Enqueue without -j / --job-id should fail with a clean error,
        not a Python traceback (regression guard for the JSON traceback
        bug from Session A)."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "render",
                # missing -j / --job-id
            ],
        )
        # typer exits with non-zero when a required option is missing.
        assert result.exit_code != 0
        # No Python traceback in the output.
        assert "Traceback" not in result.output


class TestWorkerDispatchesJobs:
    """Bug #23 close: ``ve worker`` routes queued jobs to the matching
    pipeline / skill via the ``_HANDLERS`` registry. The previous
    stub simply dequeued and ``complete()``d every message, which
    made enqueue / worker / queue_size look end-to-end OK while
    silently dropping all real work. The dispatcher now invokes
    the handler matched on ``message.command`` and reports
    failures via ``queue.fail`` so a single bad job never kills
    the worker loop.
    """

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    @pytest.fixture
    def fake_handlers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, list[Any]]:
        """Replace ``_HANDLERS`` with one sync fake per command that
        records every call into a shared list keyed by command."""
        from video_editor_cli import main

        calls: dict[str, list[Any]] = {}

        def _make_recorder(command: str) -> Any:
            def _record(ctx: JobContext, payload: dict[str, Any]) -> None:
                calls.setdefault(command, []).append((ctx, payload))

            return _record

        registry: dict[str, Any] = {command: _make_recorder(command) for command in main._HANDLERS}
        monkeypatch.setattr(main, "_HANDLERS", registry, raising=False)
        return calls

    def _enqueue(
        self,
        cli_runner: CliRunner,
        command: str,
        job_id: str,
        payload: dict[str, Any] | None = None,
        work_root: Path | None = None,
        output_root: Path | None = None,
        tenant_id: str = "default",
        max_retries: int = 3,
    ) -> None:
        """Enqueue a job via the CLI. The caller is responsible for
        passing ``work_root`` and ``output_root`` so the worker's
        ``JobContext`` can ``ensure_dirs()`` without hitting ``/work``.

        ``max_retries`` is forwarded to the queue so tests that expect an
        immediate failure can set it to 0.
        """
        from video_editor_cli.main import app

        body = dict(payload or {})
        if work_root is not None:
            body.setdefault("work_root", str(work_root))
        if output_root is not None:
            body.setdefault("output_root", str(output_root))

        cli_runner.invoke(
            app,
            [
                "enqueue",
                command,
                "--job-id",
                job_id,
                "--tenant-id",
                tenant_id,
                "--payload",
                json.dumps(body),
                "--max-retries",
                str(max_retries),
            ],
        )

    def test_worker_processes_enqueue_drain_loop(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """Enqueue 2 jobs and run ``ve worker --max-jobs 2`` — both must complete."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(cli_runner, "music", "job-1", work_root=work_root, output_root=output_root)
        self._enqueue(
            cli_runner, "captions_v2", "job-2", work_root=work_root, output_root=output_root
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "2"])

        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 2
        assert len(fresh_queue._failed) == 0
        # Both handlers were called once.
        assert len(fake_handlers["music"]) == 1
        assert len(fake_handlers["captions_v2"]) == 1

    def test_worker_requeues_a_stale_job_before_dequeuing(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """#275: a job a crashed worker left in flight is run by the next worker."""
        from video_editor_cli.main import app

        self._enqueue(
            cli_runner,
            "music",
            "job-orphan",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        orphan = asyncio.run(fresh_queue.dequeue())
        assert orphan is not None
        fresh_queue._dequeue_times["job-orphan"] -= 3600

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1", "--stale-seconds", "900"])

        assert result.exit_code == 0, result.output
        assert "Requeued stale job job-orphan" in result.output
        assert len(fake_handlers["music"]) == 1
        assert len(fresh_queue._completed) == 1

    def test_worker_heartbeats_while_a_job_runs(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from video_editor_cli import main

        beats: list[str] = []

        async def record(message: Any) -> None:
            beats.append(message.job_id)

        async def slow_dispatch(queue: Any, message: Any, ctx: Any) -> str:
            await asyncio.sleep(0.2)
            await queue.complete(message)
            return "completed"

        monkeypatch.setattr(fresh_queue, "heartbeat", record)
        monkeypatch.setattr(main, "_dispatch_message", slow_dispatch)
        self._enqueue(
            cli_runner,
            "music",
            "job-long",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )

        # stale/3 = 0.05 s between beats, so a 0.2 s job beats several times.
        result = cli_runner.invoke(
            main.app, ["worker", "--max-jobs", "1", "--stale-seconds", "0.15"]
        )

        assert result.exit_code == 0, result.output
        assert beats and set(beats) == {"job-long"}

    def test_worker_dispatches_to_correct_handler(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """A queued ``plan`` job invokes the ``plan`` handler with the
        matching payload and a JobContext rooted at the payload's
        ``work_root`` / ``output_root`` (or the safe defaults)."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(
            cli_runner,
            "graphics",
            "job-plan",
            {
                "force": True,
                "config_path": str(tmp_path / "config.yaml"),
            },
            work_root=work_root,
            output_root=output_root,
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        assert "graphics" in fake_handlers and len(fake_handlers["graphics"]) == 1
        ctx, payload = fake_handlers["graphics"][0]
        assert ctx.job_id == "job-plan"
        assert ctx.tenant_id == "default"
        assert ctx.work_root == work_root
        assert ctx.output_root == output_root
        assert payload["force"] is True
        assert payload["config_path"] == str(tmp_path / "config.yaml")

    def test_worker_calls_fail_for_unknown_command(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """An unknown command is reported via ``queue.fail`` and the
        worker keeps running for the next job."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(
            cli_runner,
            "nonsense",
            "bad-job",
            work_root=work_root,
            output_root=output_root,
            max_retries=0,
        )
        self._enqueue(cli_runner, "music", "good-job", work_root=work_root, output_root=output_root)

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "2"])
        assert result.exit_code == 0, result.output

        # The bad job is in the failure list with a clear reason.
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "nonsense"
        assert "unknown_command" in reason
        # The good job still ran to completion.
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "music"
        # Nothing was dispatched to a handler for the bogus command.
        assert "nonsense" not in fake_handlers

    def test_worker_continues_after_handler_raises(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A handler that raises is reported via ``queue.fail``; the
        next queued job still runs to completion."""
        from video_editor_cli import main

        def _boom(_ctx: JobContext, _payload: dict[str, Any]) -> None:
            raise RuntimeError("simulated handler failure")

        def _ok(_ctx: JobContext, _payload: dict[str, Any]) -> None:
            return

        registry = dict(main._HANDLERS)
        registry["music"] = _boom
        registry["captions_v2"] = _ok
        monkeypatch.setattr(main, "_HANDLERS", registry, raising=False)

        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(
            cli_runner,
            "music",
            "boom-job",
            work_root=work_root,
            output_root=output_root,
            max_retries=0,
        )
        self._enqueue(
            cli_runner, "captions_v2", "ok-job", work_root=work_root, output_root=output_root
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "2"])
        assert result.exit_code == 0, result.output

        # The failing job was recorded; the next job still completed.
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "music"
        assert "simulated handler failure" in reason
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "captions_v2"

    def test_worker_handler_failure_increments_retries_once_not_twice(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """C2 regression: a single handler failure must increment
        ``retries`` by exactly 1, not 2. Previously the worker loop's
        "no terminal state" guard fired after ``_dispatch_message`` had
        already called ``queue.fail`` (sending the message to
        ``_delayed`` for retry), and called ``queue.fail`` a SECOND time
        — consuming 2 retries per failure and overwriting the real error
        reason with "dispatch did not complete or fail". With
        ``max_retries=3`` a job was dead-lettered after ~1 failure instead
        of 3.
        """
        from video_editor_cli import main
        from video_editor_cli.main import app

        def _boom(_ctx: JobContext, _payload: dict[str, Any]) -> None:
            raise RuntimeError("transient handler failure")

        registry = dict(main._HANDLERS)
        registry["music"] = _boom
        monkeypatch.setattr(main, "_HANDLERS", registry, raising=False)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        # max_retries=3 so a single failure retries (retries 0->1) rather
        # than dead-lettering. The bug would make it 0->2 in one dequeue.
        self._enqueue(
            cli_runner,
            "music",
            "retry-job",
            work_root=work_root,
            output_root=output_root,
            max_retries=3,
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # The message was retried (went to _delayed), not dead-lettered.
        assert "retry-job" in fresh_queue._delayed
        retried = fresh_queue._delayed["retry-job"]
        # The crux of C2: a single handler failure consumed exactly ONE
        # retry. The bug would have produced retries == 2.
        assert retried.retries == 1, (
            f"expected retries == 1 after one failure, got {retried.retries} "
            "(the worker loop is double-failing retried messages again)"
        )
        # It was not also recorded as a terminal failure.
        assert len(fresh_queue._failed) == 0
        # And it is not orphaned in _in_flight.
        assert "retry-job" not in fresh_queue._in_flight

    def test_worker_loop_fails_orphaned_job_when_ensure_dirs_raises(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """C3 regression: when ``ensure_dirs()`` (or anything before
        dispatch) raises inside the worker loop, the broad ``except`` must
        fail the dequeued message through ``queue.fail`` — otherwise the
        message is orphaned in ``_in_flight`` forever (not in pending,
        completed, failed, or dead-letter).
        """
        from video_editor_cli.main import app
        from video_editor_state import job as state_job

        def _raise(_self: JobContext) -> None:
            raise PermissionError("simulated unwritable work root")

        monkeypatch.setattr(state_job.JobContext, "ensure_dirs", _raise)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(
            cli_runner,
            "music",
            "orphan-job",
            work_root=work_root,
            output_root=output_root,
            max_retries=0,
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # The job must have been failed through the queue (lands in
        # _failed / _dead_letter), not orphaned in _in_flight.
        assert len(fresh_queue._failed) == 1, fresh_queue._failed
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.job_id == "orphan-job"
        assert "simulated unwritable work root" in reason
        assert "orphan-job" not in fresh_queue._in_flight
        assert len(fresh_queue._dead_letter) == 1

    def test_worker_does_not_print_stub_warning(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """Regression guard: now that the worker is real, the old
        "this is a stub" warning must be gone."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(cli_runner, "music", "job-1", work_root=work_root, output_root=output_root)
        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert "stub" not in result.output.lower()

    def test_worker_calls_ensure_dirs_once_per_dequeue_not_per_handler(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Bug #29 regression: ``ensure_dirs()`` must run once per
        dequeued message in the worker loop, not once per handler
        invocation. A worker draining 100 jobs against the same
        context only needs one stat/mkdir pass per message.

        We patch ``JobContext.ensure_dirs`` with a counter and
        enqueue 3 jobs. The dispatcher does NOT call ensure_dirs
        itself, so the count must equal 3 (one per dequeue), not
        3 * N_handlers.
        """
        from video_editor_cli import main as cli_main
        from video_editor_cli.main import app
        from video_editor_state import job as state_job

        calls: list[str] = []

        def _counted_ensure(self: Any) -> None:
            calls.append(self.job_id)
            Path(str(self.work_root)).mkdir(parents=True, exist_ok=True)
            Path(str(self.output_root)).mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr(state_job.JobContext, "ensure_dirs", _counted_ensure)

        # Use a no-op handler so the count isolates the worker loop's
        # ensure_dirs; the real skill handlers call ensure_dirs themselves.
        registry = dict(cli_main._HANDLERS)
        registry["noop"] = lambda _ctx, _payload: None
        monkeypatch.setattr(cli_main, "_HANDLERS", registry, raising=False)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(cli_runner, "noop", "job-1", work_root=work_root, output_root=output_root)
        self._enqueue(cli_runner, "noop", "job-2", work_root=work_root, output_root=output_root)
        self._enqueue(cli_runner, "noop", "job-3", work_root=work_root, output_root=output_root)

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "3"])
        assert result.exit_code == 0, result.output

        # Exactly one ensure_dirs per dequeue, never per handler.
        assert len(calls) == 3, f"expected 3 ensure_dirs calls, got {len(calls)}: {calls}"
        assert calls == ["job-1", "job-2", "job-3"]

    def test_worker_warns_when_payload_omits_work_root_or_output_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #30 regression: a payload that omits ``work_root`` /
        ``output_root`` would silently fall back to ``/work`` /
        ``/output`` and PermissionError on developer machines. The
        worker must emit a loud stderr warning before dispatch so
        the user notices, matching the Session C ``--broll-provider``
        warning pattern.

        We mock ``_build_job_context`` to return a context rooted
        at ``tmp_path`` so the test can run without ``/work`` /
        ``/output`` permission; the warning fires regardless of
        the substitution.
        """
        from video_editor_cli import main
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"

        def _stub_build(message: Any) -> Any:
            """Bypass the /work default by returning a ctx rooted at tmp_path."""
            from video_editor_state.job import JobContext

            return JobContext(
                job_id=message.job_id,
                work_root=work_root,
                output_root=output_root,
                tenant_id=message.tenant_id,
            )

        monkeypatch.setattr(main, "_build_job_context", _stub_build)

        # Enqueue an "music" job with NO work_root / output_root
        # in the payload (the legacy v1 fallback path).
        cli_runner.invoke(
            app,
            [
                "enqueue",
                "music",
                "--job-id",
                "fallback-job",
                "--tenant-id",
                "default",
                "--payload",
                json.dumps({"force": True}),
            ],
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # #260: the worker no longer falls back to the container-only
        # ``/work`` / ``/output``, so this is an informational note naming the
        # roots it will actually use — not a permission-error warning.
        out = result.output.lower()
        assert "omits" in out
        assert "work_root" in out
        assert "output_root" in out
        assert "/work" not in out
        # The handler still ran (note, not error).
        assert len(fresh_queue._completed) == 1
        assert len(fresh_queue._failed) == 0

    def test_worker_no_warning_when_payload_has_both_paths(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        fake_handlers: dict[str, list[Any]],
        tmp_path: Path,
    ) -> None:
        """Regression guard: the missing-paths warning must NOT fire
        when the payload includes both ``work_root`` and
        ``output_root``. Otherwise every well-formed job would
        spam stderr.
        """
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(cli_runner, "music", "job-1", work_root=work_root, output_root=output_root)

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # No "missing work_root" warning.
        assert "missing" not in result.output.lower() or ("work_root" not in result.output)


class TestHandlerSourceValidation:
    """The worker handler functions raise clear ``ValueError``s when
    a payload is missing required fields, instead of a raw ``KeyError``
    that's hard to diagnose in a queue worker context."""

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def test_run_handler_rejects_missing_source(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #79: a ``run`` payload without ``source`` is rejected at
        enqueue time with a clear payload-validation error mentioning
        ``source`` — before it ever reaches a handler."""
        from video_editor_cli.main import app

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "run-no-src",
                "--payload",
                json.dumps(
                    {
                        "work_root": str(tmp_path / "work"),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 1, enqueue_result.output
        assert "payload validation failed" in enqueue_result.output.lower()
        assert "source" in enqueue_result.output
        # Nothing was queued.
        assert len(fresh_queue._pending) == 0

    def test_rough_cut_handler_rejects_missing_source(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #79: a ``rough_cut`` payload without ``source`` is rejected
        at enqueue time."""
        from video_editor_cli.main import app

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "rough_cut",
                "-j",
                "rc-no-src",
                "--payload",
                json.dumps(
                    {
                        "work_root": str(tmp_path / "work"),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 1, enqueue_result.output
        assert "payload validation failed" in enqueue_result.output.lower()
        assert "source" in enqueue_result.output
        assert len(fresh_queue._pending) == 0

    def test_run_handler_rejects_source_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """C4 regression: a ``run`` payload whose ``source`` (or
        ``transcript_path`` / ``config_path``) points outside the job's
        work/output directories is rejected as a path-traversal guard.
        In a multi-tenant worker this prevents tenant A from reading or
        copying tenant B's files (``transcript_path`` is especially
        dangerous — ``RawFootagePipeline.transcribe`` does
        ``shutil.copyfile(transcript_path, target_path)``, an
        arbitrary-file-read primitive)."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "run-escape"
        job_dir.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "other_tenant_video.mp4"
        outside.write_bytes(b"not your file")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "run-escape",
                "--payload",
                json.dumps(
                    {
                        "source": str(outside),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "run"
        assert "allowed roots" in reason.lower()

    def test_rough_cut_handler_rejects_source_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """C4 regression: a ``rough_cut`` payload whose ``source`` points
        outside the job's work/output directories is rejected as a
        path-traversal guard (same class as the ``run`` handler above)."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "rc-escape"
        job_dir.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "other_tenant_video.mp4"
        outside.write_bytes(b"not your file")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "rough_cut",
                "-j",
                "rc-escape",
                "--payload",
                json.dumps(
                    {
                        "source": str(outside),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "rough_cut"
        assert "allowed roots" in reason.lower()


class TestBriefCommand:
    """The ``ve brief`` command copies a project.md into the job's
    work directory and is also available as a worker command for
    async dispatch. Covers the most common failure modes so a user
    who mistypes the path gets a clear error."""

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def _write_source_md(self, tmp_path: Path) -> Path:
        md = tmp_path / "project.md"
        md.write_text(
            "---\ntitle: Saju Reel\naudience: Newcomers\ntone: Calm\n---\nEditorial notes here.\n",
            encoding="utf-8",
        )
        return md

    def test_brief_command_writes_project_md(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """``ve brief <job-id> --from-md <path>`` copies the source
        project.md into <work_dir>/project.md."""
        from video_editor_cli.main import app

        src = self._write_source_md(tmp_path)
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        result = cli_runner.invoke(
            app,
            [
                "brief",
                "brief-1",
                "--from-md",
                str(src),
                "--work-root",
                str(work_root),
                "--output-root",
                str(output_root),
            ],
        )
        assert result.exit_code == 0, result.output
        target = work_root / "brief-1" / "project.md"
        assert target.exists()
        text = target.read_text(encoding="utf-8")
        assert "Saju Reel" in text
        assert "Editorial notes" in text

    def test_brief_command_errors_when_source_missing(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A bad --from-md path produces a clear error and a non-zero
        exit code, not a stack trace."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "brief",
                "brief-2",
                "--from-md",
                str(tmp_path / "does-not-exist.md"),
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        # Clear, user-facing error (not a Python traceback).
        assert "does-not-exist" in result.output or "not found" in result.output.lower()

    def test_brief_handler_routes_through_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """The ``brief`` command is also a worker command. Enqueuing
        ``ve enqueue brief <job> --from-md <path>`` and running
        ``ve worker`` produces the same project.md on disk. The worker
        handler requires the source path to be inside the job's work or
        output directory.
        """
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "brief-3"
        job_dir.mkdir(parents=True, exist_ok=True)
        src = job_dir / "source.md"
        src.write_text(
            "---\ntitle: Saju Reel\naudience: Newcomers\ntone: Calm\n---\nEditorial notes here.\n",
            encoding="utf-8",
        )
        # Enqueue (this exercises the same _HANDLERS dispatch path).
        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "brief",
                "-j",
                "brief-3",
                "--payload",
                json.dumps(
                    {
                        "from_md": str(src),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        # Drain — verify the worker dispatches "brief" without crashing.
        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        target = work_root / "brief-3" / "project.md"
        assert target.exists(), f"project.md not written by worker; output was {result.output}"
        text = target.read_text(encoding="utf-8")
        assert "Saju Reel" in text

    def test_brief_handler_rejects_directory_from_md(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """A ``from_md`` payload that points to a directory (not a file)
        must be rejected with a clear ``ValueError``, not a confusing
        ``IsADirectoryError`` from ``shutil.copy2``. A bad payload
        fails the job so the queue records the failure rather than
        silently dropping it.
        """
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "brief-bad"
        job_dir.mkdir(parents=True, exist_ok=True)
        # A directory that exists, still inside the allowed work root.
        bad_dir = job_dir / "wrong_kind"
        bad_dir.mkdir()

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "brief",
                "-j",
                "brief-bad",
                "--payload",
                json.dumps(
                    {
                        "from_md": str(bad_dir),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        # The bad job was recorded as a failure (not silently dropped).
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "brief"
        assert "from_md" in reason or "directory" in reason.lower()

    def test_brief_handler_rejects_path_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """A ``from_md`` payload pointing outside the job's work/output
        directories is rejected as a path-traversal guard."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "brief-escape"
        job_dir.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "other_tenant.md"
        outside.write_text("---\ntitle: Other\n---\n", encoding="utf-8")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "brief",
                "-j",
                "brief-escape",
                "--payload",
                json.dumps(
                    {
                        "from_md": str(outside),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "brief"
        assert "allowed roots" in reason.lower()


class TestWorkerDispatchAwaitables:
    """Bug #118 regression: ``_dispatch_message`` must await any awaitable
    returned by a handler, including ``asyncio.Task`` objects, not only
    bare coroutines.
    """

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def test_worker_awaits_task_returned_by_handler(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A handler that returns an ``asyncio.Task`` is awaited to completion."""
        from video_editor_cli import main
        from video_editor_cli.main import app

        task_ran = False

        async def _async_handler(_ctx: Any, _payload: Any) -> None:
            nonlocal task_ran
            task_ran = True

        # Handlers that return awaitables are async handlers: a sync handler
        # now runs off the loop (#298) and could not create a task there.
        async def _handler(ctx: Any, payload: Any) -> asyncio.Task[Any]:
            return asyncio.create_task(_async_handler(ctx, payload))

        monkeypatch.setattr(main, "_HANDLERS", {"task-test": _handler}, raising=False)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        cli_runner.invoke(
            app,
            [
                "enqueue",
                "task-test",
                "-j",
                "task-job",
                "--payload",
                json.dumps(
                    {
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert task_ran is True
        assert len(fresh_queue._completed) == 1


class TestRealPipelineRunsThroughEnqueueWorker:
    """Session Q: the Session K dispatcher is verified end-to-end with
    fake handlers, but no test runs a real skill pipeline through the
    full ``ve enqueue`` → ``ve worker`` flow. This class closes that
    gap by driving a real ``SkillsPipeline.run`` via the queue and
    asserting the export artifacts land on disk.

    No fakes, no ``monkeypatch`` of ``_HANDLERS`` — the real handlers
    (and therefore the real ``RoughCutSkill``, ``GraphicsSkill``,
    ``CaptionsSkill``, ``ExportSkill``) are exercised. The synthetic
    3-second ``tmp_media`` clip + the minimal ``transcript_file`` JSON
    keep the test fast (~5-7s) and cost-free.
    """

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so the assertions
        see the messages this test enqueued."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def test_run_handler_executes_full_skills_pipeline_via_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """Enqueue a real ``run`` job, drain via ``ve worker``, and
        assert the v2 ``SkillsPipeline`` produced the expected
        export artifacts through the dispatcher's ``JobContext``."""
        from video_editor_cli.main import app

        job_id = "e2e-run"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"

        # C4: the worker handler now contains payload paths to the job's
        # work/output directories (multi-tenant path-traversal guard). The
        # source + transcript must live inside the job's work_dir so they
        # pass ``_resolve_payload_path``.
        work_dir = work_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        in_source = work_dir / "input.mp4"
        in_source.write_bytes(source_file.read_bytes())
        in_transcript = work_dir / "transcript.json"
        in_transcript.write_bytes(transcript_file.read_bytes())

        # 1. Enqueue via the real ``ve enqueue`` CLI — exercises the
        # JSON-parse + queue.enqueue path.
        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "source": str(in_source),
                        "transcript_path": str(in_transcript),
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                        "preset": "short_explainer",
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        # 2. Drain via the real ``ve worker`` CLI — exercises the
        # queue.dequeue → _dispatch_message → _handle_run →
        # SkillsPipeline.run → ExportSkill path.
        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # 3. The job completed (not failed) and the queue recorded it.
        assert len(fresh_queue._completed) == 1, fresh_queue._failed
        assert fresh_queue._completed[0].command == "run"
        assert fresh_queue._completed[0].job_id == job_id
        assert len(fresh_queue._failed) == 0

        # 4. The real SkillsPipeline wrote through the dispatcher's
        # JobContext (proving the ctx was constructed with the
        # payload's work_root / output_root, not /work / /output).
        final = output_root / job_id / "final.mp4"
        report = output_root / job_id / "report.md"
        assert final.exists(), f"final.mp4 missing at {final}"
        assert final.stat().st_size > 0, "final.mp4 is empty"
        assert report.exists(), f"report.md missing at {report}"

    def test_run_handler_fails_loudly_when_source_missing(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        source_file: Path,
        transcript_file: Path,
        tmp_path: Path,
    ) -> None:
        """Bug #79 moves missing-field validation to enqueue time, so a
        bad ``run`` job must reach the worker with a syntactically valid
        payload. We use a non-existent source path (passes Pydantic,
        fails inside the real pipeline) to verify that the worker loop
        records the failure and still completes the next queued job."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        # C4: place both sources inside each job's work_dir so they pass the
        # path-traversal guard. ``bad_source`` is inside the work_dir but
        # does not exist on disk — it passes ``_resolve_payload_path`` (the
        # resolved path is contained) and then fails inside the real
        # pipeline, preserving the test's "fails loudly, worker survives"
        # intent.
        bad_work_dir = work_root / "bad-run"
        bad_work_dir.mkdir(parents=True, exist_ok=True)
        bad_source = bad_work_dir / "does_not_exist.mp4"

        # 1. Enqueue a bad run job (source path that does not exist) then a
        # good one. Both payloads pass the typed enqueue schema.
        bad_enqueue = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "bad-run",
                "--payload",
                json.dumps(
                    {
                        "source": str(bad_source),
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                        "preset": "short_explainer",
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert bad_enqueue.exit_code == 0, bad_enqueue.output
        good_work_dir = work_root / "good-run"
        good_work_dir.mkdir(parents=True, exist_ok=True)
        good_source = good_work_dir / "input.mp4"
        good_source.write_bytes(source_file.read_bytes())
        good_transcript = good_work_dir / "transcript.json"
        good_transcript.write_bytes(transcript_file.read_bytes())
        cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "good-run",
                "--payload",
                json.dumps(
                    {
                        "source": str(good_source),
                        "transcript_path": str(good_transcript),
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                        "preset": "short_explainer",
                    }
                ),
            ],
        )

        # 2. Drain both. The bad one fails, the good one completes.
        result = cli_runner.invoke(app, ["worker", "--max-jobs", "2"])
        assert result.exit_code == 0, result.output

        # 3. The bad job is in ``_failed`` with a clear reason.
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "run"
        assert bad_msg.job_id == "bad-run"
        assert reason  # any actionable failure reason is acceptable

        # 4. The good job still completed — the worker loop survived
        # the bad payload.
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "run"
        assert fresh_queue._completed[0].job_id == "good-run"

    def test_brief_handler_copies_project_md_via_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #106: the real ``brief`` handler runs through ``ve worker`` and
        writes a parsed project.md into the job work dir."""
        from video_editor_cli.main import app

        job_id = "e2e-brief"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        src_md = job_dir / "source.md"
        src_md.write_text("# Saju Reel\n\nEditorial notes", encoding="utf-8")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "brief",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "from_md": str(src_md),
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "brief"

        target = job_dir / "project.md"
        assert target.exists(), f"project.md missing at {target}"
        text = target.read_text(encoding="utf-8")
        assert "Saju Reel" in text

    def test_align_handler_writes_alignment_json_via_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #106: the real ``align`` handler runs through ``ve worker`` and
        writes an alignment JSON using the stub provider.
        """
        _require_ffmpeg()
        from video_editor_cli.main import app

        job_id = "e2e-align"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "align",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "text": "hello world this is a test",
                        "provider": "stub_alignment",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "align"

        alignment_json = job_dir / "transcript" / "alignment.json"
        assert alignment_json.exists(), f"alignment.json missing at {alignment_json}"
        payload = json.loads(alignment_json.read_text(encoding="utf-8"))
        assert payload["transcript"] == "hello world this is a test"
        assert len(payload["words"]) == 6


class TestMusicGenerateCli:
    """Session T: ``ve music-generate`` writes a track to ``<work>/music/``."""

    def test_music_generate_with_stub_provider(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stub music generation through the CLI produces a real MP3 on disk."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "music-generate",
                "music-job",
                "--prompt",
                "calm lo-fi, 5s, no vocals",
                "--music-provider",
                "stub_music",
                "--music-duration-seconds",
                "1",  # keep the test fast
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        # The CLI writes the generated track to the canonical location.
        out_path = tmp_path / "work" / "music-job" / "music" / "generated_music.mp3"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.stat().st_size > 0


class TestAlignCli:
    """Session V: ``ve align`` writes a forced-alignment JSON to ``<work>/transcript/``."""

    def test_align_with_stub_provider(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Stub alignment through the CLI produces a real JSON on disk."""
        from video_editor_cli.main import app

        # Synthesize a small audio file to align against.
        audio_path = tmp_path / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "align",
                "align-job",
                "--audio",
                str(audio_path),
                "--text",
                "hello world this is a test",
                "--align-provider",
                "stub_alignment",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        # The CLI writes the alignment JSON to the canonical location.
        out_path = tmp_path / "work" / "align-job" / "transcript" / "alignment.json"
        assert out_path.exists(), f"expected {out_path} to exist"
        payload = json.loads(out_path.read_text())
        assert payload["transcript"] == "hello world this is a test"
        assert payload["language"] == "en"
        assert len(payload["words"]) == 6

    def test_align_missing_audio_exits(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A non-existent audio file fails fast (exit code != 0)."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "align",
                "align-job",
                "--audio",
                str(tmp_path / "no_such_audio.mp3"),
                "--text",
                "hello world",
                "--align-provider",
                "stub_alignment",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "Audio file not found" in result.output

    def test_align_handler_rejects_audio_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """An ``audio`` path outside the job's directories is rejected in
        the worker handler as a path-traversal guard."""
        from video_editor_cli.main import app

        outside_audio = tmp_path / "outside.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(outside_audio),
            ],
            check=True,
            capture_output=True,
        )

        work_root = tmp_path / "work"
        job_dir = work_root / "align-bad"
        job_dir.mkdir(parents=True, exist_ok=True)
        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "align",
                "-j",
                "align-bad",
                "--payload",
                json.dumps(
                    {
                        "audio": str(outside_audio),
                        "text": "hello world",
                        "provider": "stub_alignment",
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "align"
        assert "allowed roots" in reason.lower()


class TestAudioIsolateCli:
    """Session Y: ``ve audio-isolate`` writes isolated audio to ``<work>/audio/``."""

    def test_audio_isolate_with_stub_provider(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Stub isolation through the CLI produces a real MP3 on disk."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-job",
                "--audio",
                str(audio_path),
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / "work" / "isolate-job" / "audio" / "isolated.mp3"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.stat().st_size > 0

    def test_audio_isolate_missing_audio_exits(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A non-existent audio file fails fast."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-missing",
                "--audio",
                str(tmp_path / "no_such_audio.mp3"),
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "Audio file not found" in result.output

    def test_audio_isolate_wav_output(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bug #175: ``--output-name isolated.wav`` produces a real WAV file."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-wav-job",
                "--audio",
                str(audio_path),
                "--output-name",
                "isolated.wav",
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / "work" / "isolate-wav-job" / "audio" / "isolated.wav"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.read_bytes().startswith(b"RIFF")

    def test_audio_isolate_handler_via_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """The real ``audio_isolate`` handler runs through ``ve worker``."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        job_id = "e2e-isolate"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio_isolate",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "provider": "stub_isolation",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "audio_isolate"

        isolated_mp3 = job_dir / "audio" / "isolated.mp3"
        assert isolated_mp3.exists(), f"isolated.mp3 missing at {isolated_mp3}"

    def test_audio_isolate_output_name_rejects_traversal(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bug #170: a traversal output-name is rejected before touching disk."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-traversal",
                "--audio",
                str(audio_path),
                "--output-name",
                "../../tmp/pwned.mp3",
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "plain filename" in str(result.exception)

    def test_audio_isolate_handler_rejects_audio_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #183: worker handler rejects an audio path outside the job roots."""
        from video_editor_cli.main import app

        job_id = "isolate-bad-path"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        outside_audio = tmp_path / "outside.mp3"
        outside_audio.write_bytes(b"fake")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio_isolate",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(outside_audio),
                        "provider": "stub_isolation",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "audio_isolate"
        assert "allowed roots" in reason.lower()

    def test_audio_isolate_handler_rejects_directory(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #182: the worker handler rejects a directory passed as audio."""
        from video_editor_cli.main import app

        job_id = "isolate-dir"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True)
        audio_dir = job_dir / "audio_dir"
        audio_dir.mkdir()

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio_isolate",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_dir),
                        "provider": "stub_isolation",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "audio_isolate"
        assert "directory" in reason.lower()

    def test_audio_isolate_expands_tilde_audio_path(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #181: a quoted ``--audio '~/x.mp3'`` is expanded before the
        existence check, so it resolves the same way an unquoted shell path
        would (Typer's ``Path`` type does not expand ``~`` on its own)."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        # Point HOME at the temp dir so ``~`` resolves inside the sandbox.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))

        audio_path = fake_home / "tilde_audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-tilde",
                "--audio",
                "~/tilde_audio.mp3",
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "work" / "isolate-tilde" / "audio" / "isolated.mp3").exists()

    def test_audio_isolate_file_format_passthrough(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #175: an explicit ``--file-format`` reaches the request
        builder (not just the output-name extension). Captures the
        ``isolation_request`` kwargs via a wrapper and asserts the explicit
        hint is forwarded."""
        _require_ffmpeg()
        import video_editor_cli.main as cli_main
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        captured: dict[str, Any] = {}
        original = _real_isolation_request

        def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(cli_main, "isolation_request", _spy)

        result = cli_runner.invoke(
            app,
            [
                "audio-isolate",
                "isolate-ff-job",
                "--audio",
                str(audio_path),
                "--output-name",
                "isolated.mp3",
                "--file-format",
                "pcm_s16le_16",
                "--isolate-provider",
                "stub_isolation",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        # The explicit hint is forwarded; ``isolation_request`` reconciles
        # it with the ``.mp3`` extension (``pcm_s16le_16`` is a valid value
        # so it survives reconcile).
        assert captured.get("file_format") == "pcm_s16le_16"
        assert captured.get("output_ext") == "mp3"

    def test_audio_isolate_payload_preserves_file_format(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #175 (worker half): an enqueued ``audio_isolate`` payload
        with ``file_format`` survives the typed schema and is forwarded to
        ``isolation_request`` by the worker handler."""
        _require_ffmpeg()
        import video_editor_cli.main as cli_main
        from video_editor_cli.main import app

        job_id = "isolate-ff-worker"
        work_root = tmp_path / "work"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        captured: dict[str, Any] = {}
        original = _real_isolation_request

        def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(cli_main, "isolation_request", _spy)
        from video_editor_asset_ingestion import scene_assets as _sa

        monkeypatch.setattr(_sa, "isolation_request", _spy)

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio_isolate",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "provider": "stub_isolation",
                        "file_format": "pcm_s16le_16",
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert captured.get("file_format") == "pcm_s16le_16"


class TestSpeechToSpeechCli:
    """Session Z: ``ve speech-to-speech`` writes converted audio to ``<work>/audio/``."""

    def test_speech_to_speech_with_stub_provider(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Stub STS through the CLI produces a real MP3 on disk."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-job",
                "--audio",
                str(audio_path),
                "--voice-id",
                "stub-voice",
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / "work" / "sts-job" / "audio" / "converted.mp3"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.stat().st_size > 0

    def test_speech_to_speech_missing_audio_exits(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A non-existent audio file fails fast."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-missing",
                "--audio",
                str(tmp_path / "no_such_audio.mp3"),
                "--voice-id",
                "stub-voice",
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "Audio file not found" in result.output

    def test_speech_to_speech_handler_via_worker(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """The real ``speech_to_speech`` handler runs through ``ve worker``."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        job_id = "e2e-sts"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "speech_to_speech",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "voice_id": "stub-voice",
                        "provider": "stub_speech_to_speech",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "speech_to_speech"

        converted_mp3 = job_dir / "audio" / "converted.mp3"
        assert converted_mp3.exists(), f"converted.mp3 missing at {converted_mp3}"

    def test_speech_to_speech_output_name_rejects_traversal(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bug #170: a traversal output-name is rejected before touching disk."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-traversal",
                "--audio",
                str(audio_path),
                "--voice-id",
                "stub-voice",
                "--output-name",
                "../../tmp/pwned.mp3",
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "plain filename" in str(result.exception)

    def test_speech_to_speech_handler_rejects_audio_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #183: worker handler rejects an audio path outside the job roots."""
        from video_editor_cli.main import app

        job_id = "sts-bad-path"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        outside_audio = tmp_path / "outside.mp3"
        outside_audio.write_bytes(b"fake")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "speech_to_speech",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(outside_audio),
                        "voice_id": "stub-voice",
                        "provider": "stub_speech_to_speech",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "speech_to_speech"
        assert "allowed roots" in reason.lower()

    def test_speech_to_speech_handler_rejects_directory(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #182: the worker handler rejects a directory passed as audio."""
        from video_editor_cli.main import app

        job_id = "sts-dir"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True)
        audio_dir = job_dir / "audio_dir"
        audio_dir.mkdir()

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "speech_to_speech",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_dir),
                        "voice_id": "stub-voice",
                        "provider": "stub_speech_to_speech",
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "speech_to_speech"
        assert "directory" in reason.lower()

    def test_speech_to_speech_wav_output(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bug #180: ``--output-name converted.wav`` produces a real WAV file."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-wav-job",
                "--audio",
                str(audio_path),
                "--voice-id",
                "stub-voice",
                "--output-name",
                "converted.wav",
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / "work" / "sts-wav-job" / "audio" / "converted.wav"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.read_bytes().startswith(b"RIFF")

    def test_speech_to_speech_handler_honors_output_format_and_seed(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Bug #180: worker handler passes output_format, seed, voice_settings."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        job_id = "e2e-sts-options"
        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "speech_to_speech",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "voice_id": "stub-voice",
                        "provider": "stub_speech_to_speech",
                        "output_name": "converted.wav",
                        "output_format": "pcm_44100",
                        "seed": 42,
                        "stability": 0.75,
                        "similarity_boost": 0.85,
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output
        assert len(fresh_queue._completed) == 1
        assert fresh_queue._completed[0].command == "speech_to_speech"

        converted_wav = job_dir / "audio" / "converted.wav"
        assert converted_wav.exists(), f"converted.wav missing at {converted_wav}"
        assert converted_wav.read_bytes().startswith(b"RIFF")

    def test_speech_to_speech_uses_config_defaults(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Bug #187: CLI falls back to provider default_voice/default_model from config.yaml."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        audio_path = tmp_path / "input.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
assets:
  providers:
    stub_speech_to_speech:
      default_voice: cfg-voice
      default_model: cfg-model
"""
        )

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-config-job",
                "--audio",
                str(audio_path),
                "--config",
                str(config_path),
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "cfg-voice" in result.output
        out_path = tmp_path / "work" / "sts-config-job" / "audio" / "converted.mp3"
        assert out_path.exists(), f"expected {out_path} to exist"
        assert out_path.stat().st_size > 0

    def test_speech_to_speech_payload_preserves_all_options(self) -> None:
        """Bug #180 (schema half): the enqueue schema must not drop
        ``output_format``/``output_ext``/``seed``/``stability``/
        ``similarity_boost``. Previously Pydantic ``extra='ignore'`` silently
        stripped them, so a queued STS job could never set them even though
        the sync command and worker handler both supported them."""
        from video_editor_cli.main import _SpeechToSpeechPayload

        p = _SpeechToSpeechPayload.model_validate(
            {
                "audio": "a.mp3",
                "voice_id": "v",
                "output_format": "pcm_44100",
                "output_ext": "wav",
                "seed": 42,
                "stability": 0.75,
                "similarity_boost": 0.85,
            }
        )
        dump = p.model_dump()
        assert dump["output_format"] == "pcm_44100"
        assert dump["output_ext"] == "wav"
        assert dump["seed"] == 42
        assert dump["stability"] == 0.75
        assert dump["similarity_boost"] == 0.85

    def test_speech_to_speech_worker_forwards_all_payload_options(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #180 (worker half): now that the schema preserves them, the
        worker handler must forward ``output_format``, ``seed``, and
        ``voice_settings`` (assembled from stability/similarity_boost) to
        ``speech_to_speech_request``. A wrapper spies on the call kwargs."""
        _require_ffmpeg()
        import video_editor_cli.main as cli_main
        from video_editor_cli.main import app

        job_id = "e2e-sts-forward"
        work_root = tmp_path / "work"
        job_dir = work_root / job_id
        job_dir.mkdir(parents=True)
        audio_path = job_dir / "audio.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        captured: dict[str, Any] = {}
        original = _real_speech_to_speech_request

        def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(cli_main, "speech_to_speech_request", _spy)
        from video_editor_asset_ingestion import scene_assets as _sa

        monkeypatch.setattr(_sa, "speech_to_speech_request", _spy)

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "speech_to_speech",
                "-j",
                job_id,
                "--payload",
                json.dumps(
                    {
                        "audio": str(audio_path),
                        "voice_id": "stub-voice",
                        "provider": "stub_speech_to_speech",
                        "output_name": "converted.wav",
                        "output_format": "pcm_44100",
                        "seed": 42,
                        "stability": 0.75,
                        "similarity_boost": 0.85,
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output

        # The forwarded request carries the preserved options.
        assert captured.get("output_format") == "pcm_44100"
        assert captured.get("seed") == 42
        voice_settings = captured.get("voice_settings") or {}
        assert voice_settings.get("stability") == 0.75
        assert voice_settings.get("similarity_boost") == 0.85
        assert captured.get("output_ext") == "wav"

    def test_speech_to_speech_expands_tilde_audio_path(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #181: ``--audio '~/x.mp3'`` is expanded before the existence
        check for the speech-to-speech command too."""
        _require_ffmpeg()
        from video_editor_cli.main import app

        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setenv("USERPROFILE", str(fake_home))

        audio_path = fake_home / "tilde_sts.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=220:duration=1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
        )

        result = cli_runner.invoke(
            app,
            [
                "speech-to-speech",
                "sts-tilde",
                "--audio",
                "~/tilde_sts.mp3",
                "--voice-id",
                "stub-voice",
                "--sts-provider",
                "stub_speech_to_speech",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "work" / "sts-tilde" / "audio" / "converted.mp3").exists()


class TestPayloadFilenameValidation:
    """C5 regression: payload filename fields (``output_name``,
    ``plan_name``, ``base_video``) must be plain basenames. A value
    containing directory separators or parent-directory references would
    escape the intended directory and permit arbitrary file writes/reads.
    """

    def _ctx(self, tmp_path: Path) -> JobContext:
        ctx = JobContext(
            job_id="payload-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        ctx.ensure_dirs()
        return ctx

    def test_safe_filename_rejects_traversal_attempts(self) -> None:
        from video_editor_cli.main import _safe_filename

        with pytest.raises(ValueError, match="parent references"):
            _safe_filename("../../etc/cron.d/evil.mp4")
        with pytest.raises(ValueError, match="path separators"):
            _safe_filename("sub/dir.mp4")
        with pytest.raises(ValueError, match="path separators"):
            _safe_filename("sub\\dir.mp4")
        with pytest.raises(ValueError, match="parent references"):
            _safe_filename("..")
        with pytest.raises(ValueError, match="non-empty string"):
            _safe_filename("", "output_name")

    def test_handle_captions_v2_rejects_traversal_output_name(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _handle_captions_v2

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="parent references"):
            _handle_captions_v2(ctx, {"output_name": "../../evil.mp4"})

    def test_handle_music_rejects_traversal_output_name(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _handle_music

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="parent references"):
            _handle_music(ctx, {"output_name": "../../evil.mp4"})

    def test_handle_audio_rejects_traversal_output_name(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _handle_audio

        ctx = self._ctx(tmp_path)
        dummy = str(ctx.work_dir / "dummy.mp3")
        with pytest.raises(ValueError, match="parent references"):
            _handle_audio(
                ctx,
                {
                    "source": str(ctx.work_dir / "video.mp4"),
                    "voiceover": dummy,
                    "output_name": "../../evil.mp4",
                },
            )

    @pytest.mark.asyncio
    async def test_handle_align_rejects_traversal_output_name(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _handle_align

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="parent references"):
            await _handle_align(
                ctx,
                {
                    "audio": str(ctx.work_dir / "audio.mp3"),
                    "text": "hello",
                    "output_name": "../../evil.json",
                },
            )

    @pytest.mark.asyncio
    async def test_handle_run_music_video_rejects_traversal_output_name(
        self, tmp_path: Path
    ) -> None:
        """Bug #191: the ``run`` music_video worker branch must reject a
        traversal ``output_name`` before constructing the pipeline. Every
        other worker handler wraps ``output_name`` in ``_safe_filename``;
        the music_video branch must too. The check fires before any
        media_dir/source existence validation, so the payload can use
        contained-but-nonexistent paths.
        """
        from video_editor_cli.main import _handle_run

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="parent references"):
            await _handle_run(
                ctx,
                {
                    "source": str(ctx.work_dir / "song.mp3"),
                    "template": "musical_video",
                    "media_dir": str(ctx.work_dir / "media"),
                    "output_name": "../../evil.mp4",
                },
            )

    @pytest.mark.asyncio
    async def test_handle_run_music_video_rejects_traversal_language(self, tmp_path: Path) -> None:
        """Bug #192: the ``run`` music_video worker branch must reject a
        traversal ``language`` before constructing the pipeline. The
        ``language`` is interpolated into ``transcript_{language}.json``;
        a value like ``../../tmp/evil`` writes outside ``work_dir``. The
        check fires before any media_dir/source existence validation, so
        the payload can use contained-but-nonexistent paths.
        """
        from video_editor_cli.main import _handle_run

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="language"):
            await _handle_run(
                ctx,
                {
                    "source": str(ctx.work_dir / "song.mp3"),
                    "template": "musical_video",
                    "media_dir": str(ctx.work_dir / "media"),
                    "lyrics": str(ctx.work_dir / "lyrics.txt"),
                    "language": "../../tmp/evil",
                },
            )

    def test_handle_sfx_mix_rejects_traversal_plan_and_base(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _handle_sfx_mix

        ctx = self._ctx(tmp_path)
        with pytest.raises(ValueError, match="parent references"):
            _handle_sfx_mix(
                ctx,
                {
                    "plan_name": "../../other_plan.json",
                    "base": "with_broll.mp4",
                },
            )
        with pytest.raises(ValueError, match="parent references"):
            _handle_sfx_mix(
                ctx,
                {
                    "plan_name": "scene_plan.json",
                    "base": "../../other/video.mp4",
                },
            )


class TestSfxGenerateCli:
    """Session T: ``ve sfx-generate`` writes clips + a map to ``<work>/sfx/``."""

    def _write_scene_plan(self, tmp_path: Path) -> Path:
        """Create a minimal scene plan with one scene that has a visual
        description so ``sfx_requests_for_scene`` emits a SFX request."""
        plan_path = tmp_path / "work" / "sfx-job" / "plan" / "scene_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan = {
            "job_id": "sfx-job",
            "script": "a cinematic whoosh",
            "aspect_ratio": "16:9",
            "music_mood": "",
            "scenes": [
                {
                    "id": "scene-1",
                    "type": "narration",
                    "duration_seconds": 2.0,
                    "narration": "",
                    "visual_description": "a cinematic whoosh",
                    "asset_query": "",
                    "props": {},
                    "assets": [],
                }
            ],
        }
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return plan_path

    def test_sfx_generate_with_stub_provider(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Stub SFX generation through the CLI produces real MP3 artifacts
        and a JSON map on disk."""
        from video_editor_cli.main import app

        self._write_scene_plan(tmp_path)
        result = cli_runner.invoke(
            app,
            [
                "sfx-generate",
                "sfx-job",
                "--sfx-provider",
                "stub_sfx",
                "--sfx-duration-seconds",
                "1",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output

        sfx_dir = tmp_path / "work" / "sfx-job" / "sfx"
        clip = sfx_dir / "scene-1.mp3"
        sfx_map = sfx_dir / "sfx_map.json"
        assert clip.exists(), f"expected {clip} to exist"
        assert clip.stat().st_size > 0, "SFX clip is empty"
        assert sfx_map.exists(), f"expected {sfx_map} to exist"
        payload = json.loads(sfx_map.read_text(encoding="utf-8"))
        assert payload.get("scene-1") == str(clip)


class TestAudioCli:
    """Issue A: ``ve audio`` muxes an external voiceover onto a source video."""

    def _make_source(self, path: Path, duration: float = 1.0) -> Path:
        _require_ffmpeg()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s=160x120:d={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=100:duration={duration}",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        return path

    def _make_voiceover(self, path: Path, duration: float = 1.0) -> Path:
        _require_ffmpeg()
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=1000:duration={duration}",
                "-c:a",
                "libmp3lame",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        return path

    def test_audio_muxes_voiceover_onto_source(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """``ve audio`` writes <work>/intake/with_voiceover.mp4 with the VO as
        the audio track."""
        from video_editor_cli.main import app

        source = self._make_source(tmp_path / "source.mp4")
        vo = self._make_voiceover(tmp_path / "vo.mp3")

        result = cli_runner.invoke(
            app,
            [
                "audio",
                str(source),
                "audio-job",
                "--voiceover",
                str(vo),
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        out_path = tmp_path / "work" / "audio-job" / "intake" / "with_voiceover.mp4"
        assert out_path.exists(), f"expected {out_path} to exist"

        from video_editor_ffmpeg.probe import probe_media

        info = probe_media(out_path)
        assert info.has_audio
        assert (info.width, info.height) == (160, 120)

    def test_audio_missing_source_exits(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """A non-existent source fails fast (exit code != 0)."""
        from video_editor_cli.main import app

        vo = self._make_voiceover(tmp_path / "vo.mp3")
        result = cli_runner.invoke(
            app,
            [
                "audio",
                str(tmp_path / "no_such.mp4"),
                "audio-job",
                "--voiceover",
                str(vo),
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code != 0
        assert "Source video not found" in result.output

    def test_audio_handler_routes_through_worker(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """``ve enqueue audio ...`` then ``ve worker`` produces the same
        output as the sync CLI (real dispatcher, no fakes)."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "audio-job"
        job_dir.mkdir(parents=True, exist_ok=True)
        source = self._make_source(job_dir / "source.mp4")
        vo = self._make_voiceover(job_dir / "vo.mp3")
        output_root = tmp_path / "output"

        # Enqueue the audio job.
        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio",
                "-j",
                "audio-job",
                "--payload",
                json.dumps(
                    {
                        "source": str(source),
                        "voiceover": str(vo),
                        "work_root": str(work_root),
                        "output_root": str(output_root),
                    }
                ),
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        # Run the worker to dispatch it.
        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output

        out_path = work_root / "audio-job" / "intake" / "with_voiceover.mp4"
        assert out_path.exists(), f"expected {out_path} to exist after worker run"

    def test_audio_handler_rejects_voiceover_outside_work_root(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """A voiceover path outside the job's directories is rejected as a
        path-traversal guard."""
        from video_editor_cli.main import app

        work_root = tmp_path / "work"
        job_dir = work_root / "audio-bad"
        job_dir.mkdir(parents=True, exist_ok=True)
        source = self._make_source(job_dir / "source.mp4")
        outside_vo = self._make_voiceover(tmp_path / "vo.mp3")

        enqueue_result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "audio",
                "-j",
                "audio-bad",
                "--payload",
                json.dumps(
                    {
                        "source": str(source),
                        "voiceover": str(outside_vo),
                        "work_root": str(work_root),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
                "--max-retries",
                "0",
            ],
        )
        assert enqueue_result.exit_code == 0, enqueue_result.output

        worker_result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert worker_result.exit_code == 0, worker_result.output
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "audio"
        assert "allowed roots" in reason.lower()


class TestEnqueueTypedPayloadValidation:
    """Bug #79 regression: known worker payloads are validated at enqueue
    time so malformed jobs fail fast with a clear error instead of
    wedging the worker."""

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def test_enqueue_run_rejects_missing_source(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """A ``run`` payload missing the required ``source`` field is
        rejected before the message ever reaches the queue."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "typed-run-bad",
                "--payload",
                json.dumps(
                    {
                        "work_root": str(tmp_path / "work"),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
            ],
        )
        assert result.exit_code == 1, result.output
        assert "payload validation failed" in result.output.lower()
        assert "source" in result.output
        assert len(fresh_queue._pending) == 0

    def test_enqueue_run_accepts_valid_payload(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """A well-formed ``run`` payload passes validation and is queued."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "run",
                "-j",
                "typed-run-good",
                "--payload",
                json.dumps(
                    {
                        "source": str(tmp_path / "source.mp4"),
                        "work_root": str(tmp_path / "work"),
                        "output_root": str(tmp_path / "output"),
                    }
                ),
            ],
        )
        assert result.exit_code == 0, result.output
        pending = fresh_queue._pending["default"]
        assert len(pending) == 1
        msg = pending[0]
        assert msg.command == "run"
        assert msg.payload["source"] == str(tmp_path / "source.mp4")

    def test_enqueue_unknown_command_skips_validation(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        """Commands without a typed schema still enqueue with arbitrary
        payloads so the worker can decide whether to accept them."""
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "enqueue",
                "future_command",
                "-j",
                "future-job",
                "--payload",
                json.dumps({"anything": True}),
            ],
        )
        assert result.exit_code == 0, result.output
        pending = fresh_queue._pending["default"]
        assert len(pending) == 1
        assert pending[0].payload["anything"] is True


class TestWorkerLoopSupervision:
    """Bug #80 regression: a single bad job or unexpected exception must not
    kill the worker loop. The body of the drain loop is wrapped in a broad
    try/except that logs the error, increments the failure counter, and
    continues."""

    @pytest.fixture
    def fresh_queue(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> InMemoryJobQueue:
        """Reset the module-level queue singleton so tests don't leak state."""
        from video_editor_cli import main

        q = InMemoryJobQueue()
        monkeypatch.setattr(main, "_queue_instance", q, raising=False)
        return q

    def _enqueue(
        self,
        cli_runner: CliRunner,
        command: str,
        job_id: str,
        payload: dict[str, Any] | None = None,
        work_root: Path | None = None,
        output_root: Path | None = None,
    ) -> None:
        """Enqueue a job via the CLI with safe working paths."""
        from video_editor_cli.main import app

        body = dict(payload or {})
        if work_root is not None:
            body.setdefault("work_root", str(work_root))
        if output_root is not None:
            body.setdefault("output_root", str(output_root))

        cli_runner.invoke(
            app,
            [
                "enqueue",
                command,
                "--job-id",
                job_id,
                "--payload",
                json.dumps(body),
                "--max-retries",
                "0",
            ],
        )

    def test_worker_catches_unexpected_dispatch_exception_and_continues(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """If ``_dispatch_message`` itself raises outside the handler,
        the worker loop must catch it, report a loop error, and still
        process the next job."""
        from video_editor_cli import main
        from video_editor_cli.main import app

        call_count = 0

        async def _bad_dispatch(_queue: Any, _message: Any, **_kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("unexpected dispatch failure")

        monkeypatch.setattr(main, "_dispatch_message", _bad_dispatch)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(cli_runner, "music", "job-1", work_root=work_root, output_root=output_root)
        self._enqueue(cli_runner, "music", "job-2", work_root=work_root, output_root=output_root)

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "2"])
        assert result.exit_code == 0, result.output

        # Both dequeue attempts happened; the broad except kept the loop alive.
        assert call_count == 2
        assert "Worker loop error" in result.output
        # No completed jobs because dispatch never called complete/fail.
        assert len(fresh_queue._completed) == 0
        # The worker reported 2 failed at the summary level.
        assert "processed 0 job(s), 2 failed" in result.output

    def test_worker_fails_job_when_handler_never_moves_it(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """If a handler returns without completing or failing the job,
        the worker must move it to the failure list itself instead of
        spinning forever."""
        from video_editor_cli import main
        from video_editor_cli.main import app

        async def _no_terminal(_queue: Any, _message: Any, **_kwargs: Any) -> None:
            return

        monkeypatch.setattr(main, "_dispatch_message", _no_terminal)

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        self._enqueue(
            cli_runner, "music", "orphan-job", work_root=work_root, output_root=output_root
        )

        result = cli_runner.invoke(app, ["worker", "--max-jobs", "1"])
        assert result.exit_code == 0, result.output

        # The worker noticed the missing terminal state and failed the job.
        assert len(fresh_queue._failed) == 1
        bad_msg, reason = fresh_queue._failed[0]
        assert bad_msg.command == "music"
        assert "did not complete or fail" in reason
        assert "processed 0 job(s), 1 failed" in result.output


class TestStructlogConfiguration:
    """Bug #81 regression: the CLI configures structlog once at import time
    and switches to JSON rendering when ``VIDEO_EDITOR_LOG_FORMAT=json``."""

    def test_default_config_uses_console_renderer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without the env var, the active processors end with the console
        renderer."""
        from video_editor_cli import main

        monkeypatch.delenv("VIDEO_EDITOR_LOG_FORMAT", raising=False)
        main._configure_structlog()

        processors = structlog.get_config()["processors"]
        assert not any(isinstance(p, JSONRenderer) for p in processors)
        # The last processor is the human-friendly ConsoleRenderer.
        assert processors[-1].__class__.__name__ == "ConsoleRenderer"

    def test_json_env_var_uses_json_renderer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``VIDEO_EDITOR_LOG_FORMAT=json`` the active processors end
        with ``structlog.processors.JSONRenderer``."""
        from video_editor_cli import main

        monkeypatch.setenv("VIDEO_EDITOR_LOG_FORMAT", "json")
        main._configure_structlog()

        processors = structlog.get_config()["processors"]
        assert any(isinstance(p, JSONRenderer) for p in processors)


class TestPresetAspectResolution:
    """``ve run --qa`` must expect the aspect the preset actually renders."""

    def test_reads_aspect_from_preset_file(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _preset_aspect_ratio

        (tmp_path / "vertical.json").write_text('{"aspect_ratio": "9:16"}', encoding="utf-8")

        assert _preset_aspect_ratio(tmp_path, "vertical") == "9:16"

    def test_returns_none_for_unknown_preset(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _preset_aspect_ratio

        assert _preset_aspect_ratio(tmp_path, "does-not-exist") is None


class TestConfigDiscovery:
    """#258: ``ve plan-script`` must find a config.yaml without ``--config``."""

    def test_explicit_path_is_used_as_is(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _discover_config_path

        explicit = tmp_path / "custom.yaml"
        explicit.write_text("llm: {}\n", encoding="utf-8")

        assert _discover_config_path(explicit, start=tmp_path) == explicit

    def test_finds_config_in_the_working_directory(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _discover_config_path

        (tmp_path / "config.yaml").write_text("llm: {}\n", encoding="utf-8")

        assert _discover_config_path(None, start=tmp_path, repo_root=tmp_path / "nope") == (
            tmp_path / "config.yaml"
        )

    def test_walks_up_from_a_nested_directory(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _discover_config_path

        (tmp_path / "config.yaml").write_text("llm: {}\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)

        assert _discover_config_path(None, start=nested, repo_root=tmp_path / "nope") == (
            tmp_path / "config.yaml"
        )

    def test_env_var_wins_over_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_cli.main import _discover_config_path

        (tmp_path / "config.yaml").write_text("llm: {}\n", encoding="utf-8")
        elsewhere = tmp_path / "elsewhere.yaml"
        elsewhere.write_text("llm: {}\n", encoding="utf-8")
        monkeypatch.setenv("VE_CONFIG", str(elsewhere))

        assert _discover_config_path(None, start=tmp_path, repo_root=tmp_path) == elsewhere

    def test_falls_back_to_the_repo_root(self, tmp_path: Path) -> None:
        from video_editor_cli.main import _discover_config_path

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "config.yaml").write_text("llm: {}\n", encoding="utf-8")
        work = tmp_path / "work"
        work.mkdir()

        assert _discover_config_path(None, start=work, repo_root=repo) == repo / "config.yaml"

    def test_returns_none_when_no_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_cli.main import _discover_config_path

        monkeypatch.delenv("VE_CONFIG", raising=False)
        work = tmp_path / "work"
        work.mkdir()

        assert _discover_config_path(None, start=work, repo_root=tmp_path / "nope") is None

    def test_plan_script_reports_the_searched_locations(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When nothing is found the error must say where it looked."""
        from video_editor_cli import main as cli_main

        monkeypatch.delenv("VE_CONFIG", raising=False)
        monkeypatch.setattr(cli_main, "REPO_ROOT", tmp_path / "nope")
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(
            cli_main.app,
            [
                "plan-script",
                str(tmp_path / "script.md"),
                "job-x",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "out"),
            ],
        )

        assert result.exit_code == 1
        assert "config.yaml" in result.output


class TestDefaultRoots:
    """#260: defaults must fit the machine, not only a container."""

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from video_editor_cli import paths

        monkeypatch.setenv("VE_WORK_ROOT", "/mnt/fast/projects")
        assert paths.default_work_root() == "/mnt/fast/projects"

    def test_uses_the_container_mount_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from video_editor_cli import paths
        from video_editor_state import paths as state_paths

        monkeypatch.delenv("VE_WORK_ROOT", raising=False)
        monkeypatch.setattr(state_paths, "_mounted", lambda path: True)
        assert paths.default_work_root() == "/work"

    def test_falls_back_to_a_repo_local_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from video_editor_cli import paths
        from video_editor_state import paths as state_paths

        monkeypatch.delenv("VE_WORK_ROOT", raising=False)
        monkeypatch.delenv("VE_OUTPUT_ROOT", raising=False)
        monkeypatch.setattr(state_paths, "_mounted", lambda path: False)
        assert paths.default_work_root() == "projects"
        assert paths.default_output_root() == "outputs"

    def test_payloads_use_the_same_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VE_WORK_ROOT", raising=False)
        monkeypatch.delenv("VE_OUTPUT_ROOT", raising=False)
        from video_editor_cli import paths, payloads
        from video_editor_state import paths as state_paths

        monkeypatch.setattr(state_paths, "_mounted", lambda path: False)
        monkeypatch.setattr(payloads, "_DEFAULT_WORK_ROOT", paths.default_work_root())
        monkeypatch.setattr(payloads, "_DEFAULT_OUTPUT_ROOT", paths.default_output_root())
        payload = payloads.RunPayload(source="raw.mp4")
        assert payload.work_root == "projects"
        assert payload.output_root == "outputs"


@pytest.mark.parametrize("command", ["short", "explain", "run", "assets", "estimate"])
def test_final_image_chain_flag_is_exposed(cli_runner: CliRunner, command: str) -> None:
    """#291/§5.7: every image-buying command can select the final chain."""
    from video_editor_cli.main import app

    result = cli_runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0, result.output
    assert "--final" in result.output


def test_agy_setup_writes_the_restricted_profile(cli_runner: CliRunner, tmp_path: Path) -> None:
    from video_editor_cli.main import app

    profile = tmp_path / "agy-profile"
    result = cli_runner.invoke(app, ["agy-setup", "--profile-dir", str(profile)])

    assert result.exit_code == 0, result.output
    assert (profile / "agy_guard.py").exists()
    assert (profile / ".gemini" / "antigravity-cli" / "settings.json").exists()
    assert (profile / ".gemini" / "config" / "hooks.json").exists()
