"""`ve voice-clone`: manage Sarvam cloned voices (#312)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import typer
from video_editor_state.paths import default_cache_root


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "voice"


def _store_path(name: str) -> Path:
    return Path(default_cache_root()) / "voice_clones" / f"{_slug(name)}.json"


def register_voice_clone(app: typer.Typer) -> None:
    """Register the ``voice-clone`` command group."""
    voice_app = typer.Typer(help="Create, list and delete Sarvam cloned voices.")

    @voice_app.command("create")
    def create(
        name: str = typer.Option(..., "--name", help="Human-readable voice name."),
        audio: Path = typer.Option(
            ..., "--audio", exists=True, dir_okay=False, help="10–15 s clean reference clip."
        ),
        language: str = typer.Option("en-IN", "--language", help="Reference language code."),
        preview: bool = typer.Option(
            True,
            "--preview/--no-preview",
            help="Synthesize a fixed sentence so the clone can be checked before use.",
        ),
    ) -> None:
        """Create a cloned voice and store its id for pack use."""
        from video_editor_asset_ingestion.providers.tts.sarvam_clone import (
            SarvamCloneError,
            create_voice,
        )

        try:
            created = asyncio.run(create_voice(audio, name=name, language=language))
        except SarvamCloneError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        voice_id = created["voice_id"]
        store = _store_path(name)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
            json.dumps(
                {
                    **created,
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_audio": str(audio),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        typer.echo(f"Created {voice_id}")
        typer.echo(f"Stored: {store}")
        typer.echo("Pack voice:")
        typer.echo(f'  voice_id: "sarvam_clone:{voice_id}"')

        if preview:
            from video_editor_asset_ingestion.models import AssetRequest, AssetType
            from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

            output = store.with_name(f"{store.stem}-preview.mp3")
            request = AssetRequest(
                asset_id="clone_preview",
                job_id="voice-clone",
                scene_id="preview",
                asset_type=AssetType.TTS,
                prompt="This is a preview of the cloned voice reading a short line.",
                provider="sarvam_tts",
                output_ext="mp3",
                params={"voice_id": f"sarvam_clone:{voice_id}", "language": language},
            )
            try:
                asyncio.run(SarvamTTSProvider().generate(request, output))
            except Exception as exc:  # noqa: BLE001 - the voice already exists
                typer.echo(f"Preview failed (the clone is still saved): {exc}", err=True)
            else:
                typer.echo(f"Preview: {output}")

    @voice_app.command("list")
    def list_voices() -> None:
        """List the workspace's cloned voices."""
        from video_editor_asset_ingestion.providers.tts.sarvam_clone import (
            SarvamCloneError,
        )
        from video_editor_asset_ingestion.providers.tts.sarvam_clone import (
            list_voices as fetch,
        )

        try:
            voices = asyncio.run(fetch())
        except SarvamCloneError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        if not voices:
            typer.echo("No cloned voices.")
            return
        for item in voices:
            voice_id = item.get("id") or item.get("voice_id") or "?"
            name = item.get("name") or item.get("voice_name") or ""
            typer.echo(f"{voice_id}\t{name}")

    @voice_app.command("delete")
    def delete(voice_id: str = typer.Argument(..., help="svc-… voice id.")) -> None:
        """Delete a cloned voice and free its slot."""
        from video_editor_asset_ingestion.providers.tts.sarvam_clone import (
            SarvamCloneError,
            delete_voice,
        )

        try:
            asyncio.run(delete_voice(voice_id))
        except SarvamCloneError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(f"Deleted {voice_id}")

    app.add_typer(voice_app, name="voice-clone")
