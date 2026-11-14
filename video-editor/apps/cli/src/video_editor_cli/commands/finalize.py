"""ve finalize: produce the final deliverable after preview approval."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer
from verify.verify import VerifySkill
from video_editor_state.job import JobContext
from video_editor_state.ledger_hooks import record_pipeline_complete

from video_editor_cli.paths import default_output_root, default_work_root

logger = structlog.get_logger(__name__)


def finalize_command(
    job_id: str,
    work_root: str | None = None,
    output_root: str | None = None,
    output_name: str = "final.mp4",
    config_path: Path | None = None,  # reserved for future use (provider config overrides)
    run_review: bool = False,
    run_verify: bool = False,
) -> Path:
    """Export the final MP4, optionally running review and verify gates."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root or default_work_root(),
        output_root=output_root or default_output_root(),
    )
    ctx.ensure_dirs()
    manifest = ctx.manifest()

    # Determine input: use edited plan video if preview was done,
    # otherwise use the original rendered video.
    final_path = ctx.output_dir / output_name

    # If preview stage completed, the rendered video should already exist
    # at the output path from the preview's render_fablecut_project call.
    # Otherwise, look for the standard render output.
    if not final_path.exists():
        # Fall back to the standard render output
        rendered = ctx.output_dir / "final.mp4"
        if rendered.exists():
            final_path = rendered

    if not final_path.exists():
        typer.echo(
            "No rendered video found. Run ve render or ve preview first.",
            err=True,
        )
        raise typer.Exit(1)

    # Export (copy to final location + generate report)
    from video_editor_cli import main as cli_main

    export = cli_main.ExportSkill(ctx)
    final = export.run(output_name=output_name)
    typer.echo(f"Final export: {final}")

    # Optional post-render review
    if run_review and not manifest.is_stage_complete("review"):
        review_skill = cli_main.PostRenderReviewSkill(ctx)
        review_report = review_skill.run(final)
        typer.echo(
            f"Post-render review: {review_report.overall} ({len(review_report.checks)} checks)"
        )
        export.append_post_render_review()

    # Optional verification gate
    if run_verify:
        import asyncio

        verify_skill = VerifySkill(ctx)
        try:
            report = asyncio.run(verify_skill.run())
            typer.echo(f"Verification: {report.overall} ({len(report.checks)} checks)")
        except Exception as exc:
            typer.echo(f"Verification failed: {exc}", err=True)
            raise typer.Exit(1) from exc

    # Mark finalize stage
    manifest.stage_done("finalize", {"final": str(final)})
    ctx.store.save()

    # Record pipeline completion in ledger
    record_pipeline_complete(ctx, final_video_path=str(final), status="complete")

    return final


def register_finalize(app: typer.Typer) -> None:
    """Register the finalize command on the Typer app."""

    @app.command(name="finalize")
    def finalize(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str | None = typer.Option(
            None,
            "--work-root",
            help="Work directory root (default: VE_WORK_ROOT, else /work or ./projects)",
        ),
        output_root: str | None = typer.Option(
            None,
            "--output-root",
            help="Output directory root (default: VE_OUTPUT_ROOT, else /output or ./outputs)",
        ),
        output_name: str = typer.Option("final.mp4", "--output-name", help="Output video filename"),
        config_path: Path | None = typer.Option(
            None, "--config", "-c", help="Path to project config.yaml"
        ),
        review: bool = typer.Option(False, "--review", help="Run post-render review gate"),
        verify: bool = typer.Option(False, "--verify", help="Run requirements verification gate"),
    ) -> None:
        """Export the final MP4 after preview approval."""
        finalize_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            output_name=output_name,
            config_path=config_path,
            run_review=review,
            run_verify=verify,
        )
