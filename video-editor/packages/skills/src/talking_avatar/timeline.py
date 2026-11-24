"""Viseme timeline planning for the talking-avatar skill.

The planning code is deliberately pure: it takes word timings and an RMS
envelope and returns one viseme shape per video frame.  Real runs feed it the
word timestamps from faster-whisper, while tests can pass synthetic words and
envelopes without loading a model.

Mouth shapes are driven by local loudness contrast, not spelling.  The envelope
is converted to dB and compared with a centred rolling mean so syllable peaks
and dips move the mouth even when a phrase's loudness is nearly flat.  A small
share of the file-normalised global loudness keeps louder phrases wider.  Word
timings only add small phonetic touches (rounded vowels, lip closures and
fricatives) on top of that, and only when Whisper detected English.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_FPS = 30.0
DEFAULT_RMS_FPS = 30.0
DEFAULT_QUIET_THRESHOLD = 0.12
DEFAULT_GAP_SECONDS = 0.15
DEFAULT_MIN_SHAPE_FRAMES = 2
DEFAULT_NOISE_PERCENTILE = 10.0
DEFAULT_PEAK_PERCENTILE = 95.0
DEFAULT_ATTACK_FRAMES = 1.0
DEFAULT_RELEASE_FRAMES = 2.0
DEFAULT_CONTRAST_WINDOW_FRAMES = 9
DEFAULT_CONTRAST_SCALE_DB = 3.5
DEFAULT_GLOBAL_OPENNESS_BLEND = 0.3
REST_SHAPE = "X"
SILENCE_SHAPE = REST_SHAPE

#: Openness bands that map loudness to the base mouth shape.  D is reserved
#: for the strongest local syllable peaks so long loud phrases do not sit on
#: one wide-open shape.
OPENNESS_B_MIDPOINT = 0.30
OPENNESS_C_MIDPOINT = 0.55
OPENNESS_D_MIDPOINT = 0.80
ROUNDED_VOWEL_OPENNESS = 0.5

#: Mouth shapes that visibly open.  Kept for callers that used to downgrade
#: open shapes on quiet frames.
OPEN_SHAPES = frozenset({"C", "D", "E", "H"})

_MOUTH_LAYERS = (
    "mouth-X",
    "mouth-A",
    "mouth-B",
    "mouth-C",
    "mouth-D",
    "mouth-E",
    "mouth-F",
    "mouth-G",
    "mouth-H",
)

#: Preston-Blair-ish grapheme mapping used by the cartoon avatar.
_GRAPHEME_SHAPES: dict[str, str] = {
    "a": "D",
    "e": "C",
    "i": "C",
    "y": "C",
    "o": "E",
    "u": "F",
    "m": "A",
    "b": "A",
    "p": "A",
    "f": "G",
    "v": "G",
    "w": "F",
    "l": "H",
}

#: Rounded vowels get an E/F mouth while the audio is loud.
_ROUNDED_VOWELS = frozenset({"o", "u", "w"})
#: Bilabial stops briefly close the lips.
_LIP_CLOSURES = frozenset({"m", "b", "p"})
#: Labiodental fricatives get the G shape.
_FRICATIVES = frozenset({"f", "v"})


@dataclass(frozen=True)
class WordTiming:
    """A spoken word with its start/end time in seconds."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class VisemeFrame:
    """One frame of the viseme timeline."""

    frame: int
    time: float
    shape: str
    rms: float
    word: str | None = None
    openness: float = 0.0


def grapheme_to_shape(grapheme: str) -> str | None:
    """Map a grapheme (or the digraph ``oo``) to a mouth shape.

    Returns ``None`` for punctuation, digits and other non-letter input.
    """
    value = grapheme.strip().lower()
    if not value:
        return None
    if value == "oo":
        return "F"
    first = value[0]
    if not first.isalpha():
        return None
    if first in _GRAPHEME_SHAPES:
        return _GRAPHEME_SHAPES[first]
    # Every remaining consonant (and a stray vowel not covered above) uses the
    # slightly-open default consonant shape.
    return "B"


#: Friendly aliases for callers that use viseme terminology.
grapheme_to_viseme = grapheme_to_shape
shape_for_grapheme = grapheme_to_shape


def word_to_shapes(text: str) -> list[str]:
    """Spread a word's graphemes into mouth shapes, collapsing repeats."""
    lowered = text.lower()
    shapes: list[str] = []
    index = 0
    while index < len(lowered):
        shape: str | None
        if lowered.startswith("oo", index):
            shape = "F"
            index += 2
        else:
            shape = grapheme_to_shape(lowered[index])
            index += 1
        if shape is None:
            continue
        if not shapes or shapes[-1] != shape:
            shapes.append(shape)
    return shapes


