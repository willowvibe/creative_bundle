"""CLI command group."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_short(app: typer.Typer) -> None:
    """Register the short commands."""

    @app.command()
    def short(
        pack: Path = typer.Argument(
            ..., exists=True, dir_okay=False, help="Path to a pack's short.yaml"
        ),
        job_id: str = typer.Option("", "--job-id", help="Override the pack's job id."),
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        auto_approve: bool = cli_main._AUTO_APPROVE_OPT,
        final: bool = typer.Option(
            False, "--final", help="Use the final image chain (direct image APIs, no agy)"
        ),
        strict_look: bool = typer.Option(
            False,
            "--strict-look",
            help="Fail QA when the look-ban checks find monotony (advisory by default)",
        ),
    ) -> None:
        """Build a short from a content pack: stills, voice, one render, QA."""
        from video_editor_core.content_pack import ContentPackError, load_content_pack
        from video_editor_pipelines.estimate_ui import EstimateAbortedError, EstimateError
        from video_editor_pipelines.short_pipeline import (
            ShortPipelineError,
            ShortPreflightError,
            estimate_short,
            preflight_short,
            run_short_job,
        )

        try:
            content = load_content_pack(pack)
            tokens = preflight_short(content)
        except (ContentPackError, ShortPreflightError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc

        ctx = cli_main._context(
            job_id or content.job_id, work_root, output_root, config_path=config_path
        )
        try:
            estimate_short(
                ctx, content, config_path=config_path, auto_approve=auto_approve, final=final
            )
        except EstimateError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        except EstimateAbortedError:
            raise typer.Exit(0) from None
        try:
            result = run_short_job(
                ctx,
                content,
                tokens,
                config_path=config_path,
                final=final,
                strict_look=strict_look,
            )
        except ShortPipelineError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        if result.qa_text:
            typer.echo(result.qa_text)
        typer.echo(f"\nFinal: {result.final_path}")
        if result.srt_path is not None:
            typer.echo(f"SRT:   {result.srt_path}")
        typer.echo(f"QA:    {result.qa_path} ({'PASS' if result.passed else 'FAIL'})")
        if not result.passed:
            raise typer.Exit(1)
