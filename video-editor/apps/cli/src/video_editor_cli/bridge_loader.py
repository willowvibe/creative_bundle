"""Load the external-editor bridges (FableCut, ComfyStudio) only when a bridge command runs.

The bridges are optional installs (the CLI's ``bridges`` extra; ``uv sync --all-packages`` includes
them). Importing them lazily keeps every other command working without them, and a missing bridge
becomes a one-line hint instead of a traceback at start-up.
"""

from __future__ import annotations

import importlib
from typing import Any

import typer

FABLECUT = "video_editor_fablecut_bridge"
COMFYSTUDIO = "video_editor_comfystudio_bridge"


def bridge(module: str, name: str) -> Any:
    """``name`` from bridge ``module``, or exit 1 with an install hint when the bridge is absent."""
    top = module.split(".", 1)[0]
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing != top and not missing.startswith(f"{top}."):
            raise  # the bridge is installed but one of its own imports failed
        dist = top.replace("_", "-")
        typer.echo(
            f"{dist} is not installed; install it with `uv sync --all-packages` "
            "(or the video-editor-cli `bridges` extra)",
            err=True,
        )
        raise typer.Exit(1) from exc
    return getattr(loaded, name)
