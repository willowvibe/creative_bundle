"""`ve critique`: score a render with a vision model (P4.3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import yaml

from video_editor_cli import main as cli_main


def register_critique(app: typer.Typer) -> None:
    """Register the ``critique`` command."""

    @app.command()
    def critique(
        job_id: str = typer.Argument(..., help="Job whose QA frames should be scored."),
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        frames_dir: Path | None = typer.Option(
            None, "--frames-dir", help="Directory of QA frames (default: <work>/qa/frames)."
        ),
        max_frames: int = typer.Option(12, "--max-frames", help="Keyframes sent to the model."),
        cell: int = typer.Option(384, "--cell", help="Downscaled keyframe size in pixels."),
        pack: Path | None = typer.Option(
            None, "--pack", exists=True, dir_okay=False, help="Optional pack to patch."
        ),
        patched_out: Path | None = typer.Option(
            None, "--patched-out", help="Where to write the patched pack YAML."
        ),
        rebuild: bool = typer.Option(
            False, "--rebuild", help="Apply motion patches and rebuild (spends; needs --yes)."
        ),
        max_rounds: int = typer.Option(2, "--max-rounds", help="Score/patch/rebuild rounds."),
        target: int = typer.Option(8, "--target", help="Stop when every axis reaches this."),
        yes: bool = typer.Option(False, "--yes", help="Acknowledge the rebuild spend."),
    ) -> None:
        """Score the render's design quality and propose structured patches.

        The scores are advisory; the technical QA gates stay authoritative.
        """
        from critique.critique import (
            CritiqueError,
            Patch,
            apply_patches,
            critique_frames,
            critique_loop,
        )
        from video_editor_llm.router import ModelRouter

        ctx = cli_main._context(job_id, work_root, output_root, config_path=config_path)
        directory = frames_dir or (ctx.work_dir / "qa" / "frames")
        if not directory.is_dir():
            typer.echo(f"No QA frames at {directory}; build with QA enabled first.", err=True)
            raise typer.Exit(1)

        router = ModelRouter.from_config(config_path)

        if rebuild:
            if pack is None:
                typer.echo("--rebuild needs --pack so patches can be applied.", err=True)
                raise typer.Exit(2)
            if not yes:
                typer.echo("--rebuild re-renders and can spend; pass --yes.", err=True)
                raise typer.Exit(2)

            from video_editor_core.content_pack import load_content_pack
            from video_editor_pipelines.short_pipeline import preflight_short, run_short_job

            content = load_content_pack(pack)
            counter = {"round": 0}

            def build(patches: list[Patch]) -> tuple[bool, list[str], list[str]]:
                nonlocal content
                updated, applied, skipped = apply_patches(content, patches)
                if not applied:
                    return False, applied, skipped
                counter["round"] += 1
                round_path = ctx.work_dir / "plan" / f"pack_round_{counter['round']}.yaml"
                round_path.parent.mkdir(parents=True, exist_ok=True)
                round_path.write_text(
                    yaml.safe_dump(updated.model_dump(mode="json"), sort_keys=False),
                    encoding="utf-8",
                )
                try:
                    tokens = preflight_short(updated)
                    result = run_short_job(
                        ctx, updated, tokens, config_path=config_path, final=False
                    )
                except Exception as exc:  # noqa: BLE001 - report and stop the loop
                    typer.echo(f"Rebuild failed: {exc}", err=True)
                    return False, applied, skipped
                content = updated
                return result.passed, applied, skipped

            rounds = asyncio.run(
                critique_loop(
                    lambda: list(directory.glob("*.png")) + list(directory.glob("*.jpg")),
                    router,
                    build,
                    max_rounds=max_rounds,
                    target=target,
                )
            )
            rounds_path = ctx.work_dir / "qa" / "critique_rounds.json"
            rounds_path.write_text(
                json.dumps([item.model_dump(mode="json") for item in rounds], indent=2),
                encoding="utf-8",
            )
            for index, round_item in enumerate(rounds, start=1):
                avg = sum(score.score for score in round_item.scores) / max(
                    1, len(round_item.scores)
                )
                typer.echo(
                    f"Round {index}: avg {avg:.2f}; applied {len(round_item.applied)}, "
                    f"skipped {len(round_item.skipped)}"
                )
            if rounds:
                final = rounds[-1]
                for score in final.scores:
                    typer.echo(f"{score.axis:<16} {score.score:>2}  {score.note}")
            typer.echo(f"Rounds: {rounds_path}")
            return

        try:
            report = asyncio.run(
                critique_frames(
                    list(directory.glob("*.png")) + list(directory.glob("*.jpg")),
                    router,
                    max_frames=max_frames,
                    cell=cell,
                )
            )
        except CritiqueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        report_path = ctx.work_dir / "qa" / "critique.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    **report.model_dump(mode="json"),
                    "overall": report.overall,
                    "failing_axes": report.failing_axes(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        typer.echo(f"{'Axis':<16} Score")
        for score in report.scores:
            typer.echo(f"{score.axis:<16} {score.score:>2}  {score.note}")
        typer.echo(f"\nOverall: {report.overall}  (advisory)")
        if report.summary:
            typer.echo(report.summary)
        typer.echo(f"Report: {report_path}")

        if pack is not None and report.patches:
            from video_editor_core.content_pack import load_content_pack

            updated, applied, skipped = apply_patches(load_content_pack(pack), report.patches)
            if applied:
                out = patched_out or pack.with_name(f"{pack.stem}.patched.yaml")
                out.write_text(
                    yaml.safe_dump(updated.model_dump(mode="json"), sort_keys=False),
                    encoding="utf-8",
                )
                typer.echo(f"Patched pack: {out}")
            for change in applied:
                typer.echo(f"  applied: {change}")
            for change in skipped:
                typer.echo(f"  skipped: {change}")
