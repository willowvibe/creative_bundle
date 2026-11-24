"""Hybrid lip-sync timeline: Rhubarb timing plus measured phoneme hints.

The hybrid mode starts from the normal Rhubarb timeline (one frame later), moves
every lip closure onto a wav2vec2-measured M/B/P moment, then inserts closures,
open vowels and rounded mouths that the word's phones require.  The measured
hints come from :mod:`talking_avatar.align_script` or a JSON file supplied by
the caller; this module is pure Python and needs neither torch nor g2p_en.

Ported from the approved "C hybrid" trial.  The alignment script lives in an
optional environment so it never becomes a runtime dependency::

    uv venv .venv-lipsync
    uv pip install --python .venv-lipsync/bin/python torch \\
        --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from talking_avatar.rhubarb import (
    DEFAULT_CLOSURE_MIN_FRAMES,
    DEFAULT_FPS,
    DEFAULT_LEAD,
    DEFAULT_RMS_FPS,
    DEFAULT_STRESS_PERCENTILE,
    MouthCue,
    build_rhubarb_timeline,
    enforce_closure_hold,
    merge_single_frame_runs,
)
from talking_avatar.timeline import VisemeFrame

HINTS_VERSION = 1
BILABIAL = frozenset({"M", "B", "P"})
OPEN = frozenset({"AA", "AE", "AH", "AO", "AW", "AY", "EH", "EY", "ER"})
ROUND = frozenset({"OW", "UW", "UH", "OY", "W"})
OPEN_SHAPES = frozenset("CDEH")
ROUND_SHAPES = frozenset("EF")
CLOSURE_FRAMES = 2
CLOSURE_LEAD = 0.0


class HintsError(ValueError):
    """Raised when an alignment-hints payload is missing or malformed."""


@dataclass(frozen=True)
class HintWord:
    """One aligned word from the hints file."""

    text: str
    start: float
    end: float
    phones: tuple[str, ...]


@dataclass(frozen=True)
class AlignHints:
    """Validated output of ``align_script.py``."""

    bilabial: tuple[float, ...]
    words: tuple[HintWord, ...]


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HintsError(f"{field} must be a number, got {value!r}")
    return float(value)


def _hint_word(item: Any) -> HintWord:
    if not isinstance(item, Mapping):
        raise HintsError(f"each word must be an object, got {item!r}")
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HintsError(f"word text must be a non-empty string, got {text!r}")
    phones = item.get("phones")
    if not isinstance(phones, list | tuple) or not all(isinstance(phone, str) for phone in phones):
        raise HintsError(f"phones for {text!r} must be a list of strings")
    start = _number(item.get("start"), f"start for {text!r}")
    end = _number(item.get("end"), f"end for {text!r}")
    if end < start:
        raise HintsError(f"end for {text!r} is before its start")
    return HintWord(text=text, start=start, end=end, phones=tuple(phones))


def parse_hints(payload: Any) -> AlignHints:
    """Validate a hints mapping and return :class:`AlignHints`."""
    if not isinstance(payload, Mapping):
        raise HintsError(f"alignment hints must be an object, got {type(payload).__name__}")
    version = payload.get("version")
    if isinstance(version, bool) or version != HINTS_VERSION:
        raise HintsError(
            f"unsupported alignment hints version {version!r}; expected {HINTS_VERSION}"
        )
    raw_marks = payload.get("bilabial")
    if not isinstance(raw_marks, list | tuple):
        raise HintsError("bilabial must be a list of seconds")
    marks = tuple(_number(value, "bilabial mark") for value in raw_marks)
    raw_words = payload.get("words")
    if not isinstance(raw_words, list):
        raise HintsError("words must be a list")
    return AlignHints(bilabial=marks, words=tuple(_hint_word(item) for item in raw_words))


def load_hints(path: Path | str | Mapping[str, Any]) -> AlignHints:
    """Load and validate alignment hints from a JSON file or mapping."""
    if isinstance(path, Mapping):
        return parse_hints(path)
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HintsError(f"Could not read alignment hints {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HintsError(f"Invalid alignment hints JSON in {source}: {exc}") from exc
    return parse_hints(payload)


def base(phone: str) -> str:
    """Strip the ARPAbet stress digit."""
    return re.sub(r"\d", "", phone)


def is_stressed_open(phone: str) -> bool:
    """True for a stressed open vowel (``AA1``, ``EY2``, ...)."""
    return base(phone) in OPEN and phone.endswith(("1", "2"))


def slots(
    word_phones: Sequence[str],
    start: float,
    end: float,
) -> list[tuple[str, float, float]]:
    """Spread a word's duration over its sounds; vowels weigh twice consonants."""
    weights = [2.0 if re.search(r"\d", phone) else 1.0 for phone in word_phones]
    total = sum(weights) or 1.0
    out: list[tuple[str, float, float]] = []
    cursor = start
    for phone, weight in zip(word_phones, weights, strict=True):
        duration = (end - start) * weight / total
        out.append((phone, cursor, cursor + duration))
        cursor += duration
    return out


