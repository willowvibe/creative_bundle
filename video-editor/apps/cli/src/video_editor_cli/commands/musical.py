"""CLI command group."""

from __future__ import annotations

from pathlib import Path

import typer

from video_editor_cli import main as cli_main


def register_musical(app: typer.Typer) -> None:
    """Register the musical command."""

    @app.command()
    def musical(
        music: Path = typer.Option(
            ..., "--music", exists=True, dir_okay=False, help="Music file (the sole audio track)."
        ),
        media_dir: Path = typer.Option(
            ..., "--media-dir", exists=True, file_okay=False, help="Folder of stills and clips."
        ),
        lyrics: Path = typer.Option(
            ..., "--lyrics", exists=True, dir_okay=False, help="One caption per non-empty line."
        ),
        output: Path = typer.Option(..., "--output", dir_okay=False, help="Final MP4 path."),
        segment_seconds: float = typer.Option(
            8.0, "--segment-seconds", help="Visible length of one segment."
        ),
        transition_duration: float = typer.Option(
            1.5, "--transition-duration", help="Crossfade length at each boundary."
        ),
        width: int = typer.Option(1920, "--width", help="Canvas width in pixels."),
        height: int = typer.Option(1080, "--height", help="Canvas height in pixels."),
        fps: int = typer.Option(30, "--fps", help="Frame rate."),
        job_id: str = typer.Option("", "--job-id", "-j", help="Override the generated job id."),
        work_root: str = cli_main._WORK_ROOT_OPT,
        output_root: str = cli_main._OUTPUT_ROOT_OPT,
        config_path: Path | None = cli_main._CONFIG_RUN_OPT,
        skip_qa: bool = typer.Option(
            False, "--skip-qa", help="Skip the composition/loudness gates."
        ),
        quantize: str = typer.Option(
            "off",
            "--quantize",
            help="Snap scene cuts to the music: off, beat or downbeat.",
        ),
        beat_tolerance: float = typer.Option(
            0.12,
            "--beat-tolerance",
            help="Maximum distance (seconds) a cut may move to reach a beat.",
        ),
        swell: list[str] = typer.Option(
            [],
            "--swell",
            help="Music gain point 'at:db:duration' (repeatable), e.g. 10:3:2.",
        ),
    ) -> None:
        """Compose a musical video from a music file, a media folder and lyrics."""
        from video_editor_core.musical import MusicalPlanError
        from video_editor_pipelines.explainer_qa import format_qa
        from video_editor_pipelines.musical_pipeline import (
            MusicalPipelineError,
            default_job_id,
            run_musical_job,
        )

        if width <= 0 or height <= 0:
            raise typer.BadParameter("--width and --height must be positive integers")
        if fps <= 0:
            raise typer.BadParameter("--fps must be a positive integer")
        if segment_seconds <= 0:
            raise typer.BadParameter("--segment-seconds must be positive")
        if transition_duration <= 0 or transition_duration >= segment_seconds:
            raise typer.BadParameter(
                "--transition-duration must be positive and less than --segment-seconds"
            )

        swells: list[tuple[float, float, float]] = []
        for raw in swell:
            parts = raw.split(":")
            if len(parts) != 3:
                raise typer.BadParameter(f"--swell wants at:db:duration, got {raw!r}")
            try:
                at, gain_db, duration = (float(part) for part in parts)
            except ValueError as exc:
                raise typer.BadParameter(f"--swell values must be numbers: {raw!r}") from exc
            if at < 0 or duration <= 0:
                raise typer.BadParameter(f"--swell needs at>=0 and duration>0: {raw!r}")
            swells.append((at, gain_db, duration))

        ctx = cli_main._context(
            job_id or default_job_id(), work_root, output_root, config_path=config_path
        )
        try:
            result = run_musical_job(
                ctx,
                music_path=music,
                media_dir=media_dir,
                lyrics_path=lyrics,
                output_path=output,
                width=width,
                height=height,
                fps=fps,
                segment_seconds=segment_seconds,
                transition_seconds=transition_duration,
                run_qa=not skip_qa,
                quantize=quantize,
                beat_tolerance=beat_tolerance,
                swells=swells,
            )
        except (MusicalPipelineError, MusicalPlanError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        typer.echo(f"\nFinal: {result.final_path}")
        typer.echo(f"Plan:  {result.composition_path}")
        if result.qa_path is not None:
            typer.echo(format_qa(list(result.checks)))
            typer.echo(f"QA:    {result.qa_path} ({'PASS' if result.passed else 'FAIL'})")
        if not result.passed:
            raise typer.Exit(1)
