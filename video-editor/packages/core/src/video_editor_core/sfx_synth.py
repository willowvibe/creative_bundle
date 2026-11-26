"""Synthesise the bundled UI sounds with the standard library only.

``coin_in.wav`` (money in) is a metallic clink: a few inharmonic sines with
fast exponential decay, struck twice about 70 ms apart. ``coin_out.wav``
(money out) is the same shape a fourth lower and a little softer.
``swoosh.wav`` is a short filtered-noise sweep, and ``hit.wav`` is a soft low
thump with a brief click, and ``pop.wav`` is a soft 120 ms sine blip with a
small upward pitch glide (a photo card entering). All are 48 kHz mono 16-bit.

The files are generated once into ``assets/sfx/`` and committed; run
``python -m video_editor_core.sfx_synth`` to regenerate them.
"""

from __future__ import annotations

import math
import random
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

SWOOSH_SECONDS = 0.35
SWOOSH_PEAK_DBFS = -6.0
HIT_SECONDS = 0.4
HIT_PEAK_DBFS = -4.0
POP_SECONDS = 0.12
POP_PEAK_DBFS = -6.0

SFX_DIR = Path(__file__).parent / "assets" / "sfx"
_SOUND_FILES = {
    "in": "coin_in.wav",
    "out": "coin_out.wav",
    "swoosh": "swoosh.wav",
    "hit": "hit.wav",
    "pop": "pop.wav",
}


def sound_path(kind: str) -> Path:
    """The bundled WAV for ``kind`` (``in``, ``out``, ``swoosh``, ``hit`` or ``pop``)."""
    try:
        filename = _SOUND_FILES[kind]
    except KeyError:
        raise ValueError(
            f"sound kind must be one of {', '.join(sorted(_SOUND_FILES))}, got {kind!r}"
        ) from None
    return SFX_DIR / filename


def coin_path(kind: str) -> Path:
    """The bundled WAV for ``kind`` (``"in"`` or ``"out"``)."""
    if kind not in {"in", "out"}:
        raise ValueError(f"coin kind must be 'in' or 'out', got {kind!r}")
    return sound_path(kind)


def swoosh_path() -> Path:
    """The bundled chart-transition swoosh."""
    return sound_path("swoosh")


def hit_path() -> Path:
    """The bundled low emphasis hit."""
    return sound_path("hit")


def pop_path() -> Path:
    """The bundled photo-card pop."""
    return sound_path("pop")


def _normalise(values: list[float], peak_dbfs: float) -> list[int]:
    scale = (10.0 ** (peak_dbfs / 20.0)) * 32767.0 / max(abs(value) for value in values)
    return [round(value * scale) for value in values]


def _fade_tail(values: list[float], seconds: float = 0.01) -> None:
    """Fade the end so the file never clicks."""
    tail = round(seconds * SAMPLE_RATE)
    count = len(values)
    for k in range(min(tail, count)):
        values[count - 1 - k] *= k / tail


def _write_wav(path: Path, samples: list[int]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _coin_samples(partials: tuple[tuple[float, float], ...], peak_dbfs: float) -> list[int]:
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
    _fade_tail(values)
    return _normalise(values, peak_dbfs)


def write_coin(path: Path, kind: str) -> Path:
    """Write ``coin_in`` or ``coin_out`` to ``path``."""
    if kind == "in":
        partials, peak = COIN_IN_PARTIALS, COIN_IN_PEAK_DBFS
    elif kind == "out":
        partials = tuple((hz * FOURTH_DOWN, weight) for hz, weight in COIN_IN_PARTIALS)
        peak = COIN_OUT_PEAK_DBFS
    else:
        raise ValueError(f"coin kind must be 'in' or 'out', got {kind!r}")
    return _write_wav(Path(path), _coin_samples(partials, peak))


def _swoosh_samples(peak_dbfs: float = SWOOSH_PEAK_DBFS) -> list[int]:
    """A soft swell of noise whose low-pass cutoff sweeps upward."""
    count = round(SWOOSH_SECONDS * SAMPLE_RATE)
    rng = random.Random(20261126)
    values = [0.0] * count
    filtered = 0.0
    for n in range(count):
        progress = n / max(1, count - 1)
        # 250 Hz -> 6 kHz: a dull air movement opening into a bright swish.
        cutoff = 250.0 * (6000.0 / 250.0) ** progress
        alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / SAMPLE_RATE)
        filtered += alpha * (rng.uniform(-1.0, 1.0) - filtered)
        swell = math.sin(math.pi * progress) ** 2
        values[n] = filtered * swell
    _fade_tail(values)
    return _normalise(values, peak_dbfs)


def write_swoosh(path: Path) -> Path:
    """Write the chart-transition ``swoosh.wav`` to ``path``."""
    return _write_wav(Path(path), _swoosh_samples())


def _hit_samples(peak_dbfs: float = HIT_PEAK_DBFS) -> list[int]:
    """A soft pitch-dropping sine thump plus a very short click."""
    count = round(HIT_SECONDS * SAMPLE_RATE)
    values = [0.0] * count
    phase = 0.0
    for n in range(count):
        t = n / SAMPLE_RATE
        # The body falls from 120 Hz to 90 Hz over its first 120 ms.
        hz = 120.0 - 30.0 * min(1.0, t / 0.12)
        phase += 2.0 * math.pi * hz / SAMPLE_RATE
        attack = min(1.0, t / 0.004)
        thump = math.sin(phase) * math.exp(-t / 0.085) * attack
        click = (
            0.35 * math.sin(2.0 * math.pi * 1800.0 * t)
            + 0.2 * math.sin(2.0 * math.pi * 2600.0 * t)
        ) * math.exp(-t / 0.0025)
        values[n] = thump + click
    _fade_tail(values)
    return _normalise(values, peak_dbfs)


def write_hit(path: Path) -> Path:
    """Write the low emphasis ``hit.wav`` to ``path``."""
    return _write_wav(Path(path), _hit_samples())


def _pop_samples(peak_dbfs: float = POP_PEAK_DBFS) -> list[int]:
    """A soft UI pop: a short sine whose pitch blips up, with a fast decay."""
    count = round(POP_SECONDS * SAMPLE_RATE)
    values = [0.0] * count
    phase = 0.0
    for n in range(count):
        t = n / SAMPLE_RATE
        # 520 Hz glides up to 780 Hz over the first 50 ms, then holds.
        hz = 520.0 + 260.0 * min(1.0, t / 0.05)
        phase += 2.0 * math.pi * hz / SAMPLE_RATE
        attack = min(1.0, t / 0.008)
        values[n] = (math.sin(phase) + 0.25 * math.sin(2.0 * phase)) * attack * math.exp(-t / 0.032)
    _fade_tail(values)
    return _normalise(values, peak_dbfs)


def write_pop(path: Path) -> Path:
    """Write the photo-card ``pop.wav`` to ``path``."""
    return _write_wav(Path(path), _pop_samples())


def generate_all(directory: Path = SFX_DIR) -> list[Path]:
    """Write every bundled sound into ``directory``."""
    directory = Path(directory)
    return [
        write_coin(directory / "coin_in.wav", "in"),
        write_coin(directory / "coin_out.wav", "out"),
        write_swoosh(directory / "swoosh.wav"),
        write_hit(directory / "hit.wav"),
        write_pop(directory / "pop.wav"),
    ]


if __name__ == "__main__":
    for written in generate_all():
        print(written)