#: A common alternative name for :func:`word_to_shapes`.
text_to_shapes = word_to_shapes


def _coerce_words(words: Sequence[Any] | None) -> list[WordTiming]:
    timings: list[WordTiming] = []
    for item in words or ():
        if isinstance(item, WordTiming):
            timings.append(item)
            continue
        if isinstance(item, Mapping):
            text = str(item.get("word") or item.get("text") or item.get("value") or "")
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        else:
            text, start, end = item
            text = str(text)
            start = float(start)
            end = float(end)
        if not text:
            continue
        if end < start:
            start, end = end, start
        timings.append(WordTiming(text=text, start=max(0.0, start), end=max(0.0, end)))
    timings.sort(key=lambda word: (word.start, word.end))
    return timings


def _sample_rms(
    envelope: Sequence[float] | None,
    index: int,
    total_frames: int,
    *,
    fps: float,
    rms_fps: float,
) -> float:
    if not envelope:
        return 0.0
    if len(envelope) == total_frames:
        return float(envelope[index])
    time = (index + 0.5) / fps
    position = time * rms_fps
    low = max(0, int(math.floor(position)))
    if low >= len(envelope):
        return float(envelope[-1])
    high = min(len(envelope) - 1, low + 1)
    fraction = position - low
    return float(envelope[low] * (1.0 - fraction) + envelope[high] * fraction)


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Linear-interpolated percentile of an unsorted sequence."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    low = int(math.floor(position))
    high = min(len(ordered) - 1, low + 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _normalise_rms(
    rms_envelope: Sequence[float] | None,
    *,
    noise_percentile: float = DEFAULT_NOISE_PERCENTILE,
    peak_percentile: float = DEFAULT_PEAK_PERCENTILE,
) -> list[float]:
    """Convert RMS amplitudes to a raw (unsmoothed) 0..1 dB-normalised curve."""
    values = [max(0.0, float(value)) for value in rms_envelope or ()]
    if not values:
        return []

    epsilon = 1e-8
    decibels = [20.0 * math.log10(max(value, epsilon)) for value in values]
    floor = _percentile(decibels, noise_percentile)
    peak = _percentile(decibels, peak_percentile)
    span = peak - floor
    if abs(span) < 1e-6:
        # A constant signal: anything above the digital floor is fully open.
        return [1.0 if value > epsilon else 0.0 for value in values]
    return [max(0.0, min(1.0, (decibel - floor) / span)) for decibel in decibels]


def _to_decibels(values: Sequence[float]) -> list[float]:
    epsilon = 1e-8
    return [20.0 * math.log10(max(float(value), epsilon)) for value in values]


def _rolling_mean(values: Sequence[float], window: int) -> list[float]:
    """Centred rolling mean with a shortened window at the edges."""
    if not values:
        return []
    window = max(1, int(window))
    half = window // 2
    total = len(values)
    means: list[float] = []
    for index in range(total):
        low = max(0, index - half)
        high = min(total, index + half + 1)
        span = high - low
        means.append(sum(values[low:high]) / span)
    return means


def _logistic(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _contrast_openness(
    decibels: Sequence[float],
    *,
    window_frames: int,
    scale_db: float,
) -> list[float]:
    """Map dB above a centred local mean to 0..1 with a soft logistic curve."""
    if not decibels:
        return []
    local_mean = _rolling_mean(decibels, window_frames)
    scale = max(1e-6, float(scale_db))
    return [
        _logistic((decibel - mean) / scale)
        for decibel, mean in zip(decibels, local_mean, strict=True)
    ]


def rms_to_openness(
    rms_envelope: Sequence[float] | None,
    *,
    noise_percentile: float = DEFAULT_NOISE_PERCENTILE,
    peak_percentile: float = DEFAULT_PEAK_PERCENTILE,
    attack_frames: float = DEFAULT_ATTACK_FRAMES,
    release_frames: float = DEFAULT_RELEASE_FRAMES,
    contrast_window_frames: int = DEFAULT_CONTRAST_WINDOW_FRAMES,
    contrast_scale_db: float = DEFAULT_CONTRAST_SCALE_DB,
    global_blend: float = DEFAULT_GLOBAL_OPENNESS_BLEND,
) -> list[float]:
    """Convert per-frame RMS amplitudes into a smoothed 0..1 openness curve.

    Speech loudness is nearly flat within a phrase, so the global normalised
    envelope alone holds one wide mouth shape for far too long.  Openness is
    therefore driven mostly by local contrast: frame dB minus a centred rolling
    mean over about 0.3 s, run through a logistic curve.  A smaller share of the
    global curve keeps louder phrases wider than quiet ones.  The blended curve
    is smoothed with a fast attack and a short release so syllable dips still
    close the mouth partway.
    """
    normalized = _normalise_rms(
        rms_envelope,
        noise_percentile=noise_percentile,
        peak_percentile=peak_percentile,
    )
    if not normalized:
        return []

    values = [max(0.0, float(value)) for value in rms_envelope or ()]
    local = _contrast_openness(
        _to_decibels(values),
        window_frames=contrast_window_frames,
        scale_db=contrast_scale_db,
    )
    blend = max(0.0, min(1.0, float(global_blend)))
    targets = [
        (1.0 - blend) * local_value + blend * global_value
        for local_value, global_value in zip(local, normalized, strict=True)
    ]

    attack = min(1.0, 1.0 / max(1e-6, float(attack_frames)))
    release = min(1.0, 1.0 / max(1e-6, float(release_frames)))
    smoothed: list[float] = []
    previous = 0.0
    for target in targets:
        alpha = attack if target >= previous else release
        previous = previous + (target - previous) * alpha
        smoothed.append(max(0.0, min(1.0, previous)))
    return smoothed


#: Friendly aliases for the openness conversion.
envelope_to_openness = rms_to_openness
openness_from_rms = rms_to_openness


def _shape_for_openness(openness: float, *, silence_threshold: float) -> str:
    if openness < silence_threshold:
        return REST_SHAPE
    if openness < OPENNESS_B_MIDPOINT:
        return "B"
    if openness < OPENNESS_C_MIDPOINT:
        return "C"
    if openness < OPENNESS_D_MIDPOINT:
        return "H"
    return "D"


def _is_english(language: str | None) -> bool:
    if language is None:
        return False
    value = str(language).strip().lower().replace("_", "-")
    return value.split("-", 1)[0] == "en"


def _word_letters(text: str) -> list[str]:
    return [character.lower() for character in text if character.isalpha()]


def _letter_at(word: WordTiming, time: float, letters: Sequence[str]) -> str | None:
    if not letters:
        return None
    span = word.end - word.start
    if span <= 0.0:
        return letters[0]
    position = max(0.0, min((time - word.start) / span, 1.0 - 1e-9))
    index = int(position * len(letters))
    return letters[min(index, len(letters) - 1)]


def _enforce_min_hold(
    shapes: list[str],
    openness: Sequence[float],
    silent: Sequence[bool],
    *,
    min_frames: int,
    silence_threshold: float,
) -> None:
    """Make every non-rest shape last at least ``min_frames`` frames."""
    if min_frames <= 1:
        return
    total = len(shapes)
    for _ in range(total):
        changed = False
        index = 0
        while index < total:
            if shapes[index] == REST_SHAPE:
                index += 1
                continue
            end = index + 1
            while end < total and shapes[end] == shapes[index]:
                end += 1
            needed = min_frames - (end - index)
            if needed <= 0:
                index = end
                continue
            current = shapes[index]
            forward = end
            while (
                needed > 0
                and forward < total
                and not silent[forward]
                and openness[forward] >= silence_threshold
            ):
                shapes[forward] = current
                forward += 1
                needed -= 1
            backward = index - 1
            while (
                needed > 0
                and backward >= 0
                and not silent[backward]
                and openness[backward] >= silence_threshold
            ):
                shapes[backward] = current
                backward -= 1
                needed -= 1
            changed = True
            index = end
        if not changed:
            return


def _split_frames(total: int, parts: int, minimum: int) -> list[int]:
    """Split ``total`` frames across ``parts`` shapes, each at least ``minimum``."""
    if parts <= 0:
        return []
    total = max(total, minimum * parts)
    base, extra = divmod(total, parts)
    return [base + (1 if index < extra else 0) for index in range(parts)]


def build_viseme_timeline(
    words: Sequence[Any] | None,
    rms_envelope: Sequence[float] | None = None,
    *,
    duration: float | None = None,
    fps: float = DEFAULT_FPS,
    rms_fps: float = DEFAULT_RMS_FPS,
    quiet_threshold: float = DEFAULT_QUIET_THRESHOLD,
    gap_seconds: float = DEFAULT_GAP_SECONDS,
    min_shape_frames: int = DEFAULT_MIN_SHAPE_FRAMES,
    noise_percentile: float = DEFAULT_NOISE_PERCENTILE,
    peak_percentile: float = DEFAULT_PEAK_PERCENTILE,
    attack_frames: float = DEFAULT_ATTACK_FRAMES,
    release_frames: float = DEFAULT_RELEASE_FRAMES,
    contrast_window_frames: int = DEFAULT_CONTRAST_WINDOW_FRAMES,
    contrast_scale_db: float = DEFAULT_CONTRAST_SCALE_DB,
    global_blend: float = DEFAULT_GLOBAL_OPENNESS_BLEND,
    language: str | None = None,
) -> list[VisemeFrame]:
    """Build a frame-by-frame mouth-shape timeline.

    Args:
        words: Word timings as :class:`WordTiming`, mappings with
            ``word``/``text`` plus ``start``/``end``, or ``(text, start, end)``
            tuples.  ``None`` or an empty sequence drives the mouth from audio
            alone.
        rms_envelope: Optional loudness samples.  When its length equals the
            number of output frames it is used as one value per frame;
            otherwise it is sampled at ``rms_fps``.  Values are raw RMS
            amplitudes, not openness.
        duration: Output duration in seconds.  Defaults to the last word end
            (or the envelope length).
        fps: Output frame rate (30 fps by default).
        rms_fps: Sample rate of ``rms_envelope`` when it is not per-frame.
        quiet_threshold: Openness below this is silence and always rests on X.
        gap_seconds: Retained for API compatibility; loudness now controls
            pauses so gaps are no longer filled from the previous word.
        min_shape_frames: Minimum number of frames for each mouth shape.
        noise_percentile: RMS percentile treated as the noise floor.
        peak_percentile: RMS percentile treated as the peak.
        attack_frames: Smoothing time constant when the mouth opens.
        release_frames: Smoothing time constant when the mouth closes.
        contrast_window_frames: Centred rolling window for local dB contrast.
        contrast_scale_db: Logistic softness of the local contrast curve.
        global_blend: Share of the file-normalised global openness mixed in.
        language: Detected transcription language.  Spelling overrides are
            applied only for English, so a forced English mapping cannot
            distort other languages.

    Returns:
        A list with one :class:`VisemeFrame` per output frame.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    if min_shape_frames < 1:
        min_shape_frames = 1
    silence_threshold = max(0.0, float(quiet_threshold))

    del gap_seconds  # loudness-driven pauses supersede the old gap fill rule

    word_list = _coerce_words(words)
    envelope_duration = len(rms_envelope) / rms_fps if rms_envelope and rms_fps else 0.0
    if duration is None:
        duration = max([word.end for word in word_list] + [envelope_duration, 0.0])
    duration = max(0.0, float(duration))
    if duration <= 0:
        return []

    total_frames = max(1, int(math.ceil(duration * fps - 1e-9)))

    rms_values = [
        _sample_rms(
            rms_envelope,
            index,
            total_frames,
            fps=fps,
            rms_fps=rms_fps,
        )
        for index in range(total_frames)
    ]
    raw_openness = _normalise_rms(
        rms_values,
        noise_percentile=noise_percentile,
        peak_percentile=peak_percentile,
    )
    openness_values = rms_to_openness(
        rms_values,
        noise_percentile=noise_percentile,
        peak_percentile=peak_percentile,
        attack_frames=attack_frames,
        release_frames=release_frames,
        contrast_window_frames=contrast_window_frames,
        contrast_scale_db=contrast_scale_db,
        global_blend=global_blend,
    )
    # Frames at (or below) the file's noise floor are true silence.  A release
    # tail must never leave an open mouth hanging through them.
    silent = [value <= 0.0 for value in raw_openness]

    letters_by_word = [(word, _word_letters(word.text)) for word in word_list]
    spelling_overrides = _is_english(language)

    shapes_out: list[str] = []
    owners: list[str | None] = []
    for index in range(total_frames):
        time = (index + 0.5) / fps
        openness = openness_values[index]
        shape = _shape_for_openness(openness, silence_threshold=silence_threshold)
        owner: str | None = None
        for word, letters in letters_by_word:
            if word.start <= time < word.end:
                owner = word.text
                if not spelling_overrides:
                    break
                letter = _letter_at(word, time, letters)
                if letter in _LIP_CLOSURES:
                    shape = "A"
                elif letter in _FRICATIVES:
                    shape = "G"
                elif letter in _ROUNDED_VOWELS and shape in {"C", "D", "H"}:
                    shape = "E" if openness >= ROUNDED_VOWEL_OPENNESS else "F"
                break
        shapes_out.append(shape)
        owners.append(owner)

    # Silence always wins, even over a forced lip closure.
    for index in range(total_frames):
        if silent[index] or openness_values[index] < silence_threshold:
            shapes_out[index] = REST_SHAPE

    _enforce_min_hold(
        shapes_out,
        openness_values,
        silent,
        min_frames=min_shape_frames,
        silence_threshold=silence_threshold,
    )

    # Re-apply silence after the hold pass so an open shape can never leak in.
    for index in range(total_frames):
        if silent[index] or openness_values[index] < silence_threshold:
            shapes_out[index] = REST_SHAPE

    return [
        VisemeFrame(
            frame=index,
            time=index / fps,
            shape=shapes_out[index],
            rms=rms_values[index],
            word=owners[index],
            openness=openness_values[index],
        )
        for index in range(total_frames)
    ]


#: Common alternative names for the planner.
build_timeline = build_viseme_timeline
viseme_timeline = build_viseme_timeline
timeline_from_words = build_viseme_timeline


def blink_frames(
    duration: float,
    *,
    fps: float = DEFAULT_FPS,
    seed: int = 0,
    min_interval: float = 2.5,
    max_interval: float = 5.0,
    length_frames: int = 3,
) -> list[int]:
    """Return deterministic frame indices where the avatar's eyes are closed."""
    if duration <= 0 or fps <= 0:
        return []
    if max_interval < min_interval:
        min_interval, max_interval = max_interval, min_interval
    min_interval = max(0.0, min_interval)
    max_interval = max(min_interval, max_interval)
    length_frames = max(1, int(length_frames))

    rng = random.Random(seed)
    total_frames = max(0, int(round(duration * fps)))
    frames: list[int] = []
    time = rng.uniform(min_interval, max_interval)
    while time < duration:
        start = int(round(time * fps))
        for offset in range(length_frames):
            frame = start + offset
            if 0 <= frame < total_frames and frame not in frames:
                frames.append(frame)
        time += rng.uniform(min_interval, max_interval)
    return sorted(frames)


#: Friendly aliases used by the motion renderer and tests.
plan_blinks = blink_frames
schedule_blinks = blink_frames


class BlinkScheduler:
    """Small object wrapper around :func:`blink_frames`."""

    def __init__(
        self,
        *,
        fps: float = DEFAULT_FPS,
        seed: int = 0,
        min_interval: float = 2.5,
        max_interval: float = 5.0,
        length_frames: int = 3,
    ) -> None:
        self.fps = fps
        self.seed = seed
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.length_frames = length_frames

    def plan(self, duration: float) -> list[int]:
        return blink_frames(
            duration,
            fps=self.fps,
            seed=self.seed,
            min_interval=self.min_interval,
            max_interval=self.max_interval,
            length_frames=self.length_frames,
        )

    __call__ = plan


__all__ = [
    "BlinkScheduler",
    "DEFAULT_ATTACK_FRAMES",
    "DEFAULT_CONTRAST_SCALE_DB",
    "DEFAULT_CONTRAST_WINDOW_FRAMES",
    "DEFAULT_FPS",
    "DEFAULT_GAP_SECONDS",
    "DEFAULT_GLOBAL_OPENNESS_BLEND",
    "DEFAULT_MIN_SHAPE_FRAMES",
    "DEFAULT_NOISE_PERCENTILE",
    "DEFAULT_PEAK_PERCENTILE",
    "DEFAULT_QUIET_THRESHOLD",
    "DEFAULT_RELEASE_FRAMES",
    "DEFAULT_RMS_FPS",
    "OPEN_SHAPES",
    "REST_SHAPE",
    "SILENCE_SHAPE",
    "VisemeFrame",
    "WordTiming",
    "_MOUTH_LAYERS",
    "blink_frames",
    "build_timeline",
    "build_viseme_timeline",
    "envelope_to_openness",
    "grapheme_to_shape",
    "grapheme_to_viseme",
    "openness_from_rms",
    "plan_blinks",
    "rms_to_openness",
    "schedule_blinks",
    "shape_for_grapheme",
    "text_to_shapes",
    "timeline_from_words",
    "viseme_timeline",
    "word_to_shapes",
]
