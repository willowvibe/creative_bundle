"""Shared FFmpeg region parsing helpers.

``silencedetect`` and ``blackdetect`` emit interleaved start/end lines to
stderr.  Multiple skills historically parsed the same output differently; this
module provides one tested implementation so both QA and rough-cut agree on
unpaired events and numeric extraction.
"""

from __future__ import annotations

import re


def parse_ffmpeg_regions(
    stderr: str,
    start_key: str,
    end_key: str,
    trailing_duration: float = 0.1,
) -> list[tuple[float, float]]:
    """Parse start/end timestamp pairs from FFmpeg filter stderr.

    Handles the common case where a video ends in silence/black and FFmpeg
    emits a trailing ``*_start`` without a matching ``*_end``.  Such a region
    is reported with a minimal duration controlled by ``trailing_duration``.

    Args:
        stderr: FFmpeg stderr text containing the start/end lines.
        start_key: Token that precedes the start timestamp, e.g.
            ``"silence_start:"`` or ``"black_start:"``.
        end_key: Token that precedes the end timestamp, e.g.
            ``"silence_end:"`` or ``"black_end:"``.
        trailing_duration: Duration assigned to a trailing unpaired start.

    Returns:
        A list of ``(start, end)`` float tuples, sorted by start time.
    """
    starts: list[float] = []
    ends: list[float] = []
    for line in stderr.splitlines():
        if start_key in line:
            match = re.search(rf"{re.escape(start_key)}\s*([\d.]+)", line)
            if match:
                starts.append(float(match.group(1)))
        if end_key in line:
            match = re.search(rf"{re.escape(end_key)}\s*([\d.]+)", line)
            if match:
                ends.append(float(match.group(1)))

    regions: list[tuple[float, float]] = []
    for start, end in zip(starts, ends, strict=False):
        if end > start:
            regions.append((start, end))

    if len(starts) > len(ends) and starts:
        tail = starts[-1]
        regions.append((tail, tail + trailing_duration))

    return regions
