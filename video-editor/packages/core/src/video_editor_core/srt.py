"""SubRip sidecar captions from measured word timings (#316)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["srt_from_words"]

MAX_CHARS = 42
MAX_SECONDS = 7.0
_END_PUNCT = ".?!。？！"


def _text(word: Any) -> str:
    if isinstance(word, Mapping):
        return str(word.get("display") or word.get("text") or word.get("word") or "")
    return str(getattr(word, "display", "") or getattr(word, "text", "") or "")


def _start(word: Any) -> float:
    return float(word["start"] if isinstance(word, Mapping) else word.start)


def _end(word: Any) -> float:
    return float(word["end"] if isinstance(word, Mapping) else word.end)


def _stamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def srt_from_words(
    words: Sequence[Any], *, max_chars: int = MAX_CHARS, max_seconds: float = MAX_SECONDS
) -> str:
    """Group word timings into readable cues (≤ ``max_chars``/``max_seconds``)."""
    cues: list[list[Any]] = []
    current: list[Any] = []
    for word in words:
        text = _text(word).strip()
        if not text:
            continue
        if current:
            length = len(" ".join(_text(item) for item in current)) + 1 + len(text)
            too_long = length > max_chars
            too_slow = _end(word) - _start(current[0]) > max_seconds
            if too_long or too_slow:
                cues.append(current)
                current = []
        current.append(word)
        if text.endswith(tuple(_END_PUNCT)):
            cues.append(current)
            current = []
    if current:
        cues.append(current)

    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        text = " ".join(_text(item) for item in cue).strip()
        if not text:
            continue
        blocks.append(f"{index}\n{_stamp(_start(cue[0]))} --> {_stamp(_end(cue[-1]))}\n{text}\n")
    return "\n".join(blocks)
