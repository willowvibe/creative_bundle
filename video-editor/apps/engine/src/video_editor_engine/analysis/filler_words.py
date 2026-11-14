"""Filler word detection from transcript."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_FILLERS = [
    "um",
    "umm",
    "uh",
    "uuh",
    "ah",
    "aah",
    "eh",
    "er",
    "erm",
    "like",
    "you know",
    "so",
    "actually",
    "basically",
    "literally",
    "sort of",
    "kind of",
    "i mean",
]


def detect_filler_words(
    transcript_path: Path | str,
    output_path: Path | str,
    fillers: list[str] | None = None,
) -> dict[str, Any]:
    """Find filler words in a word-level transcript."""
    transcript_path = Path(transcript_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    fillers = fillers or DEFAULT_FILLERS
    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    words = transcript.get("words", [])
    # C36: DEFAULT_FILLERS contains multi-word phrases ("you know",
    # "sort of", ...). We scan n-grams so those phrases are actually
    # detected instead of silently missed.
    fillers_set = set(fillers)
    max_n = max(len(phrase.split()) for phrase in fillers_set) if fillers_set else 1

    matches = []
    i = 0
    while i < len(words):
        matched = False
        window_max = min(max_n, len(words) - i)
        for n in range(window_max, 0, -1):
            phrase = " ".join(w["word"].strip().lower().strip(".,!?;") for w in words[i : i + n])
            if phrase in fillers_set:
                for w in words[i : i + n]:
                    matches.append(
                        {
                            "word": w["word"],
                            "start": w["start"],
                            "end": w["end"],
                            "probability": w.get("probability"),
                        }
                    )
                i += n
                matched = True
                break
        if not matched:
            i += 1

    data = {
        "source": str(transcript_path),
        "fillers": fillers,
        "matches": matches,
        "count": len(matches),
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("filler_detection_complete", count=len(matches))
    return data
