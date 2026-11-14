"""Record the external tool versions a render used (A6)."""

from __future__ import annotations

import shutil
from functools import lru_cache

from video_editor_ffmpeg.run import CommandError, run_subprocess


def _version(command: list[str]) -> str | None:
    try:
        result = run_subprocess(command, timeout=10.0)
    except (CommandError, FileNotFoundError, OSError):
        return None
    first = (result.stdout or result.stderr or "").strip().splitlines()
    return first[0] if first else None


@lru_cache(maxsize=1)
def collect_tool_versions() -> dict[str, str | None]:
    """Best-effort versions for the binaries that affect a render."""
    tools = {
        "ffmpeg": shutil.which("ffmpeg"),
        "hyperframes": shutil.which("hyperframes"),
        "chrome": shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser"),
        "agy": shutil.which("agy"),
    }
    return {
        name: _version([path, "--version" if name != "ffmpeg" else "-version"]) if path else None
        for name, path in tools.items()
    }
