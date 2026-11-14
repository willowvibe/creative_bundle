"""Exact integer-tick time base for drift-free timeline arithmetic.

Timelines in the engine are authored in floating-point seconds. At NTSC rates
(24000/1001, 30000/1001, 60000/1001) and around sample-accurate audio, summing
float seconds accumulates error — a long timeline can drift off a frame
boundary. Borrowing FilmCraft's design, this module defines one integer time
base that every common frame rate *and* audio sample rate divides evenly:

    TICKS_PER_SECOND = 254_016_000_000

(``254_016_000_000 / 24000 = 10_584_000``, ``/ 30000 = 8_467_200``,
``/ 44100 = 5_760_000``, ``/ 48000 = 5_292_000`` — all exact, so NTSC frame
durations and 44.1/48 kHz sample durations are whole numbers of ticks.)

Convert to ticks at the edges of the system, do timeline arithmetic in plain
integers, and convert back to seconds only when handing values to FFmpeg or
HyperFrames.
"""

from __future__ import annotations

import math
from fractions import Fraction

__all__ = [
    "TICKS_PER_SECOND",
    "fps_fraction",
    "seconds_to_ticks",
    "ticks_to_seconds",
    "ticks_for_frames",
    "frames_for_ticks",
    "frame_count",
    "format_seconds",
    "snap_to_frame",
]

#: The single integer time base. Chosen so that every common frame rate
#: (including 1001-denominator NTSC rates) and audio sample rate divides it
#: exactly. One tick is ~3.94e-12 seconds.
TICKS_PER_SECOND: int = 254_016_000_000

# Common frame rates as exact rationals. NTSC rates are 1001-denominator, which
# a plain ``Fraction(float)`` cannot recover (it would find 23976/1000), so we
# snap floats to these within ``_FPS_TOLERANCE``.
_KNOWN_FPS: tuple[Fraction, ...] = (
    Fraction(24000, 1001),  # 23.976
    Fraction(30000, 1001),  # 29.97
    Fraction(60000, 1001),  # 59.94
    Fraction(120000, 1001),  # 119.88
    Fraction(24),
    Fraction(25),
    Fraction(30),
    Fraction(48),
    Fraction(50),
    Fraction(60),
    Fraction(120),
)
_FPS_TOLERANCE = 0.005


class TickConversionError(ValueError):
    """Raised when a value cannot be represented on the tick time base."""


def fps_fraction(fps: float | int | Fraction) -> Fraction:
    """Return ``fps`` as an exact rational, snapping NTSC floats to ``n/1001``.

    ``23.976``/``29.97``/``59.94`` become ``24000/1001``, ``30000/1001`` and
    ``60000/1001`` respectively; an exact ``Fraction`` passes through; any other
    value is approximated with a high-denominator fraction.
    """
    if isinstance(fps, Fraction):
        return fps
    value = float(fps)
    if value <= 0:
        raise TickConversionError(f"fps must be > 0, got {fps}")
    for candidate in _KNOWN_FPS:
        if abs(float(candidate) - value) < _FPS_TOLERANCE:
            return candidate
    return Fraction(value).limit_denominator(100_000)


def seconds_to_ticks(seconds: float | int | Fraction) -> int:
    """Convert seconds to the nearest whole tick (exact for finite floats)."""
    return round(Fraction(seconds) * TICKS_PER_SECOND)


def ticks_to_seconds(ticks: int) -> float:
    """Convert whole ticks back to (float) seconds."""
    return ticks / TICKS_PER_SECOND


def ticks_for_frames(frames: int, fps: float | int | Fraction) -> int:
    """Return the exact tick span of ``frames`` frames at ``fps``.

    Exact for every rate in :data:`_KNOWN_FPS` because the tick base divides
    each rate's frame duration.
    """
    rate = fps_fraction(fps)
    span = Fraction(frames) * TICKS_PER_SECOND / rate
    if span.denominator != 1:
        # Only reachable for exotic rates not on the tick grid.
        return round(span)
    return span.numerator


def frames_for_ticks(ticks: int, fps: float | int | Fraction, *, mode: str = "round") -> int:
    """Return the frame count spanning ``ticks`` at ``fps``.

    ``mode`` is ``"round"`` (default), ``"ceil"`` or ``"floor"``. Roundtrip-safe:
    ``frames_for_ticks(ticks_for_frames(n, fps), fps) == n``.
    """
    rate = fps_fraction(fps)
    value = Fraction(ticks) * rate / TICKS_PER_SECOND
    if mode == "round":
        return round(value)
    if mode == "ceil":
        return math.ceil(value)
    if mode == "floor":
        return math.floor(value)
    raise TickConversionError(f"unknown mode {mode!r}")


def frame_count(
    seconds: float | int | Fraction,
    fps: float | int | Fraction,
    *,
    mode: str = "round",
) -> int:
    """Return how many frames span ``seconds`` at ``fps`` (via the tick base)."""
    return frames_for_ticks(seconds_to_ticks(seconds), fps, mode=mode)


def format_seconds(ticks: int, *, places: int = 9) -> str:
    """Format ticks as a fixed-precision decimal string for FFmpeg arguments.

    Nine places is sub-millisecond at the tick resolution and avoids the
    accumulating float error of raw ``f"{seconds}"`` interpolation.
    """
    if places < 0:
        raise TickConversionError(f"places must be >= 0, got {places}")
    return f"{ticks_to_seconds(ticks):.{places}f}"


def snap_to_frame(
    seconds: float | int | Fraction,
    fps: float | int | Fraction,
    *,
    mode: str = "round",
) -> float:
    """Snap ``seconds`` to the nearest frame boundary at ``fps`` and return seconds."""
    frames = frame_count(seconds, fps, mode=mode)
    return ticks_to_seconds(ticks_for_frames(frames, fps))