def frames_in(shapes: Sequence[str], fps: float, start: float, end: float) -> range:
    return range(max(0, int(start * fps)), min(len(shapes), int(end * fps) + 1))


def aligner_closures(
    shapes: Sequence[str],
    bilabial_marks: Sequence[float],
    fps: float = DEFAULT_FPS,
    lead: float = CLOSURE_LEAD,
    frames: int = CLOSURE_FRAMES,
) -> list[str]:
    """Take lip closures from measured M/B/P times instead of Rhubarb.

    Every Rhubarb ``A`` becomes ``B`` (lips nearly closed), then an ``A`` of
    ``frames`` frames is centred on each measured M/B/P moment, shifted
    ``lead`` seconds early.  Open shapes, puckers and F/V stay as Rhubarb timed
    them.
    """
    out = ["B" if shape == "A" else shape for shape in shapes]
    for mark in bilabial_marks:
        centre = int(round((float(mark) - lead) * fps))
        for index in range(centre - frames // 2, centre - frames // 2 + frames):
            if 0 <= index < len(out):
                out[index] = "A"
    return out


def _coerce_word(word: HintWord | Mapping[str, Any]) -> HintWord:
    return word if isinstance(word, HintWord) else _hint_word(word)


def fix(
    shapes: Sequence[str],
    words: Sequence[HintWord | Mapping[str, Any]],
    *,
    fps: float = DEFAULT_FPS,
) -> tuple[list[str], dict[str, int]]:
    """Insert the mouth a word's sounds require when Rhubarb left it out.

    The phones come from the hints file.  Sounds are spread across the word by
    :func:`slots`; an existing correct shape is never removed.  Returns the new
    shapes and a count of each inserted mouth type.
    """
    out = list(shapes)
    counts = {"closure": 0, "open": 0, "rounded": 0}
    for raw_word in words:
        word = _coerce_word(raw_word)
        phone_list = word.phones
        if not phone_list:
            continue
        sound_slots = slots(phone_list, word.start, word.end)
        got = {out[index] for index in frames_in(out, fps, word.start, word.end)}
        bases = {base(phone) for phone in phone_list}
        if bases & BILABIAL and "A" not in got:
            _phone, start, end = next(item for item in sound_slots if base(item[0]) in BILABIAL)
            middle = int(round((start + end) / 2 * fps))
            for index in (middle - 1, middle):
                if 0 <= index < len(out):
                    out[index] = "A"
            counts["closure"] += 1
        if any(is_stressed_open(phone) for phone in phone_list) and not got & OPEN_SHAPES:
            _phone, start, end = next(item for item in sound_slots if is_stressed_open(item[0]))
            for index in frames_in(out, fps, start, end):
                if out[index] in {"B", "X"}:
                    out[index] = "C"
            counts["open"] += 1
        if bases & ROUND and not got & ROUND_SHAPES:
            phone, start, end = next(item for item in sound_slots if base(item[0]) in ROUND)
            for index in frames_in(out, fps, start, end):
                if out[index] in {"B", "X", "C"}:
                    out[index] = "F" if base(phone) in {"UW", "W", "UH"} else "E"
            counts["rounded"] += 1
    return out, counts


def _word_key(text: str) -> str:
    return re.sub(r"[^A-Za-z']", "", text).lower()


def _word_fields(word: Any) -> tuple[str, float, float]:
    if isinstance(word, Mapping):
        return str(word["text"]), float(word["start"]), float(word["end"])
    if isinstance(word, Sequence) and not isinstance(word, str):
        return str(word[0]), float(word[1]), float(word[2])
    return str(word.text), float(word.start), float(word.end)


def words_with_phones(words: Sequence[Any], hints: AlignHints) -> list[HintWord]:
    """Give each timed word the phones the hints recorded for the same text.

    Words the aligner never saw get no phones and are skipped by :func:`fix`.
    """
    phones_by_text: dict[str, tuple[str, ...]] = {}
    for hint in hints.words:
        phones_by_text.setdefault(_word_key(hint.text), hint.phones)
    out: list[HintWord] = []
    for word in words:
        text, start, end = _word_fields(word)
        if not text.strip() or end < start:
            continue
        out.append(HintWord(text, start, end, phones_by_text.get(_word_key(text), ())))
    return out


def build_hybrid_timeline(
    cues: Sequence[MouthCue | Mapping[str, Any] | Sequence[Any]],
    hints: AlignHints,
    *,
    duration: float,
    fps: float = DEFAULT_FPS,
    lead: float = DEFAULT_LEAD,
    closure_min_frames: int = DEFAULT_CLOSURE_MIN_FRAMES,
    envelope: Sequence[float] | None = None,
    rms_fps: float = DEFAULT_RMS_FPS,
    bounce: bool = False,
    stress_open: bool = True,
    stress_percentile: float = DEFAULT_STRESS_PERCENTILE,
    stats: dict[str, Any] | None = None,
    words: Sequence[Any] | None = None,
) -> list[VisemeFrame]:
    """Build a hybrid timeline from Rhubarb ``cues`` and measured ``hints``.

    The normal Rhubarb timeline is delayed by exactly one frame, its closures
    are moved onto the measured M/B/P marks, missing mouths are inserted per
    word, and then the engine's closure hold and single-frame merge run again.

    ``words`` are the engine's word timings (objects or tuples with text, start
    and end; Whisper in practice).  The approved trial placed the per-word fixes
    on those spans with phones looked up from the hints, so they are preferred;
    without them the hints' own wav2vec2 word spans are used.
    """
    base_frames = build_rhubarb_timeline(
        cues,
        duration=duration,
        fps=fps,
        lead=lead,
        closure_min_frames=closure_min_frames,
        envelope=envelope,
        rms_fps=rms_fps,
        bounce=bounce,
        stress_open=stress_open,
        stress_percentile=stress_percentile,
        stats=stats,
    )
    if not base_frames:
        return []

    rhubarb_shapes = [frame.shape for frame in base_frames]
    delayed = ["X", *rhubarb_shapes[:-1]]
    shapes = aligner_closures(
        delayed,
        hints.bilabial,
        fps=fps,
        lead=CLOSURE_LEAD,
        frames=CLOSURE_FRAMES,
    )
    fix_words = words_with_phones(words, hints) if words else list(hints.words)
    shapes, counts = fix(shapes, fix_words, fps=fps)
    shapes = enforce_closure_hold(shapes, closure_min_frames)
    shapes = merge_single_frame_runs(shapes)

    if stats is not None:
        stats["hybrid_closure_fixes"] = counts["closure"]
        stats["hybrid_open_fixes"] = counts["open"]
        stats["hybrid_rounded_fixes"] = counts["rounded"]

    return [
        VisemeFrame(
            frame=frame.frame,
            time=frame.time,
            shape=shape,
            rms=frame.rms,
            word=frame.word,
            openness=frame.openness,
        )
        for frame, shape in zip(base_frames, shapes, strict=True)
    ]


__all__ = [
    "BILABIAL",
    "CLOSURE_FRAMES",
    "CLOSURE_LEAD",
    "HINTS_VERSION",
    "OPEN",
    "OPEN_SHAPES",
    "ROUND",
    "ROUND_SHAPES",
    "AlignHints",
    "HintWord",
    "HintsError",
    "aligner_closures",
    "base",
    "build_hybrid_timeline",
    "fix",
    "frames_in",
    "is_stressed_open",
    "load_hints",
    "parse_hints",
    "slots",
    "words_with_phones",
]
