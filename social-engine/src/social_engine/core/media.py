"""Minimal FFprobe helper used by manifest validation and drafting.

The ported code imported ``video_editor_ffmpeg.probe``; social-engine may not
import video-editor, so this is the local replacement: one ``ffprobe -print_format
json`` call with a 30 second timeout, returning only what validation needs
(width, height, duration).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

PROBE_TIMEOUT_SECONDS = 30.0


class MediaInfoError(Exception):
    """FFprobe could not read the file, or its output was unusable."""


class MediaInfo:
    """The parts of an FFprobe report the publishers care about."""

    def __init__(self, path: Path | str, payload: dict[str, Any]) -> None:
        self.path = Path(path)
        self._format: dict[str, Any] = payload.get("format", {})
        streams = payload.get("streams", [])
        self.video_stream: dict[str, Any] | None = next(
            (stream for stream in streams if stream.get("codec_type") == "video"), None
        )

    @property
    def width(self) -> int:
        if not self.video_stream:
            return 0
        return int(self.video_stream.get("width", 0))

    @property
    def height(self) -> int:
        if not self.video_stream:
            return 0
        return int(self.video_stream.get("height", 0))

    @property
    def duration(self) -> float | None:
        duration = self._format.get("duration")
        if duration is not None:
            return float(duration)
        if self.video_stream and "duration" in self.video_stream:
            return float(self.video_stream["duration"])
        return None


def probe_media(path: Path | str) -> MediaInfo:
    """Run FFprobe on ``path`` and return its shape.

    Raises :class:`MediaInfoError` when FFprobe is missing, fails, takes longer
    than 30 seconds, or prints something other than JSON.
    """
    binary = shutil.which("ffprobe")
    if binary is None:
        raise MediaInfoError("ffprobe is not installed or not on PATH")
    argv = [
        binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaInfoError(f"could not run ffprobe on {path}: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {result.returncode}"
        raise MediaInfoError(f"ffprobe failed for {path}: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaInfoError(f"ffprobe printed invalid JSON for {path}") from exc
    if not isinstance(payload, dict):
        raise MediaInfoError(f"ffprobe printed unexpected JSON for {path}")
    return MediaInfo(path, payload)
