"""Synthesise the bundled coin sounds with the standard library only.

``coin_in.wav`` (money in) is a metallic clink: a few inharmonic sines with
fast exponential decay, struck twice about 70 ms apart. ``coin_out.wav``
(money out) is the same shape a fourth lower and a little softer. Both are
48 kHz mono 16-bit, about 0.45 s.

The files are generated once into ``assets/sfx/`` and committed; run
``python -m video_editor_core.sfx_synth`` to regenerate them.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 48_000
TOTAL_SECONDS = 0.45
HIT_GAP_SECONDS = 0.07

#: Inharmonic partials of a coin clink: (Hz, weight).
COIN_IN_PARTIALS: tuple[tuple[float, float], ...] = ((2093.0, 1.0), (3520.0, 0.55), (5274.0, 0.3))
#: A perfect fourth down is a frequency ratio of 3/4.
FOURTH_DOWN = 3.0 / 4.0

#: Decay time constant of each partial's envelope; higher partials die faster.
_DECAY_SECONDS = 0.085
_ATTACK_SECONDS = 0.001
#: The second strike is a little quieter than the first.
_SECOND_HIT_GAIN = 0.8

COIN_IN_PEAK_DBFS = -3.0
COIN_OUT_PEAK_DBFS = -5.0

SFX_DIR = Path(__file__).parent / "assets" / "sfx"


def coin_path(kind: str) -> Path:
    """The bundled WAV for ``kind`` (``"in"`` or ``"out"``)."""
    if kind not in {"in", "out"}:
        raise ValueError(f"coin kind must be 'in' or 'out', got {kind!r}")
    return SFX_DIR / f"coin_{kind}.wav"


def _samples(partials: tuple[tuple[float, float], ...], peak_dbfs: float) -> list[int]:
    count = round(TOTAL_SECONDS * SAMPLE_RATE)
    values = [0.0] * count
    for hit, gain in ((0.0, 1.0), (HIT_GAP_SECONDS, _SECOND_HIT_GAIN)):
        first = round(hit * SAMPLE_RATE)
        for n in range(first, count):
            t = (n - first) / SAMPLE_RATE
            attack = min(1.0, t / _ATTACK_SECONDS)
            value = 0.0
            for hz, weight in partials:
                # Higher partials ring for less time.
                tau = _DECAY_SECONDS * (partials[0][0] / hz) ** 0.5
                value += weight * math.sin(2.0 * math.pi * hz * t) * math.exp(-t / tau)
            values[n] += gain * attack * value
    # Fade the last 10 ms so the file never ends on a click.
    tail = round(0.01 * SAMPLE_RATE)
    for k in range(tail):
        values[count - 1 - k] *= k / tail
    scale = (10.0 ** (peak_dbfs / 20.0)) * 32767.0 / max(abs(v) for v in values)
    return [round(v * scale) for v in values]


def write_coin(path: Path, kind: str) -> Path:
    """Write ``coin_in`` or ``coin_out`` to ``path``."""
    if kind == "in":
        partials, peak = COIN_IN_PARTIALS, COIN_IN_PEAK_DBFS
    elif kind == "out":
        partials = tuple((hz * FOURTH_DOWN, weight) for hz, weight in COIN_IN_PARTIALS)
        peak = COIN_OUT_PEAK_DBFS
    else:
        raise ValueError(f"coin kind must be 'in' or 'out', got {kind!r}")
    samples = _samples(partials, peak)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def generate_all(directory: Path = SFX_DIR) -> list[Path]:
    """Write both coin sounds into ``directory``."""
    return [write_coin(Path(directory) / f"coin_{kind}.wav", kind) for kind in ("in", "out")]


if __name__ == "__main__":
    for written in generate_all():
        print(written)
