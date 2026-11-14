"""Measure the audio offset between two recordings of the same event.

Used to align a camera track with a separate audio recorder (or a lip-sync
clip with its dialogue line) per shot, instead of trusting one global constant.

The pipeline is FFmpeg-first and standard-library-only, mirroring the approach
in kajisho5/ffmpeg-skill's ``sync.py``:

1. FFmpeg decodes both files to mono 8 kHz and emits an RMS level every 8
   samples, giving a 1 kHz loudness envelope.
2. The envelope is decimated to 100 Hz and cross-correlated with a radix-2 FFT
   for a coarse lag.
3. Direct correlation around that lag refines the answer to 1 ms.

A positive offset means the candidate starts *later* than the reference, so
delaying the reference by ``offset_seconds`` aligns the two.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import structlog

from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, FFmpegError, run_ffmpeg

logger = structlog.get_logger(__name__)

_ENVELOPE_RATE = 1000  # 1 kHz: one RMS value per millisecond
_DECIMATE = 10  # coarse search runs at 100 Hz
_MIN_ENVELOPE_SECONDS = 1.0
_DEFAULT_ANALYSE_SECONDS = 60.0
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf|inf|nan)")
_FLOOR_DB = -100.0


def _envelope(path: Path | str, timeout: float = RENDER_TIMEOUT_SECONDS) -> list[float]:
    """Return a 1 kHz linear-amplitude envelope for ``path``.

    FFmpeg does the decoding and the per-millisecond RMS reduction; Python only
    parses floats. ``-inf`` (digital silence) is clamped to the floor so the
    correlation is not poisoned by infinities.
    """
    cmd = [
        "ffmpeg",
        "-i",
        str(path),
        "-vn",
        "-af",
        (
            "aresample=8000,"
            "asetnsamples=n=8,"
            "astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
        ),
        "-f",
        "null",
        "-",
    ]
    result = run_ffmpeg(cmd, check=False, timeout=timeout)
    if result.returncode != 0:
        raise FFmpegError(
            f"ffmpeg envelope extraction failed for {path}",
            cmd,
            stderr=result.stderr,
        )

    envelope: list[float] = []
    for line in result.stdout.splitlines():
        match = _RMS_RE.search(line)
        if not match:
            continue
        raw = match.group(1)
        level = _FLOOR_DB if raw == "-inf" else float(raw)
        envelope.append(10.0 ** (max(level, _FLOOR_DB) / 20.0))
    if not envelope:
        raise ValueError(f"no audio envelope produced for {path}")
    return envelope


def _fft(values: list[complex], invert: bool = False) -> list[complex]:
    """Iterative radix-2 FFT; ``len(values)`` must be a power of two."""
    n = len(values)
    if n == 0 or n & (n - 1):
        raise ValueError(f"FFT length must be a power of two, got {n}")
    data = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= n:
        angle = (2.0 if invert else -2.0) * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, n, length):
            weight = 1 + 0j
            for k in range(start, start + half):
                even = data[k]
                odd = data[k + half] * weight
                data[k] = even + odd
                data[k + half] = even - odd
                weight *= step
        length <<= 1

    if invert:
        data = [value / n for value in data]
    return data


def _next_power_of_two(value: int) -> int:
    return 1 << max(0, (value - 1).bit_length())


def _centre(values: list[float]) -> list[float]:
    mean = sum(values) / len(values)
    return [value - mean for value in values]


def _coarse_lag(reference: list[float], candidate: list[float], limit: int) -> int:
    """Return the lag (in coarse samples) maximising reference vs candidate.

    Positive lag means ``candidate`` is delayed relative to ``reference``.
    """
    size = _next_power_of_two(2 * max(len(reference), len(candidate)))
    a = _centre(reference) + [0.0] * (size - len(reference))
    b = _centre(candidate) + [0.0] * (size - len(candidate))

    spectrum_a = _fft([complex(v, 0.0) for v in a])
    spectrum_b = _fft([complex(v, 0.0) for v in b])
    # R[k] = sum_n reference[n] * candidate[n-k]: a positive lag means the
    # candidate's content lands later, i.e. the candidate is delayed.
    product = [sa.conjugate() * sb for sa, sb in zip(spectrum_a, spectrum_b, strict=True)]
    correlation = _fft(product, invert=True)

    best_lag = 0
    best_score = -math.inf
    for lag in range(-limit, limit + 1):
        index = lag % size
        score = correlation[index].real
        if score > best_score:
            best_score = score
            best_lag = lag
    return best_lag


def _normalised_correlation(reference: list[float], candidate: list[float], lag: int) -> float:
    """Pearson correlation of the overlap when ``candidate`` is shifted by ``lag``."""
    if lag >= 0:
        left = reference[: len(reference) - lag]
        right = candidate[lag:]
    else:
        left = reference[-lag:]
        right = candidate[: len(candidate) + lag]
    length = min(len(left), len(right))
    if length < 10:
        return -math.inf
    left = _centre(left[:length])
    right = _centre(right[:length])
    denominator = math.sqrt(sum(v * v for v in left) * sum(v * v for v in right))
    if denominator == 0:
        return -math.inf
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def measure_audio_offset(
    reference: Path | str,
    candidate: Path | str,
    *,
    max_offset_seconds: float = 5.0,
    analyse_seconds: float = _DEFAULT_ANALYSE_SECONDS,
) -> dict[str, float]:
    """Measure how much later ``candidate`` starts than ``reference``.

    Returns ``offset_seconds`` (positive = candidate is later), the coarse
    estimate it was refined from, and ``confidence`` (normalised correlation at
    the chosen lag, 0..1). Raises ``ValueError`` when either track is too short
    to correlate.
    """
    if max_offset_seconds <= 0:
        raise ValueError("max_offset_seconds must be positive")
    if analyse_seconds <= 0:
        raise ValueError("analyse_seconds must be positive")

    reference_envelope = _envelope(reference)
    candidate_envelope = _envelope(candidate)
    window = int(analyse_seconds * _ENVELOPE_RATE)
    reference_envelope = reference_envelope[:window]
    candidate_envelope = candidate_envelope[:window]

    minimum = int(_MIN_ENVELOPE_SECONDS * _ENVELOPE_RATE)
    if len(reference_envelope) < minimum or len(candidate_envelope) < minimum:
        raise ValueError(
            f"both tracks need at least {_MIN_ENVELOPE_SECONDS:.0f}s of audio to measure an offset"
        )

    coarse_reference = reference_envelope[::_DECIMATE]
    coarse_candidate = candidate_envelope[::_DECIMATE]
    limit = max(1, int(round(max_offset_seconds * _ENVELOPE_RATE / _DECIMATE)))
    coarse_lag = _coarse_lag(coarse_reference, coarse_candidate, limit)

    centre_ms = coarse_lag * _DECIMATE
    best_lag = centre_ms
    best_score = -math.inf
    for lag in range(centre_ms - _DECIMATE, centre_ms + _DECIMATE + 1):
        score = _normalised_correlation(reference_envelope, candidate_envelope, lag)
        if score > best_score:
            best_score = score
            best_lag = lag

    offset_seconds = best_lag / _ENVELOPE_RATE
    logger.info(
        "audio_offset_measured",
        reference=str(reference),
        candidate=str(candidate),
        offset_seconds=offset_seconds,
        coarse_seconds=centre_ms / _ENVELOPE_RATE,
        confidence=best_score,
    )
    return {
        "offset_seconds": round(offset_seconds, 4),
        "coarse_seconds": round(centre_ms / _ENVELOPE_RATE, 3),
        "confidence": round(best_score, 4) if math.isfinite(best_score) else 0.0,
    }
