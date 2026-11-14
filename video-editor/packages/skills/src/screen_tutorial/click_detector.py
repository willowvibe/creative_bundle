"""Click detection for screen recordings.

Combines two weak signals into a click decision:
- audio spikes (a keyboard/mouse click produces a short, sharp sound), and
- cursor dwell / jitter (a click usually pauses the cursor near the target).

When no audio stream is present the detector falls back to cursor-dwell only,
so the skill still works on silent screen captures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, FFmpegError, run_ffmpeg

from screen_tutorial.cursor_tracker import CursorFrame

logger = structlog.get_logger(__name__)

AUDIO_WINDOW_SECONDS = 0.25


@dataclass(frozen=True)
class ClickEvent:
    """A single detected click event."""

    time: float
    x: float
    y: float


def _audio_spikes(wav_path: Path) -> list[tuple[float, float]]:
    """Return time ranges with elevated audio energy.

    Uses ffmpeg's ``silencedetect`` to find loud windows in reverse: we probe
    with a low ``noise`` threshold so every short click surfaces as a region
    boundary, then return windows where audio is present between silences.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(wav_path),
        "-af",
        "silencedetect=noise=-30dB:d=0.05",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
    except (FFmpegError, FileNotFoundError):
        return []

    # Parse silence_start / silence_end markers into loud windows.
    silence_end_times: list[float] = []
    silence_start_times: list[float] = []
    for line in result.stderr.splitlines():
        if "silence_start" in line:
            try:
                silence_start_times.append(float(line.split("silence_start: ")[1].split(" ")[0]))
            except (IndexError, ValueError):
                continue
        elif "silence_end" in line:
            try:
                silence_end_times.append(float(line.split("silence_end: ")[1].split(" ")[0]))
            except (IndexError, ValueError):
                continue

    if not silence_start_times:
        return []

    # Loud windows: from a silence_end to the next silence_start.
    loud: list[tuple[float, float]] = []
    for end in silence_end_times:
        following = [s for s in silence_start_times if s > end]
        if following:
            loud.append((end, following[0]))
    return loud


def detect_clicks(
    cursor_frames: list[CursorFrame],
    *,
    audio_path: Path | None = None,
    dwell_threshold_seconds: float = 0.15,
) -> list[ClickEvent]:
    """Detect click events from cursor frames and optional audio spikes.

    A click is registered when the cursor stays within a small radius for
    ``dwell_threshold_seconds`` and (if audio is available) an audio spike
    overlaps the dwell window.
    """
    if not cursor_frames:
        return []

    windows = _audio_spikes(audio_path) if audio_path and audio_path.exists() else []

    clicks: list[ClickEvent] = []
    dwell_start: int | None = None
    dwell_start_pos = cursor_frames[0]

    for idx, frame in enumerate(cursor_frames):
        dx = frame.x - dwell_start_pos.x
        dy = frame.y - dwell_start_pos.y
        moved = (dx * dx + dy * dy) > 9.0  # ~3px radius in screen pixels
        if not moved and dwell_start is None:
            dwell_start = idx
            dwell_start_pos = frame
        elif moved:
            if dwell_start is not None:
                duration = frame.time - cursor_frames[dwell_start].time
                if duration >= dwell_threshold_seconds and _has_audio_overlap(
                    cursor_frames[dwell_start].time, duration, windows
                ):
                    clicks.append(
                        ClickEvent(
                            time=cursor_frames[dwell_start].time,
                            x=dwell_start_pos.x,
                            y=dwell_start_pos.y,
                        )
                    )
            dwell_start = None
            dwell_start_pos = frame

    # Trailing dwell at the end of the video.
    if dwell_start is not None:
        duration = cursor_frames[-1].time - cursor_frames[dwell_start].time
        if duration >= dwell_threshold_seconds:
            clicks.append(
                ClickEvent(
                    time=cursor_frames[dwell_start].time,
                    x=dwell_start_pos.x,
                    y=dwell_start_pos.y,
                )
            )

    return clicks


def _has_audio_overlap(start: float, duration: float, windows: list[tuple[float, float]]) -> bool:
    """Return True when any loud audio window overlaps [start, start+duration]."""
    if not windows:
        # No audio signal available — accept the dwell as a click.
        return True
    return any(w_end >= start and w_start <= start + duration for w_start, w_end in windows)
