"""`ve talking-avatar`: overlay a lip-synced cartoon avatar on a recording.

The default ``hybrid`` engine uses Rhubarb for open/puckered mouth shapes and
moves lip closures onto wav2vec2-measured M/B/P moments, filling in missing
open and rounded vowels from each word's phones.  It needs ``--script`` (or a
precomputed ``--align-hints`` JSON).  The optional aligner environment is not a
runtime dependency; create it once with::

    uv venv .venv-lipsync
    uv pip install --python .venv-lipsync/bin/python torch \
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy

If the interpreter is missing, hybrid logs the setup command and falls back to
plain Rhubarb.  ``--lipsync rhubarb`` and ``--lipsync loudness`` still work.
"""

from __future__ import annotations

from pathlib import Path

import typer


def register_talking_avatar(app: typer.Typer) -> None:
    """Register the ``talking-avatar`` command."""

    @app.command(name="talking-avatar")
    def talking_avatar(
        screen: Path = typer.Option(
            ...,
            "--screen",
            exists=True,
            dir_okay=False,
            help="Screen recording to place the avatar on.",
        ),
        audio: Path = typer.Option(
            ...,
            "--audio",
            exists=True,
            dir_okay=False,
            help="Voice audio that drives the lip sync.",
        ),
        out: Path = typer.Option(
            ...,
            "--out",
            dir_okay=False,
            help="Output MP4 path.",
        ),
        avatar: Path | None = typer.Option(
            None,
            "--avatar",
            exists=True,
            help=(
                "Layered avatar SVG, or a folder of PNG sprites "
                "(default: templates/avatars/bolt/bolt.svg)."
            ),
        ),
        position: str = typer.Option(
            "br",
            "--position",
            help="Corner for the avatar: br, bl, tr or tl.",
        ),
        size: float = typer.Option(
            0.30,
            "--size",
            help="Avatar height as a fraction of the output height.",
        ),
        margin: int = typer.Option(
            32,
            "--margin",
            help="Distance from the screen edge in pixels.",
        ),
        seed: int = typer.Option(
            0,
            "--seed",
            help="Random seed for blink timing.",
        ),
        lipsync: str = typer.Option(
            "hybrid",
            "--lipsync",
            help=(
                "Lip-sync engine: hybrid (default), rhubarb or loudness. "
                "Hybrid moves Rhubarb closures onto measured M/B/P times and "
                "fixes missing vowels; it needs --script or --align-hints, plus "
                "the optional .venv-lipsync aligner environment."
            ),
        ),
        script: Path | None = typer.Option(
            None,
            "--script",
            exists=True,
            dir_okay=False,
            help=(
                "Dialogue text (passed to Rhubarb with -d and aligned by hybrid). "
                "Required by hybrid unless --align-hints is given."
            ),
        ),
        align_hints: Path | None = typer.Option(
            None,
            "--align-hints",
            exists=True,
            dir_okay=False,
            help=(
                "Precomputed hybrid alignment hints JSON (skips the align_script.py subprocess)."
            ),
        ),
        lead: float = typer.Option(
            0.02,
            "--lead",
            help="Show Rhubarb cues this many seconds early.",
        ),
        no_crossfade: bool = typer.Option(
            False,
            "--no-crossfade",
            help="Disable the one-frame blend between mouth shapes.",
        ),
        bounce: bool = typer.Option(
            False,
            "--bounce",
            help="Enable the sine head bob and loudness lift.",
        ),
        no_motion: bool = typer.Option(
            False,
            "--no-motion",
            help="Disable breathing, tilts and the entrance/exit slide.",
        ),
        stress_open: bool = typer.Option(
            True,
            "--stress-open/--no-stress-open",
            help="Open the mouth wider (D) on loud syllables.",
        ),
        stress_percentile: float = typer.Option(
            80.0,
            "--stress-percentile",
            help="dB percentile treated as a stressed syllable.",
        ),
        bubbles: Path | None = typer.Option(
            None,
            "--bubbles",
            exists=True,
            dir_okay=False,
            help="YAML file of speech bubbles timed to words or seconds.",
        ),
    ) -> None:
        """Put a lip-synced cartoon avatar in the corner of a screen recording."""
        from talking_avatar.talking_avatar import (
            TalkingAvatarError,
            TalkingAvatarSkill,
        )

        skill = TalkingAvatarSkill()
        try:
            result = skill.run(
                screen,
                audio,
                out,
                avatar_path=avatar,
                position=position,
                size=size,
                margin=margin,
                seed=seed,
                lipsync=lipsync,
                script=script,
                align_hints=align_hints,
                lead=lead,
                crossfade=not no_crossfade,
                bounce=bounce,
                motion=not no_motion,
                stress_open=stress_open,
                stress_percentile=stress_percentile,
                bubbles=bubbles,
            )
        except (TalkingAvatarError, FileNotFoundError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        typer.echo(f"Output:   {result['output']}")
        typer.echo(f"Timeline: {result['timeline']}")
