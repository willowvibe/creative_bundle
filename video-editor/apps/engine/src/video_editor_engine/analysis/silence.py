"""Silence detection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.run import PROBE_TIMEOUT_SECONDS, run_ffmpeg

logger = structlog.get_logger(__name__)


class SilenceDetectionError(Exception):
    """Raised when FFmpeg fails during silence detection."""


def _merge_regions(regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or adjacent regions."""
    if not regions:
        return []
    regions = sorted(regions)
    merged: list[tuple[float, float]] = [regions[0]]
    for start, end in regions[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def detect_silence(
    audio_path: Path | str,
    output_path: Path | str,
    silence_threshold: float = -35.0,
    min_duration: float = 0.3,
    timeout: float | None = 60.0,
) -> dict[str, Any]:
    """Detect silent regions in an audio file using FFmpeg silencedetect."""
    audio_path = Path(audio_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={silence_threshold}dB:d={min_duration}",
        "-f",
        "null",
        "-",
    ]
    logger.info("detecting_silence", input=str(audio_path), threshold=silence_threshold)
    result = run_ffmpeg(
        cmd,
        check=False,
        timeout=timeout if timeout is not None else PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise SilenceDetectionError(
            f"ffmpeg silencedetect failed (rc={result.returncode}): {result.stderr[:512]}"
        )

    # Parse silence_start / silence_end events in stream order, pairing each
    # end with the most recent unpaired start. Any start that never gets an end
    # becomes a region of at least ``min_duration`` so no event is dropped.
    starts: list[float] = []
    regions: list[tuple[float, float]] = []
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].strip().split()[0])
            starts.append(start)
        elif "silence_end:" in line:
            end = float(line.split("silence_end:")[1].strip().split()[0])
            if starts:
                regions.append((starts.pop(), end))

    for start in starts:
        regions.append((start, start + min_duration))

    merged = _merge_regions(regions)
    silences = [{"start": start, "end": end, "duration": end - start} for start, end in merged]

    data = {
        "source": str(audio_path),
        "threshold_db": silence_threshold,
        "min_duration": min_duration,
        "silences": silences,
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def detect_transcript_pauses(
    transcript_path: Path | str,
    output_path: Path | str,
    pause_threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect pauses between words from a word-level transcript."""
    transcript_path = Path(transcript_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    words = transcript.get("words", [])
    pauses: list[dict[str, Any]] = []
    for prev_word, word in zip(words, words[1:], strict=False):
        gap = word["start"] - prev_word["end"]
        if gap >= pause_threshold:
            pauses.append(
                {
                    "start": prev_word["end"],
                    "end": word["start"],
                    "duration": gap,
                    "after_word": prev_word["word"],
                    "before_word": word["word"],
                }
            )

    data = {
        "source": str(transcript_path),
        "pause_threshold": pause_threshold,
        "pauses": pauses,
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("transcript_pauses_complete", pauses=len(pauses))
    return data
