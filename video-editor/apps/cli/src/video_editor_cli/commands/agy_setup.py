"""ve agy-setup: create the restricted HOME used by the image agent."""

from __future__ import annotations

import typer
from video_editor_asset_ingestion.providers.visual.agy_profile import (
    DEFAULT_PROFILE_DIR,
    auth_ready,
    ensure_profile,
)

_PROFILE_DIR_OPT = typer.Option(
    DEFAULT_PROFILE_DIR,
    "--profile-dir",
    help="Directory used as HOME for the image agent",
)


def register_agy_setup(app: typer.Typer) -> None:
    """Register ``agy-setup`` on the Typer app."""

    @app.command(name="agy-setup")
    def agy_setup(profile_dir: str = _PROFILE_DIR_OPT) -> None:
        """Install the restricted settings and PreToolUse guard for agy.

        The profile is a dedicated HOME: auth files are symlinked from the
        real ``~/.gemini``, shell commands are denied, and the hook permits
        only ``generate_image``, ``finish`` and writes inside the turn
        workspace. Run this once per machine after logging in with ``agy``.
        """
        profile = ensure_profile(profile_dir)
        typer.echo(f"agy profile: {profile}")
        typer.echo(f"  settings: {profile / '.gemini' / 'antigravity-cli' / 'settings.json'}")
        typer.echo(f"  guard:    {profile / 'agy_guard.py'}")
        if auth_ready(profile):
            typer.echo("  auth:     OAuth credentials linked")
        else:
            typer.echo(
                "  auth:     MISSING; run `agy` once to sign in, then re-run `ve agy-setup`",
                err=True,
            )
        typer.echo(
            "  Set assets.providers.agy_image.provider_options.profile_dir to "
            f"{profile} (or leave the default)."
        )
