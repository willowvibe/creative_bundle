"""Speech pace analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def analyze_pace(
    transcript_path: Path | str,
    output_path: Path | str,
    window_seconds: float = 10.0,
) -> dict[str, Any]:
    """Compute words-per-minute over sliding windows."""
    transcript_path = Path(transcript_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    words = transcript.get("words", [])
    if not words:
        data = {"source": str(transcript_path), "windows": [], "overall_wpm": 0.0}
        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data

    start_time = words[0]["start"]
    end_time = words[-1]["end"]
    windows = []
    current = start_time
    while current < end_time:
        window_words = [w for w in words if current <= w["start"] < current + window_seconds]
        duration = min(window_seconds, end_time - current)
        wpm = (len(window_words) / duration) * 60.0 if duration else 0.0
        windows.append(
            {
                "start": current,
                "end": current + duration,
                "word_count": len(window_words),
                "wpm": round(wpm, 2),
            }
        )
        current += window_seconds

    total_duration = end_time - start_time
    overall_wpm = (len(words) / total_duration) * 60.0 if total_duration else 0.0
    data = {
        "source": str(transcript_path),
        "window_seconds": window_seconds,
        "windows": windows,
        "overall_wpm": round(overall_wpm, 2),
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("pace_analysis_complete", overall_wpm=overall_wpm)
    return data
