"""CLI command group."""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer

from video_editor_cli import main as cli_main

logger = structlog.get_logger(__name__)


def register_explain(app: typer.Typer) -> None:
    """Register the explain commands."""

    @app.command()
    def explain(
        script_path: Path | None = typer.Argument(
            None, exists=True, dir_okay=False, help="Path to the explainer script.md"
        ),
        job_id: str = cli_main._JOB_ID_OPT,
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        from_topic: str = cli_main._EXPLAIN_FROM_TOPIC_OPT,
        tts_provider: str = cli_main._EXPLAIN_TTS_PROVIDER_OPT,
        image_provider: str = cli_main._EXPLAIN_IMAGE_PROVIDER_OPT,
        music_provider: str = cli_main._MUSIC_PROVIDER_OPT,
        music_path: Path | None = cli_main._MUSIC_PATH_OPT,
        caption_preset: str = cli_main._EXPLAIN_CAPTION_PRESET_OPT,
        sfx_provider: str = cli_main._SFX_PROVIDER_OPT,
        no_reuse: bool = cli_main._EXPLAIN_NO_REUSE_OPT,
        auto_approve: bool = cli_main._AUTO_APPROVE_OPT,
        final: bool = typer.Option(
            False, "--final", help="Use the final image chain (direct image APIs, no agy)"
        ),
    ) -> None:
        """Build, price and render a simple explanatory video from script.md."""

        from video_editor_pipelines.estimate_ui import EstimateAbortedError, EstimateError
        from video_editor_pipelines.explainer_pipeline import (
            ExplainerPreflightError,
            estimate_explainer,
            preflight_explainer,
            prepare_explainer,
            run_explainer_job,
        )
        from video_editor_pipelines.explainer_script import load_explainer_script
        from video_editor_pipelines.explainer_topic import (
            GENERATED_SCRIPT_PATH,
            TopicDraftError,
            draft_script_from_topic,
        )
        from video_editor_pipelines.still_reuse import format_reuse_report

        if from_topic and script_path is not None:
            raise typer.BadParameter(
                "pass a script.md or --from-topic, not both: the script argument and "
                "--from-topic are alternatives."
            )
        if not from_topic and script_path is None:
            raise typer.BadParameter("pass a script.md, or use --from-topic to draft one.")

        ctx = cli_main._context(job_id, work_root, output_root, config_path=config_path)
        if from_topic:
            llm = cli_main._llm_router(cli_main._discover_config_path(config_path), ctx)
            if llm is None:
                typer.echo(
                    "No config.yaml found for --from-topic. Pass --config PATH or "
                    "create config.yaml in the repo root.",
                    err=True,
                )
                raise typer.Exit(1)
            try:
                asyncio.run(draft_script_from_topic(from_topic, llm, ctx.work_dir, job_id=job_id))
            except TopicDraftError as exc:
                typer.echo(f"Could not draft a script: {exc}", err=True)
                raise typer.Exit(1) from exc
            generated = ctx.work_dir / GENERATED_SCRIPT_PATH
            typer.echo(f"Script drafted: {generated}")
            if not auto_approve and not typer.confirm("Build this script?", default=False):
                typer.echo(f"Edit {generated}, then run: ve explain {generated} --job-id {job_id}")
                raise typer.Exit(0)
            script = load_explainer_script(generated)
        elif script_path is not None:
            script = load_explainer_script(script_path)

        # The design, caption preset and HyperFrames check are free and local.
        # Run them before the estimate so an unbuildable job is never priced.
        try:
            preflight_explainer(script, caption_preset=caption_preset)
        except ExplainerPreflightError as exc:
            typer.echo(f"Explainer preflight failed: {exc}", err=True)
            raise typer.Exit(1) from exc

        plan, reuse = prepare_explainer(ctx, script, reuse=not no_reuse)
        typer.echo(format_reuse_report(reuse))
        typer.echo(f"Plan: {ctx.work_dir / 'plan' / 'scene_plan.json'}")
        try:
            estimate_explainer(
                ctx,
                script,
                plan,
                config_path=config_path,
                tts_provider=tts_provider,
                image_provider=image_provider,
                music_provider=music_provider,
                music_path=music_path,
                auto_approve=auto_approve,
                final=final,
                sfx_provider=sfx_provider,
            )
        except EstimateError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        except EstimateAbortedError:
            raise typer.Exit(0) from None
        result = run_explainer_job(
            ctx,
            script,
            plan,
            config_path=config_path,
            tts_provider=tts_provider,
            image_provider=image_provider,
            music_provider=music_provider,
            music_path=music_path,
            caption_preset=caption_preset,
            final=final,
            sfx_provider=sfx_provider,
        )
        typer.echo(f"Final output: {result.final_path}")
        if result.qa_text:
            typer.echo(result.qa_text)
        if not result.passed:
            typer.echo(f"QA failed: {result.qa_path}", err=True)
            raise typer.Exit(1)
