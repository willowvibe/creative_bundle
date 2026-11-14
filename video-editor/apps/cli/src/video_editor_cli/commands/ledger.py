"""CLI command group."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer
from video_editor_pipelines.handlers import _safe_filename
from video_editor_state.job import JobContext
from video_editor_state.ledger import ProjectLedger

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_ledger(app: typer.Typer) -> None:
    """Register the ledger commands."""

    @app.command(name="project-init")
    def project_init(
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Create/verify the SQLite project ledger file."""
        ledger = JobContext._resolve_ledger(config_path)
        if isinstance(ledger, ProjectLedger):
            ledger.init_db()
            typer.echo(f"Ledger ready: {ledger.path}")
        else:
            typer.echo(
                "Ledger is disabled or no path is configured. "
                "Add a 'ledger:' section to config.yaml to enable."
            )

    @app.command(name="project-list")
    def project_list(
        status: str | None = typer.Option(None, "--status", "-s", help="Filter by project status"),
        since: str | None = typer.Option(None, "--since", help="ISO date lower bound"),
        limit: int = typer.Option(100, "--limit", "-n", help="Maximum rows to show"),
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """List projects in the ledger."""
        ledger = cli_main._resolve_ledger_cli(config_path)
        rows = ledger.list_projects(status=status, since=since, limit=limit)
        if not rows:
            typer.echo("No projects found.")
            return
        header = f"{'id':<24} {'status':<10} {'template':<20} {'created_at':<24}"
        typer.echo(header)
        typer.echo("-" * len(header))
        for row in rows:
            typer.echo(
                f"{row['id']:<24} {row['status']:<10} "
                f"{(row['template'] or ''):<20} {row['created_at']:<24}"
            )

    @app.command(name="project-status")
    def project_status(
        project_id: str = typer.Argument(..., help="Project id (usually the job_id)"),
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Show project status, jobs, costs, issues, and artifacts."""
        ledger = cli_main._resolve_ledger_cli(config_path)
        project = ledger.get_project(project_id)
        if project is None:
            typer.echo(f"Project not found: {project_id}", err=True)
            raise typer.Exit(1)

        typer.echo(f"id: {project['id']}")
        typer.echo(f"status: {project['status']}")
        typer.echo(f"template: {project['template'] or '—'}")
        typer.echo(f"pipeline_kind: {project['pipeline_kind'] or '—'}")
        typer.echo(f"created_at: {project['created_at']}")
        if project["completed_at"]:
            typer.echo(f"completed_at: {project['completed_at']}")
        if project["notes"]:
            typer.echo(f"notes: {project['notes']}")

        if project["jobs"]:
            typer.echo(f"\njobs ({len(project['jobs'])}):")
            for job in project["jobs"]:
                typer.echo(f"  {job['stage']:<12} {job['status']:<10} {job['started_at'] or ''}")

        if project["cost_lines"]:
            typer.echo(f"\ncost_lines ({len(project['cost_lines'])}):")
            for cost in project["cost_lines"]:
                typer.echo(
                    f"  {cost['provider'] or '—':<18} {cost['task_type'] or '—':<12} "
                    f"est={cost['estimated_cost'] or 0:.4f} act={cost['actual_cost'] or 0:.4f} "
                    f"[{cost['status']}"
                )

        if project["issues"]:
            typer.echo(f"\nissues ({len(project['issues'])}):")
            for issue in project["issues"]:
                typer.echo(
                    f"  [{issue['severity']:<8}] {issue['category']:<10} {issue['description']}"
                )

        if project["artifacts"]:
            typer.echo(f"\nartifacts ({len(project['artifacts'])}):")
            for artifact in project["artifacts"]:
                typer.echo(f"  {artifact['kind']:<16} {artifact['path'] or ''}")

    @app.command(name="cost-report")
    def cost_report(
        provider: str | None = typer.Option(None, "--provider", help="Filter by provider"),
        task_type: str | None = typer.Option(None, "--task-type", help="Filter by task type"),
        template: str | None = typer.Option(None, "--template", help="Filter by project template"),
        since: str | None = typer.Option(None, "--since", help="ISO date lower bound"),
        config_path: Path | None = cli_main._CONFIG_OPT,
    ) -> None:
        """Aggregate spend across projects."""
        ledger = cli_main._resolve_ledger_cli(config_path)
        rows = ledger.cost_report(
            provider=provider,
            task_type=task_type,
            template=template,
            since=since,
        )
        degraded = ledger.degraded_cost_projects(since=since)
        if not rows:
            typer.echo("No cost data found.")
            if degraded:
                typer.echo(
                    f"warning: {len(degraded)} project(s) reported degraded cost tracking "
                    f"({', '.join(degraded[:5])}{'…' if len(degraded) > 5 else ''}); "
                    "some spend may be missing",
                    err=True,
                )
            return
        header = (
            f"{'provider':<18} {'task_type':<12} {'calls':<6} {'estimated':<12} "
            f"{'actual':<12} {'unpriced':<8}"
        )
        typer.echo(header)
        typer.echo("-" * len(header))
        for row in rows:
            typer.echo(
                f"{row['provider']:<18} {row['task_type']:<12} "
                f"{row['call_count']:<6} {row['estimated_total']:<12.4f} "
                f"{row['actual_total']:<12.4f} {row.get('unpriced_count') or 0:<8}"
            )
        unpriced = sum(int(row.get("unpriced_count") or 0) for row in rows)
        if unpriced:
            # #283: those rows are NULL, so the totals above undercount the spend.
            typer.echo(
                f"warning: {unpriced} call(s) have no recorded price; "
                "the actual totals undercount real spend",
                err=True,
            )
        if degraded:
            typer.echo(
                f"warning: {len(degraded)} project(s) reported degraded cost tracking "
                f"({', '.join(degraded[:5])}{'…' if len(degraded) > 5 else ''}); "
                "some spend may be missing",
                err=True,
            )

    @app.command(name="review")
    def review_cmd(
        job_id: str = cli_main._JOB_ID_ARG,
        output_name: str = cli_main._OUTPUT_NAME_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        expected_aspect_ratio: str = cli_main._EXPECTED_ASPECT_OPT,
        loudness_target_lufs: float | None = cli_main._LOUDNESS_TARGET_OPT,
    ) -> None:
        """Run the post-render review gate on an existing exported video."""
        ctx = cli_main._context(job_id, work_root, output_root)
        final_path = ctx.output_dir / _safe_filename(output_name, "output_name")
        if not final_path.exists():
            typer.echo(f"No exported video found at {final_path}", err=True)
            raise typer.Exit(1)

        review_skill = cli_main.PostRenderReviewSkill(
            ctx,
            expected_aspect_ratio=expected_aspect_ratio,
            loudness_target_lufs=loudness_target_lufs,
        )
        review_report = review_skill.run(final_path)
        typer.echo(
            f"Post-render review: {review_report.overall} ({len(review_report.checks)} checks)"
        )
        for check in review_report.checks:
            if check.result != "PASS":
                typer.echo(
                    f"  {check.category}: {check.name} = {check.result} — {check.reason}",
                    err=(check.category == "critical" and check.result == "FAIL"),
                )
