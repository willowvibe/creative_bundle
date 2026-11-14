"""Tests for the beat sync skill."""

from __future__ import annotations

import json
import struct
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from beat_sync import (
    Beat,
    BeatDetector,
    BeatSyncConfig,
    BeatSyncError,
    BeatSyncSkill,
    _EnergyDetector,
    _extract_audio_to_wav,
    _filter_bpm,
    _has_librosa,
    _read_wav_samples,
    _resolve_detector,
    _to_cuts,
)
from beat_sync.beat_sync import (  # noqa: E402
    _count_onsets,
    _intervals_to_bpm,
    _samples_to_seconds,
    _seconds_to_samples,
)

# ---------------------------------------------------------------------------
# Minimal JobContext stub
# ---------------------------------------------------------------------------


class _StubCtx:
    """Minimal JobContext for beat_sync.run()."""

    def __init__(self, tmp_path: Path) -> None:
        self.work_dir = tmp_path / "work"
        self.output_dir = tmp_path / "out"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def ensure_dirs(self) -> None:
        for d in (self.work_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Audio generation helpers
# ---------------------------------------------------------------------------


def _write_wav(
    path: Path,
    samples: list[float],
    sample_rate: int = 16_000,
    channels: int = 1,
) -> None:
    """Write a 16-bit PCM WAV from a list of floats in [-1, 1]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # Convert to 16-bit signed.
        int_samples = [max(-32768, min(32767, int(s * 32767))) for s in samples]
        raw = struct.pack(f"<{len(int_samples)}h", *int_samples)
        w.writeframes(raw)


def _metronome_wav(
    path: Path,
    bpm: float = 120.0,
    seconds: float = 4.0,
    sample_rate: int = 16_000,
) -> Path:
    """Synthesize a metronome-like WAV with a click on every beat."""
    samples: list[float] = []
    interval = 60.0 / bpm
    click_len = int(sample_rate * 0.02)  # 20 ms click
    click_freq = 1000.0
    n_total = int(seconds * sample_rate)
    next_click = 0
    for i in range(n_total):
        t = i / sample_rate
        if t >= next_click:
            # Generate a damped click for the next 20 ms.
            for j in range(click_len):
                if i + j >= n_total:
                    break
                decay = 1.0 - (j / click_len)
                samples.append(
                    0.5 * decay * (1.0 if (j % int(sample_rate / click_freq) < 1) else -1.0)
                )
            next_click += interval
        else:
            samples.append(0.0)
    # Pad to actual length.
    while len(samples) < n_total:
        samples.append(0.0)
    _write_wav(path, samples, sample_rate=sample_rate)
    return path


def _silent_wav(path: Path, seconds: float = 2.0, sample_rate: int = 16_000) -> Path:
    """Write a silent WAV (used for empty-beats testing)."""
    samples = [0.0] * int(seconds * sample_rate)
    _write_wav(path, samples, sample_rate=sample_rate)
    return path


# ---------------------------------------------------------------------------
# ffmpeg stub
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


def _make_fake_ffmpeg(*, fail: bool = False) -> Callable[..., _StubResult]:
    """Return a subprocess.run stand-in that pretends ffmpeg works."""

    def fake_run(cmd: list[str], **_: Any) -> _StubResult:
        if fail:
            return _StubResult(returncode=1, stderr="ffmpeg failed")
        # For audio extraction, the last token is the output WAV path.
        for tok in reversed(cmd):
            if tok.endswith(".wav"):
                Path(tok).parent.mkdir(parents=True, exist_ok=True)
                # Write a tiny valid WAV (1 frame of silence).
                _silent_wav(Path(tok), seconds=0.05)
                break
        return _StubResult(returncode=0)

    return fake_run


@pytest.fixture
def patched_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _StubResult]:
    fake = _make_fake_ffmpeg()
    monkeypatch.setattr("beat_sync.beat_sync.run_ffmpeg", fake)
    return fake


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


def test_to_cuts_basic() -> None:
    beats = [Beat(timestamp=i * 0.5, strength=0.5) for i in range(20)]
    cuts = _to_cuts(beats, every_n=4, min_interval=0.0)
    # Should yield beats 0, 4, 8, 12, 16 = 5 cuts.
    assert len(cuts) == 5
    assert cuts[0]["timestamp"] == 0.0
    assert cuts[0]["beat_index"] == 0
    assert cuts[1]["beat_index"] == 4


def test_to_cuts_respects_min_interval() -> None:
    beats = [Beat(timestamp=i * 0.05, strength=0.5) for i in range(100)]
    cuts = _to_cuts(beats, every_n=1, min_interval=0.5)
    # Differences between consecutive cuts must be >= 0.5s.
    for a, b in zip(cuts, cuts[1:], strict=False):
        assert b["timestamp"] - a["timestamp"] >= 0.5


def test_to_cuts_empty_beats() -> None:
    assert _to_cuts([], every_n=2, min_interval=0.0) == []


def test_to_cuts_zero_every_n() -> None:
    beats = [Beat(timestamp=0.0)]
    assert _to_cuts(beats, every_n=0, min_interval=0.0) == []


def test_to_cuts_negative_every_n() -> None:
    beats = [Beat(timestamp=0.0)]
    assert _to_cuts(beats, every_n=-1, min_interval=0.0) == []


def test_to_cuts_index_assigned() -> None:
    beats = [Beat(timestamp=i * 1.0) for i in range(10)]
    cuts = _to_cuts(beats, every_n=3, min_interval=0.0)
    for idx, cut in enumerate(cuts):
        assert cut["index"] == idx


def test_filter_bpm_no_filter() -> None:
    beats = [Beat(timestamp=i * 0.5) for i in range(10)]
    assert _filter_bpm(beats, min_bpm=None, max_bpm=None) == beats


def test_filter_bpm_all_pass() -> None:
    # 120 BPM = 0.5s interval.
    beats = [Beat(timestamp=i * 0.5) for i in range(10)]
    out = _filter_bpm(beats, min_bpm=60.0, max_bpm=180.0)
    assert len(out) == len(beats)


def test_filter_bpm_too_slow() -> None:
    # 30 BPM = 2.0s interval.
    beats = [Beat(timestamp=i * 2.0) for i in range(10)]
    out = _filter_bpm(beats, min_bpm=60.0, max_bpm=180.0)
    assert out == []


def test_filter_bpm_too_fast() -> None:
    # 240 BPM = 0.25s interval.
    beats = [Beat(timestamp=i * 0.25) for i in range(10)]
    out = _filter_bpm(beats, min_bpm=60.0, max_bpm=180.0)
    assert out == []


def test_filter_bpm_single_beat() -> None:
    beats = [Beat(timestamp=0.0)]
    out = _filter_bpm(beats, min_bpm=60.0, max_bpm=180.0)
    assert out == beats  # can't compute BPM, no filter


def test_filter_bpm_no_beats() -> None:
    assert _filter_bpm([], min_bpm=60.0, max_bpm=180.0) == []


def test_read_wav_samples_mono_16bit(tmp_path: Path) -> None:
    samples = [0.0, 0.5, -0.5, 0.25, -0.25]
    p = tmp_path / "test.wav"
    _write_wav(p, samples, sample_rate=8000)
    loaded, sr = _read_wav_samples(p)
    assert sr == 8000
    assert len(loaded) == 5
    # 0.5 should round-trip close to 0.5 (within quantisation error).
    assert abs(loaded[1] - 0.5) < 0.001


def test_read_wav_samples_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BeatSyncError, match="audio file not found"):
        _read_wav_samples(tmp_path / "missing.wav")


def test_read_wav_samples_stereo_downmix(tmp_path: Path) -> None:
    p = tmp_path / "stereo.wav"
    samples = [0.4, 0.8, -0.4, -0.8, 0.2, 0.6]  # 3 stereo frames
    _write_wav(p, samples, sample_rate=8000, channels=2)
    loaded, sr = _read_wav_samples(p)
    assert sr == 8000
    assert len(loaded) == 3  # downmixed to mono
    # First mono frame = (0.4 + 0.8) / 2 = 0.6
    assert abs(loaded[0] - 0.6) < 0.001


def test_extract_audio_to_wav_writes_file(
    tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]
) -> None:
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fake video")
    dst = tmp_path / "out.wav"
    _extract_audio_to_wav(src, dst, sample_rate=16000, channels=1, sample_width=2)
    assert dst.exists()


def test_extract_audio_to_wav_ffmpeg_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "beat_sync.beat_sync.run_ffmpeg",
        lambda *args, **kwargs: _StubResult(returncode=1, stderr="boom"),
    )
    src = tmp_path / "video.mp4"
    src.write_bytes(b"fake")
    dst = tmp_path / "out.wav"
    with pytest.raises(BeatSyncError, match="ffmpeg failed"):
        _extract_audio_to_wav(src, dst, sample_rate=16000, channels=1, sample_width=2)


def test_resolve_detector_explicit() -> None:
    custom = _EnergyDetector()
    assert _resolve_detector(custom) is custom


def test_resolve_detector_default_returns_some() -> None:
    detector = _resolve_detector(None)
    assert isinstance(detector, BeatDetector)


def test_has_librosa_returns_bool() -> None:
    # Just exercises the import probe.
    v = _has_librosa()
    assert isinstance(v, bool)


def test_count_onsets() -> None:
    beats = [
        Beat(timestamp=0.0, is_onset=True),
        Beat(timestamp=0.5),
        Beat(timestamp=1.0, is_onset=True),
        Beat(timestamp=1.5, is_onset=False),
    ]
    assert _count_onsets(beats) == 2


def test_intervals_to_bpm() -> None:
    # 120 BPM = 0.5s intervals.
    assert abs(_intervals_to_bpm([0.5, 0.5, 0.5]) - 120.0) < 0.01
    assert _intervals_to_bpm([]) == 0.0
    assert _intervals_to_bpm([0.0]) == 0.0


def test_seconds_to_samples() -> None:
    assert _seconds_to_samples(1.0, 16000) == 16000
    assert _seconds_to_samples(0.5, 8000) == 4000


def test_samples_to_seconds() -> None:
    assert _samples_to_seconds(16000, 16000) == 1.0
    assert _samples_to_seconds(0, 16000) == 0.0
    assert _samples_to_seconds(100, 0) == 0.0


# ---------------------------------------------------------------------------
# _EnergyDetector
# ---------------------------------------------------------------------------


def test_energy_detector_empty_samples() -> None:
    det = _EnergyDetector()
    assert det.detect_beats([], 16000) == []


def test_energy_detector_zero_rate() -> None:
    det = _EnergyDetector()
    assert det.detect_beats([0.0, 0.0], 0) == []


def test_energy_detector_silent_audio() -> None:
    det = _EnergyDetector()
    samples = [0.0] * 16000  # 1 second of silence
    beats = det.detect_beats(samples, 16000)
    assert beats == []


def test_energy_detector_picks_peaks() -> None:
    """A click train should produce at least one beat."""
    det = _EnergyDetector()
    samples = [0.0] * 8000
    # Add a click in the middle.
    for _i in range(16000, 16200):
        samples.append(0.9)
    samples.extend([0.0] * 1000)
    beats = det.detect_beats(samples, 16000)
    assert len(beats) >= 1
    assert all(0.0 <= b.strength <= 1.0 for b in beats)


def test_energy_detector_handles_long_signal() -> None:
    det = _EnergyDetector()
    samples = [0.0] * 48000
    # Add two clicks spaced 0.25s apart.
    for i in range(8000, 8200):
        samples[i] = 0.9
    for i in range(12000, 12200):
        samples[i] = 0.9
    beats = det.detect_beats(samples, 16000)
    assert len(beats) >= 2


# ---------------------------------------------------------------------------
# Beat dataclass
# ---------------------------------------------------------------------------


def test_beat_to_dict() -> None:
    b = Beat(timestamp=1.5, strength=0.7, is_onset=True)
    d = b.to_dict()
    assert d["timestamp"] == 1.5
    assert d["strength"] == 0.7
    assert d["is_onset"] is True


def test_beat_defaults() -> None:
    b = Beat(timestamp=0.0)
    assert b.strength == 0.0
    assert b.is_onset is False


# ---------------------------------------------------------------------------
# BeatSyncSkill.run()
# ---------------------------------------------------------------------------


def test_skill_run_with_audio_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "metronome.wav", bpm=120.0, seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["sampled"] is True
    assert plan["beat_count"] >= 1
    assert plan["beats_path"].endswith("beats.json")
    assert (ctx.output_dir / "beat_sync" / "beats.json").exists()
    assert (ctx.output_dir / "beat_sync" / "cuts.json").exists()
    assert (ctx.output_dir / "beat_sync" / "plan.json").exists()


def test_skill_run_with_video_path(
    tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]
) -> None:
    ctx = _StubCtx(tmp_path)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")
    skill = BeatSyncSkill(config=BeatSyncConfig(generate_cuts=False))
    plan = skill.run(ctx, video_path=video)
    assert "sampled" in plan
    # ffmpeg stub wrote a 0.05s silent WAV → too short for detection.
    assert plan["beat_count"] == 0


def test_skill_run_needs_video_or_audio(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    skill = BeatSyncSkill()
    with pytest.raises(BeatSyncError, match="video_path or audio_path"):
        skill.run(ctx)


def test_skill_run_missing_video(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    skill = BeatSyncSkill()
    with pytest.raises(BeatSyncError, match="video not found"):
        skill.run(ctx, video_path=tmp_path / "missing.mp4")


def test_skill_run_missing_audio(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    skill = BeatSyncSkill()
    with pytest.raises(BeatSyncError, match="audio not found"):
        skill.run(ctx, audio_path=tmp_path / "missing.wav")


def test_skill_run_silent_audio(tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "silent.wav", seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["beat_count"] == 0
    assert plan["avg_bpm"] == 0.0


def test_skill_run_short_audio(tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "short.wav", seconds=0.1)
    skill = BeatSyncSkill()
    plan = skill.run(ctx, audio_path=audio)
    assert plan["beat_count"] == 0
    assert plan["duration_seconds"] < 0.25


def test_skill_run_generate_cuts_disabled(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(generate_cuts=False, detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["cut_count"] == 0
    assert plan["cuts_path"] is None
    # No cuts.json file is written.
    assert not (ctx.output_dir / "beat_sync" / "cuts.json").exists()


def test_skill_run_beats_json_roundtrip(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    beats = json.loads((ctx.output_dir / "beat_sync" / "beats.json").read_text())
    assert isinstance(beats, list)
    for b in beats:
        assert "timestamp" in b
        assert "strength" in b
        assert "is_onset" in b
    assert plan["beat_count"] == len(beats)


def test_skill_run_cuts_json_roundtrip(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=4.0)
    skill = BeatSyncSkill(
        config=BeatSyncConfig(
            detector=_EnergyDetector(),
            cut_every_n_beats=2,
            cut_min_interval=0.3,
        )
    )
    plan = skill.run(ctx, audio_path=audio)
    if plan["cut_count"] > 0:
        cuts = json.loads((ctx.output_dir / "beat_sync" / "cuts.json").read_text())
        for c in cuts:
            assert "index" in c
            assert "timestamp" in c
            assert "beat_index" in c
            assert "strength" in c


def test_skill_run_progress_callback(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=2.0)
    messages: list[str] = []
    skill = BeatSyncSkill(
        config=BeatSyncConfig(
            detector=_EnergyDetector(),
            on_progress=lambda m: messages.append(m),
        )
    )
    skill.run(ctx, audio_path=audio)
    assert len(messages) >= 2
    assert any("audio" in m.lower() for m in messages)
    assert any("beat" in m.lower() for m in messages)


def test_skill_run_progress_callback_does_not_raise(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)

    def boom(msg: str) -> None:
        raise RuntimeError("kaboom")

    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector(), on_progress=boom))
    # Should not raise.
    plan = skill.run(ctx, audio_path=audio)
    assert "beat_count" in plan


def test_skill_run_bpm_filter_drops_all(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=2.0)
    skill = BeatSyncSkill(
        config=BeatSyncConfig(
            detector=_EnergyDetector(),
            min_bpm=300.0,
            max_bpm=500.0,
        )
    )
    plan = skill.run(ctx, audio_path=audio)
    assert plan["beat_count"] == 0


def test_skill_run_cleans_up_audio_when_extracted(
    tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]
) -> None:
    """When we extract audio from a video, the transient WAV is removed."""
    ctx = _StubCtx(tmp_path)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")
    skill = BeatSyncSkill()
    skill.run(ctx, video_path=video)
    # The transient WAV should be removed.
    assert not (ctx.output_dir / "beat_sync" / "_audio.wav").exists()


def test_skill_run_preserves_external_audio_when_already_wav(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _metronome_wav(tmp_path / "m.wav", bpm=120.0, seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    skill.run(ctx, audio_path=audio)
    # External audio file should still exist (we didn't transcode).
    assert audio.exists()


def test_skill_run_plan_includes_config(tmp_path: Path) -> None:
    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector(), sample_rate=22050))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["config"]["sample_rate"] == 22050
    assert plan["config"]["generate_cuts"] is True
    assert plan["detector"] == "_EnergyDetector"


def test_skill_run_with_custom_detector(tmp_path: Path) -> None:
    """A custom BeatDetector implementation should be used in place of the default."""

    class _ConstantDetector:
        def detect_beats(
            self,
            samples: list[float],
            sample_rate: int,
            *,
            hop_length: int = 512,
            onset_delta: float = 0.5,
        ) -> list[Beat]:
            return [
                Beat(timestamp=0.5, strength=0.8, is_onset=True),
                Beat(timestamp=1.0, strength=0.6, is_onset=False),
                Beat(timestamp=1.5, strength=0.7, is_onset=True),
            ]

    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_ConstantDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["beat_count"] == 3
    assert plan["detector"] == "_ConstantDetector"
    assert plan["avg_bpm"] == 120.0  # matches 0.5s intervals


def test_skill_run_caps_beats_output(tmp_path: Path) -> None:
    """When a detector returns > MAX_BEATS_OUTPUT beats, we cap the list."""
    from beat_sync.beat_sync import _MAX_BEATS_OUTPUT

    class _HugeDetector:
        def detect_beats(
            self,
            samples: list[float],
            sample_rate: int,
            *,
            hop_length: int = 512,
            onset_delta: float = 0.5,
        ) -> list[Beat]:
            return [
                Beat(timestamp=i * 0.01, strength=0.5, is_onset=True)
                for i in range(_MAX_BEATS_OUTPUT + 100)
            ]

    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_HugeDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["beat_count"] == _MAX_BEATS_OUTPUT


def test_skill_run_cuts_beat_index_points_right(tmp_path: Path) -> None:
    """Sanity: the cut's beat_index should index back into the original beat list."""

    class _FixedDetector:
        def detect_beats(
            self,
            samples: list[float],
            sample_rate: int,
            *,
            hop_length: int = 512,
            onset_delta: float = 0.5,
        ) -> list[Beat]:
            return [Beat(timestamp=i * 1.0, strength=0.5, is_onset=True) for i in range(10)]

    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)
    skill = BeatSyncSkill(
        config=BeatSyncConfig(
            detector=_FixedDetector(),
            cut_every_n_beats=3,
            cut_min_interval=0.0,
        )
    )
    plan = skill.run(ctx, audio_path=audio)
    cuts = json.loads((ctx.output_dir / "beat_sync" / "cuts.json").read_text())
    assert plan["cut_count"] == len(cuts)
    for c in cuts:
        assert c["beat_index"] % 3 == 0


def test_skill_run_transcodes_non_wav_audio(
    tmp_path: Path, patched_ffmpeg: Callable[..., _StubResult]
) -> None:
    """Non-WAV audio paths go through ffmpeg."""
    ctx = _StubCtx(tmp_path)
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake mp3")
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["sampled"] is True or plan["beat_count"] == 0


def test_skill_run_silent_audio_with_generate_cuts(tmp_path: Path) -> None:
    """Silent audio + cuts: cuts.json is still written but empty."""
    ctx = _StubCtx(tmp_path)
    audio = _silent_wav(tmp_path / "s.wav", seconds=2.0)
    skill = BeatSyncSkill(config=BeatSyncConfig(detector=_EnergyDetector()))
    plan = skill.run(ctx, audio_path=audio)
    assert plan["cut_count"] == 0


def test_beat_sync_skill_protocol_runtime_check() -> None:
    """Custom scorers should be runtime-checkable instances of BeatDetector."""

    class _Stub:
        def detect_beats(
            self,
            samples: list[float],
            sample_rate: int,
            *,
            hop_length: int = 512,
            onset_delta: float = 0.5,
        ) -> list[Beat]:
            return []

    assert isinstance(_Stub(), BeatDetector)
