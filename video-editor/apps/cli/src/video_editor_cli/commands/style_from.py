"""`ve style-from`: measure a reference video's style grammar (P4.4)."""

from __future__ import annotations

from pathlib import Path

import typer


def register_style_from(app: typer.Typer) -> None:
    """Register the ``style-from`` command."""

    @app.command("style-from")
    def style_from(
        video: Path = typer.Argument(..., exists=True, dir_okay=False),
        out: Path = typer.Option(Path("style.md"), "--out", help="Markdown style guide path."),
        design_out: Path | None = typer.Option(
            None, "--design-out", help="Optional DesignTokens draft JSON path."
        ),
        scene_threshold: float = typer.Option(
            0.3, "--scene-threshold", help="ffmpeg scene score (0–1) that counts as a cut."
        ),
        frames: int = typer.Option(24, "--frames", help="Frames to sample for the palette."),
        colors: int = typer.Option(5, "--colors", help="Palette entries to keep."),
        vision: bool = typer.Option(
            False,
            "--vision",
            help="Add an LLM style interpretation (one vision call; a few cents).",
        ),
    ) -> None:
        """Measure cuts, shot lengths and palette from a reference video.

        The measured grammar is deterministic; the prose interpretation (type
        pairing, camera moves) still needs a vision pass.
        """
        from video_editor_core.style_from import measure_style, write_style

        try:
            profile = measure_style(
                video,
                threshold=scene_threshold,
                frames=frames,
                colors=colors,
            )
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        style, design = write_style(profile, style_path=out, design_path=design_out)
        data = profile.as_dict()
        typer.echo(f"Cuts: {data['cuts']} ({data['cut_rate_per_minute']}/min)")
        typer.echo(f"Palette: {', '.join(profile.palette)}")
        typer.echo(f"Style: {style}")
        if design is not None:
            typer.echo(f"Design draft: {design}")

        if vision:
            _append_interpretation(video, style, frames=frames)


_PROMPT = (
    "Write a style guide for a video in this visual language. Cover palette, "
    "type, shot lengths, transition types, camera moves, grain/film look and how "
    "text enters and exits. Take the grammar, never the content: do not name or "
    "copy logos, brands or on-screen text. Markdown, at most 250 words."
)


def _append_interpretation(video: Path, style: Path, *, frames: int) -> None:
    """One vision call: append the prose reading of the measured grammar."""
    import asyncio
    import tempfile

    from critique.critique import frames_to_images
    from video_editor_core.style_from import sample_frames
    from video_editor_llm.router import ModelRouter

    with tempfile.TemporaryDirectory(prefix="style-vision-") as tmp:
        sampled = sample_frames(video, tmp, frames=min(frames, 8))
        images = frames_to_images(sampled, max_frames=8, cell=768)
    text = asyncio.run(
        ModelRouter.from_config().generate("critique", _PROMPT, images=images)
    ).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text[:-3]
    with style.open("a", encoding="utf-8") as handle:
        handle.write("\n## Interpretation (vision)\n\n" + text.strip() + "\n")
