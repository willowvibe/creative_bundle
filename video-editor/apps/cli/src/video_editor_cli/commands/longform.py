"""`ve longform`: build a chapter pack list, join and gate it (#315)."""

from __future__ import annotations

from pathlib import Path

import typer

from video_editor_cli import main as cli_main


def register_longform(app: typer.Typer) -> None:
    """Register the ``longform`` command."""

    @app.command()
    def longform(
        plan_path: Path = typer.Argument(..., exists=True, dir_okay=False),
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        auto_approve: bool = cli_main._AUTO_APPROVE_OPT,
        final: bool = typer.Option(
            False, "--final", help="Use the final image chain for every chapter."
        ),
        skip_qa: bool = typer.Option(False, "--skip-qa", help="Skip joined-file QA."),
    ) -> None:
        """Render each chapter as its own job, join them and gate the result."""
        from video_editor_pipelines.longform import (
            LongformError,
            load_longform,
            run_longform,
        )

        try:
            plan = load_longform(plan_path)
        except (LongformError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc

        ctx = cli_main._context(plan.job_id, work_root, output_root, config_path=config_path)
        try:
            result = run_longform(
                ctx,
                plan,
                config_path=config_path,
                auto_approve=auto_approve,
                final=final,
                run_qa=not skip_qa,
            )
        except (LongformError, Exception) as exc:  # noqa: BLE001 - report the failing chapter
            typer.echo(f"Long-form failed: {exc}", err=True)
            raise typer.Exit(1) from exc

        for index, chapter in enumerate(result.chapter_paths, start=1):
            typer.echo(f"Chapter {index}: {chapter}")
        typer.echo(f"\nFinal: {result.final_path}")
        if result.qa_path is not None:
            typer.echo(f"QA:    {result.qa_path} ({'PASS' if result.passed else 'FAIL'})")
        if not result.passed:
            raise typer.Exit(1)
