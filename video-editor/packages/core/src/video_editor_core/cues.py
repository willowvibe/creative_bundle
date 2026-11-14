"""Narration cues: time visuals to the moment a word is spoken.

Explainers hold attention when the picture reacts to the voice — an arrow
appears on "pulls", a call-out lights up on "calcium" — instead of drawing a
whole diagram at scene start and then sitting still. Every pipeline that has
word timestamps (faster-whisper, ElevenLabs forced alignment, WhisperX) can
use these helpers to turn *phrases* into *times*:

* :func:`find_cue` / :func:`resolve_cues` map a phrase to the start time of
  its first spoken occurrence (case- and punctuation-insensitive, multi-word).
* :func:`spotlight_layer` builds a render-graph ``shape`` layer that rings a
  region of the frame from a cue until the scene (or the next cue) ends. The
  HyperFrames compiler draws it as a glowing outline that fades in and out.

Word dicts may use ``word`` or ``text`` for the token and must carry
``start``/``end`` seconds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from video_editor_core.render_graph import Layer, Rect, Timing

_TOKEN_RE = re.compile(r"[^\w%]+", re.UNICODE)
_SPLIT_PERCENT_RE = re.compile(r"^(\d+(?:\.\d+)?)%$")

_UNITS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
}


def _norm(token: str) -> str:
    return _TOKEN_RE.sub("", token.lower())


def _canonical(tokens: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Normalise a token stream so spelled and transcribed forms compare equal.

    Speech-to-text writes "sixty percent" as ``60`` ``%`` (or ``60%``) while
    a script says "sixty percent". Runs of number words fold into one digit
    token (keeping the run's start time), ``percent`` becomes ``%`` and
    ``60%`` splits into ``60`` ``%``. Scale words on their own ("billions")
    stay words, so ``"billions"`` still matches "billions".
    """
    out: list[tuple[str, float]] = []
    i = 0
    while i < len(tokens):
        text, start = tokens[i]
        if text in _UNITS:
            total, current, j = 0, 0, i
            while j < len(tokens) and (tokens[j][0] in _UNITS or tokens[j][0] in _SCALES):
                word = tokens[j][0]
                if word in _UNITS:
                    current += _UNITS[word]
                elif word == "hundred":
                    current = max(current, 1) * 100
                else:
                    total += max(current, 1) * _SCALES[word]
                    current = 0
                j += 1
            out.append((str(total + current), start))
            i = j
            continue
        match = _SPLIT_PERCENT_RE.match(text)
        if match:
            out.extend([(match.group(1), start), ("%", start)])
        elif text == "percent":
            out.append(("%", start))
        else:
            out.append((text, start))
        i += 1
    return out


def _word_text(word: Mapping[str, Any]) -> str:
    return str(word.get("word", word.get("text", "")))


def find_cue(
    words: Iterable[Mapping[str, Any]],
    phrase: str,
    *,
    after: float = 0.0,
    before: float | None = None,
    prefix: bool = True,
) -> float | None:
    """Return the start time of the first spoken ``phrase`` in ``[after, before)``.

    Matching ignores case and punctuation, and numbers compare by value, so
    ``"sixty percent"`` matches a transcript's ``60`` ``%``. With
    ``prefix=True`` each phrase token may match the start of a spoken token,
    so ``"orbit"`` also hits "orbits"; numeric tokens always match exactly.
    Returns ``None`` when the phrase is never spoken in the window.
    """
    targets = [t for t, _ in _canonical([(n, 0.0) for n in map(_norm, phrase.split()) if n])]
    if not targets:
        return None
    tokens = _canonical(
        [(text, float(w.get("start", 0.0))) for w in words if (text := _norm(_word_text(w)))]
    )
    for i in range(len(tokens) - len(targets) + 1):
        start = tokens[i][1]
        if start < after - 1e-6 or (before is not None and start >= before):
            continue
        window = tokens[i : i + len(targets)]
        if all(
            spoken.startswith(target) if prefix and not target.isdigit() else spoken == target
            for (spoken, _), target in zip(window, targets, strict=True)
        ):
            return start
    return None


def declared_cue(value: Any, fallback: str) -> str:
    """Resolve an authored cue value against the item's default phrase.

    A string names the words to wait for. ``False`` (YAML ``false``) says the
    item is display copy the narration never speaks — a title or a chart label
    — so it must not carry a cue at all. Any other value (usually ``None``)
    means the author left it unset and the plate's own display text is the cue.
    """
    if value is False:
        return ""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback.strip()


class CueMismatchError(ValueError):
    """Raised when a declared cue matches no spoken word."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            "cue phrases matched no spoken word: " + ", ".join(repr(m) for m in missing)
        )


def resolve_cues(
    words: Iterable[Mapping[str, Any]],
    cues: Mapping[str, str],
    *,
    after: float = 0.0,
    before: float | None = None,
    lead: float = 0.08,
    strict: bool = False,
) -> dict[str, float]:
    """Map ``{key: phrase}`` to ``{key: seconds}`` for every phrase that is spoken.

    ``lead`` shifts each time slightly earlier so a reveal lands *with* the
    word rather than a beat behind it (perception lags onset by ~100 ms).
    Missing phrases are left out, so callers can fall back to a default —
    unless ``strict`` is set, which raises :class:`CueMismatchError` instead.
    """
    word_list = list(words)
    resolved: dict[str, float] = {}
    for key, phrase in cues.items():
        t = find_cue(word_list, phrase, after=after, before=before)
        if t is not None:
            resolved[key] = max(after, t - lead)
    if strict:
        missing = sorted(key for key in cues if key not in resolved)
        if missing:
            raise CueMismatchError(missing)
    return resolved


def spotlight_layer(
    name: str,
    rect: Rect,
    start: float,
    end: float,
    *,
    color: str = "#FDB813",
    shape: str = "rect",
    radius: int = 18,
    stroke_width: int = 5,
    fill_opacity: float = 0.10,
) -> Layer:
    """Return a ``shape`` layer that rings ``rect`` from ``start`` to ``end``.

    ``shape`` is ``"rect"`` (rounded box) or ``"circle"``. The layer carries
    ``requires_motion`` so the router sends the graph to HyperFrames.
    """
    return Layer(
        kind="shape",
        name=name,
        rect=rect,
        timing=Timing(start=start, end=max(end, start + 0.2)),
        style={
            "shape": shape,
            "color": color,
            "radius": radius,
            "stroke_width": stroke_width,
            "fill_opacity": fill_opacity,
            "effect": "spotlight",
            "requires_motion": True,
        },
    )
