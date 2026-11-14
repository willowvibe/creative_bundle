"""CLI command group."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
import typer
from pydantic import ValidationError
from video_editor_config import ProjectConfig
from video_editor_state.locking import JobLockError, job_lock
from video_editor_state.queue import JobMessage

from video_editor_cli import main as cli_main
from video_editor_cli.paths import default_output_root, default_work_root
from video_editor_cli.payloads import (
    PAYLOAD_SCHEMAS,
)

logger = structlog.get_logger(__name__)


def register_queue(app: typer.Typer) -> None:
    """Register the queue commands."""

    @app.command()
    def enqueue(
        command: str = typer.Argument(..., help="Command name to enqueue"),
        job_id: str = cli_main._JOB_ID_OPT,
        tenant_id: str = cli_main._TENANT_ID_OPT,
        payload: str = typer.Option("{}", "--payload", help="JSON payload string"),
        max_retries: int = typer.Option(
            3, "--max-retries", help="Maximum retry attempts before dead-lettering (default: 3)"
        ),
    ) -> None:
        """Enqueue a job for the worker layer.

        The JSON payload's required and optional keys depend on
        ``command``. See ``docs/CLI_USAGE.md`` for the full per-command
        payload contract. ``work_root`` and ``output_root`` are optional:
        when omitted the worker resolves them the same way the CLI does
        (``VE_WORK_ROOT`` / ``VE_OUTPUT_ROOT`` → container mount →
        ``./projects`` / ``./outputs``).

        .. note::

            The default queue is **``PersistentJobQueue``** (SQLite at
            ``VE_QUEUE_DB``, following the output root), so ``ve enqueue`` and
            ``ve worker`` in different processes share jobs. An injected
            in-memory queue is process-local and is intended for tests and
            in-process workflows.
        """

        try:
            parsed_payload: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError as exc:
            typer.echo(
                f"Error: --payload is not valid JSON: {exc.msg} at line {exc.lineno} col {exc.colno}",
                err=True,
            )
            raise typer.Exit(1) from exc

        if not isinstance(parsed_payload, dict):
            typer.echo(
                f"Error: --payload must decode to a JSON object, got {type(parsed_payload).__name__}",
                err=True,
            )
            raise typer.Exit(1)

        # Bug #79: validate known payloads against Pydantic schemas at enqueue
        # time so the worker receives a typed/contract-checked dict.
        schema = PAYLOAD_SCHEMAS.get(command)
        if schema is not None:
            try:
                schema.model_validate(parsed_payload)
            except ValidationError as exc:
                typer.echo(
                    f"Error: payload validation failed for {command!r}: {exc}",
                    err=True,
                )
                raise typer.Exit(1) from exc

        message = JobMessage(
            job_id=job_id,
            tenant_id=tenant_id,
            command=command,
            payload=parsed_payload,
            max_retries=max_retries,
            idempotency_key=job_id,
        )
        stored = asyncio.run(cli_main._get_queue().enqueue(message))
        if stored.job_id != job_id:
            typer.echo(f"Already enqueued as {tenant_id}/{stored.job_id} (idempotent duplicate)")
        else:
            typer.echo(f"Enqueued {command} for {tenant_id}/{job_id}")
        cli_main._warn_in_memory_queue_once(cli_main._get_queue())

    @app.command()
    def dequeue(
        tenant_id: str = cli_main._TENANT_ID_OPT,
    ) -> None:
        """Dequeue the next pending job (scoped to tenant if provided)."""

        cli_main._warn_in_memory_queue_once(cli_main._get_queue())
        message = asyncio.run(cli_main._get_queue().dequeue(tenant_id))
        if message is None:
            typer.echo("No pending jobs")
            return
        typer.echo(f"Dequeued: {message.command} {message.job_id} {message.payload}")

    @app.command()
    def queue_size(
        tenant_id: str = cli_main._TENANT_ID_OPT,
    ) -> None:
        """Show the number of pending jobs."""

        cli_main._warn_in_memory_queue_once(cli_main._get_queue())
        size = asyncio.run(cli_main._get_queue().size(tenant_id))
        typer.echo(f"Pending jobs: {size}")

    @app.command()
    def worker(
        tenant_id: str = cli_main._TENANT_ID_OPT,
        max_jobs: int = typer.Option(1, "--max-jobs", help="Jobs to process before exiting"),
        stale_seconds: float | None = typer.Option(
            None,
            "--stale-seconds",
            help=(
                "Requeue a running job whose worker has not heartbeated for this long "
                "(default: queue.stale_seconds from the config)"
            ),
        ),
    ) -> None:
        """Process queued jobs by dispatching each to the matching pipeline / skill.

        The worker dequeues ``JobMessage`` entries from the configured queue
        and routes each one to a handler based on the ``command`` field.
        A handler exception or unknown command is reported via
        ``queue.fail`` so the worker keeps running across the remaining
        jobs. See ``HANDLERS`` for the supported command names and the
        payload keys each one accepts.

        .. note::

            The default queue is **``PersistentJobQueue``** (SQLite at
            ``VE_QUEUE_DB``), shared across processes. An injected in-memory
            queue is process-local and only serves same-process workflows.
        """

        if stale_seconds is None:
            stale_seconds = ProjectConfig.load().queue.stale_seconds
        if stale_seconds <= 0:
            raise typer.BadParameter("--stale-seconds must be greater than zero")

        cli_main._warn_in_memory_queue_once(cli_main._get_queue())

        async def _run() -> None:
            queue = cli_main._get_queue()
            processed = 0
            failed = 0
            lock_contention = 0
            while processed + failed < max_jobs:
                message: JobMessage | None = None
                try:
                    # #275: reclaim jobs a crashed worker left ``running``.
                    for stale in await queue.requeue_stale(stale_seconds):
                        typer.echo(f"Requeued stale job {stale.job_id}", err=True)
                    message = await queue.dequeue(tenant_id)
                    if message is None:
                        typer.echo("No more jobs")
                        break
                    typer.echo(f"Processing {message.command} for {message.job_id}")
                    # Bug #30 / #260: a payload without ``work_root`` /
                    # ``output_root`` used to fall back to the container-only
                    # ``/work`` / ``/output`` and PermissionError on the first
                    # ``ensure_dirs()``. The roots now resolve through
                    # ``video_editor_state.paths`` (env → mount → ./projects),
                    # so this is informational rather than a warning.
                    payload = message.payload
                    if "work_root" not in payload or "output_root" not in payload:
                        missing = [k for k in ("work_root", "output_root") if k not in payload]
                        typer.echo(
                            f"Note: {message.command} payload omits "
                            f"{', '.join(missing)}; the worker will use "
                            f"{default_work_root()} / {default_output_root()}.",
                            err=True,
                        )
                    # ``cli_main._build_job_context`` now validates the roots themselves;
                    # every other payload path is resolved by its handler.
                    # Ensure work_root / output_root exist once per dequeued
                    # message. The dispatcher no longer calls ensure_dirs()
                    # itself, so a worker draining 100 jobs pays one
                    # stat/mkdir pass per message (Bug #29) instead of
                    # once per handler invocation.
                    job_ctx = cli_main._build_job_context(message)
                    job_ctx.ensure_dirs()
                    # ``cli_main._dispatch_message`` returns a status literal so the
                    # worker accounts for the message exactly once. C2
                    # regression: previously the loop snapshotted
                    # ``completed_count`` / ``failed_count`` and fired a
                    # "no terminal state" guard when neither grew — but a
                    # *retried* message goes to ``_delayed`` (neither counter
                    # grows), so the guard called ``queue.fail`` a SECOND
                    # time, consuming 2 retries per handler failure and
                    # overwriting the real error reason. The status literal
                    # distinguishes "dispatcher handled it" (completed/failed)
                    # from "dispatcher returned without doing anything"
                    # (unknown), so retried messages are no longer double-failed.
                    with job_lock(job_ctx.work_dir):
                        async with cli_main._heartbeat(queue, message, stale_seconds):
                            status = await cli_main._dispatch_message(queue, message, ctx=job_ctx)
                    if status == "completed":
                        processed += 1
                        lock_contention = 0
                    elif status == "failed":
                        # Dispatcher already called ``queue.fail`` — either a
                        # retry to ``_delayed`` or a dead-letter. Either way
                        # the message is accounted for; do NOT call fail again.
                        failed += 1
                        lock_contention = 0
                    elif status == "lost":
                        # Another worker reclaimed this job while it ran
                        # (#298). That worker owns the row now; touching it
                        # here would overwrite its state, so leave it alone.
                        logger.warning(
                            "worker_lease_lost",
                            command=message.command,
                            job_id=message.job_id,
                        )
                    else:
                        # ``status == "unknown"``: dispatcher returned without
                        # moving the message to a terminal state. Bug #80 guard
                        # — treat as a worker-level failure so we don't spin.
                        logger.error(
                            "worker_dispatch_no_terminal_state",
                            command=message.command,
                            job_id=message.job_id,
                        )
                        await queue.fail(message, "dispatch did not complete or fail")
                        failed += 1
                except JobLockError:
                    # A live worker already holds this job's lock. Never call
                    # ``fail`` here (#298): this worker did not run the job, so
                    # a failure would burn one of the owner's retries and
                    # overwrite its error. Hand the claim back and let the
                    # sweeper/owner finish.
                    if message is not None:
                        await queue.release(message)
                    lock_contention += 1
                    if lock_contention > 5:
                        typer.echo(
                            "Worker: job lock is held by another process; stopping",
                            err=True,
                        )
                        break
                    await asyncio.sleep(0.5)
                    continue
                except Exception as exc:  # noqa: BLE001
                    # Bug #80: supervision — catch unexpected loop-level errors,
                    # log them, and keep draining. Without this a single bad
                    # dequeue or handler could kill the worker process.
                    logger.error(
                        "worker_loop_error",
                        error=str(exc),
                        job_id=getattr(message, "job_id", None),
                    )
                    typer.echo(f"Worker loop error: {exc!r}", err=True)
                    # C3 regression: previously the broad except counted the
                    # job as failed without telling the queue. The message was
                    # already dequeued (removed from ``_pending``, added to
                    # ``_in_flight``), so it was orphaned — not in pending,
                    # completed, failed, or dead-letter; stuck in ``_in_flight``
                    # forever. Fail it through the queue so it lands in
                    # ``_failed`` / ``_dead_letter`` and can be retried or
                    # inspected. ``message`` is None only if dequeue itself
                    # raised before assigning.
                    if message is not None:
                        try:
                            await queue.fail(message, repr(exc))
                        except Exception:  # noqa: BLE001 — best-effort cleanup
                            logger.error(
                                "worker_loop_fail_after_error",
                                job_id=message.job_id,
                                error=str(exc),
                            )
                    failed += 1
            typer.echo(f"Worker processed {processed} job(s), {failed} failed")

        asyncio.run(_run())
