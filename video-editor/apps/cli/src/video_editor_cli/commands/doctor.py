"""ve doctor: check the external tools a render depends on (A6)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import typer
from video_editor_core.constants import REPO_ROOT
from video_editor_core.hyperframes_cli import resolve_hyperframes_cli
from video_editor_ffmpeg.run import PROBE_TIMEOUT_SECONDS, CommandError, run_subprocess


def _version(command: list[str]) -> str | None:
    try:
        result = run_subprocess(command, timeout=PROBE_TIMEOUT_SECONDS)
    except (CommandError, FileNotFoundError, OSError):
        return None
    return (
        (result.stdout or result.stderr or "").strip().splitlines()[0]
        if (result.stdout or result.stderr)
        else ""
    )


def collect_tools() -> list[dict[str, Any]]:
    """Return the tools a render depends on and whether they are usable."""
    checks: list[dict[str, Any]] = []

    ffmpeg = shutil.which("ffmpeg")
    ffmpeg_version = _version([ffmpeg, "-version"]) if ffmpeg else None
    filters = ""
    if ffmpeg:
        try:
            filters = run_subprocess([ffmpeg, "-filters"], timeout=PROBE_TIMEOUT_SECONDS).stdout
        except (CommandError, FileNotFoundError, OSError):
            filters = ""
    required_filters = ("xfade", "loudnorm", "subtitles")
    missing_filters = [name for name in required_filters if name not in filters]
    checks.append(
        {
            "name": "ffmpeg",
            "path": ffmpeg,
            "version": ffmpeg_version,
            "ok": bool(ffmpeg) and not missing_filters,
            "detail": "missing filters: " + ", ".join(missing_filters) if missing_filters else "ok",
        }
    )

    local_bin = REPO_ROOT / "node_modules" / ".bin" / "hyperframes"
    hyperframes = shutil.which(resolve_hyperframes_cli("hyperframes"))
    pin: str | None = None
    package_json = REPO_ROOT / "package.json"
    try:
        pin = (
            json.loads(package_json.read_text(encoding="utf-8"))
            .get("devDependencies", {})
            .get("hyperframes")
        )
    except (OSError, ValueError):
        pin = None
    if hyperframes and local_bin.is_file() and Path(hyperframes) == local_bin:
        hyperframes_detail = f"local bin, pinned {pin or 'version unknown'}"
    elif hyperframes:
        hyperframes_detail = (
            f"global install; package.json pins {pin} (run pnpm install to use the local bin)"
            if pin
            else "global install"
        )
    else:
        hyperframes_detail = "not installed (run pnpm install)"
    checks.append(
        {
            "name": "hyperframes",
            "path": hyperframes,
            "version": _version([hyperframes, "--version"]) if hyperframes else None,
            "ok": hyperframes is not None,
            "detail": hyperframes_detail,
        }
    )

    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    checks.append(
        {
            "name": "chrome",
            "path": chrome,
            "version": _version([chrome, "--version"]) if chrome else None,
            "ok": chrome is not None,
            "detail": "required by HyperFrames" if chrome else "not installed",
        }
    )

    agy = shutil.which("agy")
    checks.append(
        {
            "name": "agy",
            "path": agy,
            "version": _version([agy, "--version"]) if agy else None,
            "ok": agy is not None,
            "detail": "optional draft image agent" if agy else "not installed",
        }
    )
    return checks


def register_doctor(app: typer.Typer) -> None:
    """Register ``doctor`` on the Typer app."""

    @app.command()
    def doctor(json_output: bool = typer.Option(False, "--json", help="Print JSON")) -> None:
        """Check ffmpeg filters, HyperFrames, Chrome and agy."""
        checks = collect_tools()
        if json_output:
            typer.echo(json.dumps(checks, indent=2))
        else:
            for check in checks:
                mark = "OK" if check["ok"] else "MISSING"
                typer.echo(
                    f"{check['name']:<12} {mark:<8} {check['version'] or '-'}  {check['detail']}"
                )
        if not all(check["ok"] for check in checks if check["name"] in {"ffmpeg", "hyperframes"}):
            raise typer.Exit(1)
