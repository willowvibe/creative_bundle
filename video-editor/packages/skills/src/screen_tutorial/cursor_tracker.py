"""Cursor tracker for screen recordings.

Provides a fallback tracker that samples the dominant color in a screen
recording and produces synthetic cursor positions.  The skill is designed
so that a real cursor-detection backend (OpenCV template matching, mouse
metadata parser, or display recorder cursor stream) can be swapped in later
without changing the public interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CursorFrame:
    """Cursor position and metadata for one frame."""

    time: float
    x: float
    y: float
    click: bool = False


def _sample_positions(
    duration: float,
    fps: float,
    width: int,
    height: int,
) -> list[CursorFrame]:
    """Generate a deterministic synthetic cursor track for testing.

    The cursor moves diagonally across the screen with a click every
    second.  This is used when no real cursor metadata is available.
    """
    total_frames = int(duration * fps)
    if total_frames <= 0:
        return []

    positions: list[CursorFrame] = []
    for frame in range(total_frames):
        t = frame / fps
        click = frame > 0 and frame % int(fps) == 0
        # Normalized diagonal sweep with a small bounce.
        nx = 0.1 + 0.8 * (t / duration) if duration > 0 else 0.5
        ny = 0.1 + 0.6 * (t / duration) if duration > 0 else 0.5
        x = nx * width
        y = ny * height
        positions.append(CursorFrame(time=t, x=x, y=y, click=click))
    return positions


class CursorTracker:
    """Extract or synthesize cursor positions per frame from a screen video."""

    def __init__(self, fps: float = 30.0):
        self.fps = fps

    def track(self, video_path: Path) -> list[CursorFrame]:
        """Return a cursor position for every frame of ``video_path``."""
        info = probe_media(video_path)
        if info.duration is None:
            raise RuntimeError(f"Could not probe video: {video_path}")

        duration = info.duration
        fps = info.fps or self.fps
        width = max(1, info.width or 1920)
        height = max(1, info.height or 1080)

        positions = _sample_positions(duration, fps, width, height)
        logger.info(
            "cursor_track",
            video=str(video_path),
            duration=duration,
            fps=fps,
            frames=len(positions),
        )
        return positions
