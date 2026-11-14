"""Tests for per-shot audio offset measurement."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest
from video_editor_ffmpeg.run import FFmpegError
from video_editor_ffmpeg.sync import measure_audio_offset

from tests.conftest import _require_ffmpeg

_RATE = 16000
_CLICKS = (0.30, 0.71, 1.05, 1.62, 2.10, 2.48, 3.01, 3.44)


def _write_clicks(path: Path, *, delay: float = 0.0, duration: float = 4.0) -> Path:
    """Write a mono WAV of short 1 kHz bursts, optionally delayed."""
    total = int(duration * _RATE)
    samples = [0] * total
    burst = int(0.006 * _RATE)
    for click in _CLICKS:
        start = int((click + delay) * _RATE)
        for index in range(burst):
            if start + index >= total:
                break
            samples[start + index] = int(0.8 * 32767 * math.sin(2 * math.pi * 1000 * index / _RATE))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_RATE)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def test_identical_tracks_measure_zero(tmp_path: Path) -> None:
    _require_ffmpeg()
    reference = _write_clicks(tmp_path / "ref.wav")
    candidate = _write_clicks(tmp_path / "cand.wav")

    result = measure_audio_offset(reference, candidate)

    assert result["offset_seconds"] == pytest.approx(0.0, abs=0.002)
    assert result["confidence"] > 0.5


def test_delayed_candidate_is_positive(tmp_path: Path) -> None:
    _require_ffmpeg()
    reference = _write_clicks(tmp_path / "ref.wav")
    candidate = _write_clicks(tmp_path / "cand.wav", delay=0.137)

    result = measure_audio_offset(reference, candidate)

    assert result["offset_seconds"] == pytest.approx(0.137, abs=0.003)


def test_advanced_candidate_is_negative(tmp_path: Path) -> None:
    _require_ffmpeg()
    reference = _write_clicks(tmp_path / "ref.wav", delay=0.092)
    candidate = _write_clicks(tmp_path / "cand.wav")

    result = measure_audio_offset(reference, candidate)

    assert result["offset_seconds"] == pytest.approx(-0.092, abs=0.003)


def test_short_track_raises(tmp_path: Path) -> None:
    _require_ffmpeg()
    short = _write_clicks(tmp_path / "short.wav", duration=0.4)
    other = _write_clicks(tmp_path / "other.wav")

    with pytest.raises(ValueError, match="at least"):
        measure_audio_offset(short, other)


def test_missing_file_raises_ffmpeg_error(tmp_path: Path) -> None:
    present = _write_clicks(tmp_path / "present.wav")

    with pytest.raises(FFmpegError):
        measure_audio_offset(tmp_path / "missing.wav", present)


def test_rejects_non_positive_limits(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        measure_audio_offset(tmp_path / "a.wav", tmp_path / "b.wav", max_offset_seconds=0)
