"""Convenience helpers for writing common ledger rows during a pipeline run.

These helpers are thin wrappers around :class:`JobContext.ledger` so the
pipeline code stays readable and every row carries consistent field names.
They are safe to call with a :class:`NullLedger`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_editor_state.job import JobContext


def record_pipeline_start(
    ctx: JobContext,
    pipeline_kind: str,
    *,
    template: str | None = None,
    source_path: str | Path | None = None,
    budget_limit: float | None = None,
    status: str = "running",
    tags: list[str] | None = None,
) -> None:
    """Upsert a project row when a pipeline begins."""
    ctx.ledger.upsert_project(
        ctx.job_id,
        name=ctx.job_id,
        template=template,
        pipeline_kind=pipeline_kind,
        status=status,
        source_path=str(source_path) if source_path else None,
        budget_limit=budget_limit,
        tags=tags,
    )


def record_stage_start(ctx: JobContext, stage: str) -> None:
    """Record that a pipeline stage has started."""
    ctx.ledger.record_job(ctx.job_id, stage, status="running")


def record_stage_done(
    ctx: JobContext,
    stage: str,
    outputs: dict[str, Any] | None = None,
) -> None:
    """Record that a pipeline stage completed successfully."""
    ctx.ledger.set_job_status(ctx.job_id, stage, "done")


def record_stage_failed(ctx: JobContext, stage: str, error: str) -> None:
    """Record that a pipeline stage failed."""
    ctx.ledger.set_job_status(ctx.job_id, stage, "failed")
    ctx.ledger.record_issue(
        ctx.job_id,
        severity="high",
        category="render",
        description=f"Stage {stage} failed: {error}",
    )


def record_pipeline_complete(
    ctx: JobContext,
    *,
    final_video_path: str | Path | None = None,
    final_duration_s: float | None = None,
    status: str = "complete",
    notes: str | None = None,
) -> None:
    """Mark the project complete/partial/dropped and rollup usage."""
    updates: dict[str, Any] = {"status": status}
    if notes is not None:
        updates["notes"] = notes
    ctx.ledger.set_project_status(ctx.job_id, status, notes=notes)
    if final_video_path is not None:
        ctx.ledger.record_artifact(
            ctx.job_id,
            "final_video",
            path=str(final_video_path),
        )
    ctx.ledger.rollup_provider_usage(ctx.job_id)


def record_pipeline_failed(
    ctx: JobContext,
    error: str,
    *,
    status: str = "failed",
) -> None:
    """Mark the project failed or dropped with an explanatory note."""
    ctx.ledger.set_project_status(ctx.job_id, status, notes=error)
    ctx.ledger.record_issue(
        ctx.job_id,
        severity="critical",
        category="render",
        description=f"Pipeline failed: {error}",
    )
    ctx.ledger.rollup_provider_usage(ctx.job_id)


def record_cost_line(
    ctx: JobContext,
    *,
    asset_id: str | None = None,
    provider: str | None = None,
    task_type: str | None = None,
    model: str | None = None,
    estimated_cost: float | None = None,
    actual_cost: float | None = None,
    currency: str = "credits",
    status: str = "success",
    job_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a provider cost line for the current project."""
    ctx.ledger.record_cost(
        ctx.job_id,
        asset_id=asset_id,
        provider=provider,
        task_type=task_type,
        model=model,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        currency=currency,
        status=status,
        job_id=job_id,
        metadata=metadata,
    )


def record_issue(
    ctx: JobContext,
    *,
    severity: str,
    category: str,
    description: str,
    code: str | None = None,
    job_id: str | None = None,
) -> None:
    """Record an audit/QA/review issue for the current project."""
    ctx.ledger.record_issue(
        ctx.job_id,
        severity=severity,
        category=category,
        description=description,
        code=code,
        job_id=job_id,
    )
