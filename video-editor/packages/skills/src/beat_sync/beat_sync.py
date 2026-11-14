"""Beat sync skill.

Detects musical beats and onsets in a video (or its audio track) and
optionally emits beat-synced cut points for montage-style edits.

Outputs (under ``ctx.output_dir``):

    beat_sync/
        beats.json          # full beat/onset list with timestamps
        cuts.json            # beat-synced cut points (if requested)
        plan.json            # manifest with summary stats + settings
"""

from __future__ import annotations

import contextlib
import json
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Default audio extraction parameters (16 kHz mono PCM — librosa's
# happy place and small enough for in-memory processing).
_DEFAULT_SAMPLE_RATE = 16_000
_DEFAULT_CHANNELS = 1
_DEFAULT_SAMPLE_WIDTH = 2  # 16-bit signed PCM

# Minimum reasonable audio length (seconds) below which we skip
# detection and return an empty beat list.
_MIN_AUDIO_LENGTH = 0.25

# Default onset sensitivity (passed to librosa when available).
_DEFAULT_HOP_LENGTH = 512
_DEFAULT_ONSET_DELTA = 0.5

# Maximum number of beats retained in the output JSON. librosa can
# return thousands of beats on long tracks; we cap to keep the JSON
# manageable for downstream tools.
_MAX_BEATS_OUTPUT = 5_000


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Beat:
    """A single detected beat.

    Attributes
    ----------
    timestamp:
        Time in seconds from the start of the audio.
    strength:
        Optional confidence / strength score in ``[0.0, 1.0]`` (when
        the detector provides one).
    is_onset:
        ``True`` if this beat is also flagged as an onset (a
        particularly strong spectral flux event).
    """

    timestamp: float
    strength: float = 0.0
    is_onset: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "strength": self.strength,
            "is_onset": self.is_onset,
        }


@dataclass
class BeatSyncConfig:
    """Configuration for the beat-sync pipeline.

    Attributes
    ----------
    sample_rate:
        Target sample rate for audio extraction (Hz).
    hop_length:
        librosa hop length (smaller = finer time resolution).
    onset_delta:
        librosa onset detection sensitivity.
    min_bpm:
        If set, beats slower than this BPM are filtered out (rare;
        mostly useful for trimming low-frequency rumble).
    max_bpm:
        If set, beats faster than this BPM are filtered out.
    generate_cuts:
        When ``True``, also emit beat-synced cut points (every N
        beats) under ``cuts.json``.
    cut_every_n_beats:
        Interval (in beats) between consecutive cuts.
    cut_min_interval:
        Minimum gap between cuts (seconds). Cuts closer than this
        are skipped to avoid jarring micro-edits.
    detector:
        Optional ``BeatDetector`` instance. When ``None``, the skill
        uses :class:`_LibrosaDetector` if librosa is importable, else
        :class:`_EnergyDetector` (a pure-stdlib fallback).
    on_progress:
        Optional progress callback ``(str) -> None``.
    """

    sample_rate: int = _DEFAULT_SAMPLE_RATE
    hop_length: int = _DEFAULT_HOP_LENGTH
    onset_delta: float = _DEFAULT_ONSET_DELTA
    min_bpm: float | None = None
    max_bpm: float | None = None
    generate_cuts: bool = True
    cut_every_n_beats: int = 4
    cut_min_interval: float = 0.5
    detector: BeatDetector | None = None
    on_progress: Callable[[str], None] | None = None


class BeatSyncError(RuntimeError):
    """Raised when the beat sync pipeline cannot continue (missing
    audio, detector failure, etc.)."""


# ---------------------------------------------------------------------------
# Detector Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class BeatDetector(Protocol):
    """Pluggable beat detector.

    Implementations receive a mono PCM float32 array (length
    ``num_samples``) and sample rate, and return a list of float
    timestamps in seconds.
    """

    def detect_beats(
        self,
        samples: list[float],
        sample_rate: int,
        *,
        hop_length: int = _DEFAULT_HOP_LENGTH,
        onset_delta: float = _DEFAULT_ONSET_DELTA,
    ) -> list[Beat]: ...


# ---------------------------------------------------------------------------
# Default detectors
# ---------------------------------------------------------------------------


@dataclass
class _LibrosaDetector:
    """Default detector backed by librosa (lazy-imported).

    librosa is heavy and optional; we only import it when detection
    is actually requested. If librosa is missing, callers should
    fall back to :class:`_EnergyDetector` (or pass their own
    ``BeatDetector``).
    """

    def detect_beats(
        self,
        samples: list[float],
        sample_rate: int,
        *,
        hop_length: int = _DEFAULT_HOP_LENGTH,
        onset_delta: float = _DEFAULT_ONSET_DELTA,
    ) -> list[Beat]:
        try:
            import librosa  # type: ignore[import-not-found]
            import numpy as np  # numpy is a librosa dependency
        except ImportError as exc:
            raise BeatSyncError(
                "librosa is not installed; pass a custom BeatDetector or use _EnergyDetector"
            ) from exc

        if not samples:
            return []
        y = np.asarray(samples, dtype=np.float32)
        # Tempo + beat frames.
        tempo, beat_frames = librosa.beat.beat_track(
            y=y,
            sr=sample_rate,
            hop_length=hop_length,
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sample_rate, hop_length=hop_length)
        # Onset envelope for strength scoring.
        onset_env = librosa.onset.onset_strength(y=y, sr=sample_rate, hop_length=hop_length)
        # Compute onset times for the strength lookup.
        onset_frames = librosa.onset.onset_detect(
            y=y,
            sr=sample_rate,
            hop_length=hop_length,
            delta=onset_delta,
        )
        onset_times = set(
            librosa.frames_to_time(onset_frames, sr=sample_rate, hop_length=hop_length).tolist()
        )
        beats: list[Beat] = []
        # librosa returns tempo as a 0-d or 1-d array depending on version.
        bpm = float(np.asarray(tempo, dtype=np.float64).reshape(-1)[0].item())
        for idx, ts in enumerate(beat_times.tolist()):
            beats.append(
                Beat(
                    timestamp=float(ts),
                    strength=float(onset_env[idx]) if idx < len(onset_env) else 0.0,
                    is_onset=any(abs(float(ts) - ot) < 0.05 for ot in onset_times),
                )
            )
        return _filter_bpm(beats, min_bpm=None, max_bpm=None, bpm=bpm)


@dataclass
class _EnergyDetector:
    """Pure-stdlib fallback: detect beats via simple energy thresholding.

    This is *much* weaker than librosa but is fine for short upbeat
    clips and is guaranteed to work without any extra dependencies.
    It works by:
        1. Computing short-time RMS energy.
        2. Thresholding at ``mean + 1.5 * stddev``.
        3. Picking the local maxima as onsets.
    """

    window_ms: int = 23  # ~1024 samples at 44.1 kHz

    def detect_beats(
        self,
        samples: list[float],
        sample_rate: int,
        *,
        hop_length: int = _DEFAULT_HOP_LENGTH,
        onset_delta: float = _DEFAULT_ONSET_DELTA,
    ) -> list[Beat]:
        if not samples or sample_rate <= 0:
            return []
        win = max(1, int(sample_rate * self.window_ms / 1000))
        step = max(1, hop_length)
        energies: list[float] = []
        for i in range(0, len(samples) - win, step):
            chunk = samples[i : i + win]
            if not chunk:
                continue
            rms = sum(x * x for x in chunk) / len(chunk)
            energies.append(rms**0.5)
        if not energies:
            return []
        mean = sum(energies) / len(energies)
        var = sum((e - mean) ** 2 for e in energies) / len(energies)
        std = var**0.5
        # Quiet signal (no variation) → no beats. This avoids
        # false positives on silent audio.
        if std < 1e-6:
            return []
        threshold = mean + 1.5 * std
        peaks: list[Beat] = []
        prev_idx = -10
        for i, e in enumerate(energies):
            if e < threshold:
                continue
            # Local max in a small window.
            left = max(0, i - 2)
            right = min(len(energies), i + 3)
            if e == max(energies[left:right]) and (i - prev_idx) >= 2:
                ts = i * step / sample_rate
                peaks.append(
                    Beat(
                        timestamp=ts,
                        strength=min(1.0, e / max(mean + 2 * std, 1e-9)),
                        is_onset=True,
                    )
                )
                prev_idx = i
        return peaks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_bpm(
    beats: list[Beat],
    *,
    min_bpm: float | None,
    max_bpm: float | None,
    bpm: float | None = None,
) -> list[Beat]:
    """Optionally filter beats by BPM range.

    The current implementation keeps all beats when ``min_bpm`` and
    ``max_bpm`` are ``None``; when set, it returns the subset whose
    *average* inter-beat interval falls in range. This is a coarse
    filter — useful for trimming noise.
    """
    if min_bpm is None and max_bpm is None:
        return beats
    if len(beats) < 2:
        return beats
    intervals = [beats[i + 1].timestamp - beats[i].timestamp for i in range(len(beats) - 1)]
    intervals = [d for d in intervals if d > 0]
    if not intervals:
        return beats
    avg_interval = sum(intervals) / len(intervals)
    avg_bpm = 60.0 / avg_interval if avg_interval > 0 else 0.0
    if min_bpm is not None and avg_bpm < min_bpm:
        return []
    if max_bpm is not None and avg_bpm > max_bpm:
        return []
    return beats


def _extract_audio_to_wav(
    video_path: Path,
    output_wav: Path,
    sample_rate: int,
    channels: int,
    sample_width: int,
) -> None:
    """Extract audio from a video to a 16-bit PCM WAV using ffmpeg."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        str(output_wav),
    ]
    result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise BeatSyncError(f"ffmpeg failed extracting audio: {result.stderr[-400:]}")


def _read_wav_samples(wav_path: Path) -> tuple[list[float], int]:
    """Read a WAV file and return ``(samples, sample_rate)``."""
    if not wav_path.exists():
        raise BeatSyncError(f"audio file not found: {wav_path}")
    with wave.open(str(wav_path), "rb") as w:
        n_channels = w.getnchannels()
        sample_rate = w.getframerate()
        sample_width = w.getsampwidth()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)
    if sample_width == 2:
        # 16-bit signed little-endian.
        import struct

        n_samples = len(raw) // 2
        unpacked = struct.unpack(f"<{n_samples}h", raw)
        # Normalise to [-1.0, 1.0].
        samples = [s / 32768.0 for s in unpacked]
    elif sample_width == 1:
        # 8-bit unsigned.
        samples = [(b - 128) / 128.0 for b in raw]
    else:
        raise BeatSyncError(f"unsupported WAV sample width: {sample_width} bytes")
    if n_channels > 1:
        # Downmix to mono.
        mono: list[float] = []
        for i in range(0, len(samples), n_channels):
            chunk = samples[i : i + n_channels]
            mono.append(sum(chunk) / n_channels)
        samples = mono
    return samples, sample_rate


def _has_librosa() -> bool:
    """Return ``True`` if librosa is importable."""
    try:
        import librosa  # noqa: F401

        return True
    except ImportError:
        return False


def _resolve_detector(explicit: BeatDetector | None) -> BeatDetector:
    """Pick a detector: explicit override > librosa > energy fallback."""
    if explicit is not None:
        return explicit
    if _has_librosa():
        return _LibrosaDetector()
    return _EnergyDetector()


def _to_cuts(
    beats: list[Beat],
    every_n: int,
    min_interval: float,
) -> list[dict[str, Any]]:
    """Return beat-synced cut points (every Nth beat, respecting min_interval)."""
    if not beats or every_n < 1:
        return []
    out: list[dict[str, Any]] = []
    last_ts = -min_interval
    for idx, beat in enumerate(beats):
        if idx % every_n != 0:
            continue
        if beat.timestamp - last_ts < min_interval:
            continue
        out.append(
            {
                "index": len(out),
                "timestamp": beat.timestamp,
                "beat_index": idx,
                "strength": beat.strength,
            }
        )
        last_ts = beat.timestamp
    return out


# ---------------------------------------------------------------------------
# BeatSyncSkill
# ---------------------------------------------------------------------------


@dataclass
class BeatSyncSkill:
    """Skill: detect beats and emit beat-synced cut points."""

    name: str = "beat_sync"
    config: BeatSyncConfig = field(default_factory=BeatSyncConfig)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, msg: str) -> None:
        if self.config.on_progress is not None:
            with contextlib.suppress(Exception):
                self.config.on_progress(msg)

    def _output_dir(self, ctx: Any) -> Path:
        if hasattr(ctx, "ensure_dirs"):
            ctx.ensure_dirs()
        out = Path(ctx.output_dir) / "beat_sync"
        out.mkdir(parents=True, exist_ok=True)
        return out

    # ------------------------------------------------------------------
    # Public run()
    # ------------------------------------------------------------------

    def run(
        self,
        ctx: Any,
        video_path: Path | None = None,
        audio_path: Path | None = None,
        generate_cuts: bool | None = None,
    ) -> dict[str, Any]:
        """Run the beat sync pipeline.

        Parameters
        ----------
        ctx:
            Job context with ``output_dir`` (and optionally
            ``ensure_dirs``).
        video_path:
            Optional path to a video file. When provided (and
            ``audio_path`` is not), the audio track is extracted to
            a temporary WAV for analysis.
        audio_path:
            Optional path to a pre-extracted audio file (WAV). Takes
            precedence over ``video_path``.
        generate_cuts:
            Override ``cfg.generate_cuts``.
        """
        if video_path is None and audio_path is None:
            raise BeatSyncError("either video_path or audio_path must be provided")
        out_dir = self._output_dir(ctx)
        cfg = self.config
        use_cuts = generate_cuts if generate_cuts is not None else cfg.generate_cuts

        # 1. Resolve audio source.
        if audio_path is None:
            assert video_path is not None
            video_path = Path(video_path)
            if not video_path.exists():
                raise BeatSyncError(f"video not found: {video_path}")
            wav_path = out_dir / "_audio.wav"
            self._emit(f"extracting audio from {video_path.name}")
            _extract_audio_to_wav(
                video_path,
                wav_path,
                sample_rate=cfg.sample_rate,
                channels=_DEFAULT_CHANNELS,
                sample_width=_DEFAULT_SAMPLE_WIDTH,
            )
        else:
            audio_path = Path(audio_path)
            if not audio_path.exists():
                raise BeatSyncError(f"audio not found: {audio_path}")
            wav_path = out_dir / "_audio.wav"
            # If the audio is already a WAV, copy; otherwise transcode.
            if audio_path.suffix.lower() == ".wav":
                wav_path = audio_path
            else:
                _extract_audio_to_wav(
                    audio_path,
                    wav_path,
                    sample_rate=cfg.sample_rate,
                    channels=_DEFAULT_CHANNELS,
                    sample_width=_DEFAULT_SAMPLE_WIDTH,
                )

        # 2. Load samples.
        self._emit("loading audio samples")
        samples, sample_rate = _read_wav_samples(wav_path)
        try:
            if not samples:
                self._emit("no audio samples; returning empty beats")
                return self._write_outputs(
                    out_dir,
                    beats=[],
                    cuts=[],
                    duration_seconds=0.0,
                    detector_name="empty",
                    sampled=False,
                )
            duration = len(samples) / sample_rate
            if duration < _MIN_AUDIO_LENGTH:
                self._emit(f"audio too short ({duration:.3f}s); skipping detection")
                return self._write_outputs(
                    out_dir,
                    beats=[],
                    cuts=[],
                    duration_seconds=duration,
                    detector_name="empty",
                    sampled=True,
                )

            # 3. Detect beats.
            detector = _resolve_detector(cfg.detector)
            detector_name = type(detector).__name__
            self._emit(f"detecting beats using {detector_name}")
            beats = detector.detect_beats(
                samples,
                sample_rate,
                hop_length=cfg.hop_length,
                onset_delta=cfg.onset_delta,
            )
            beats = _filter_bpm(
                beats,
                min_bpm=cfg.min_bpm,
                max_bpm=cfg.max_bpm,
            )
            # Cap to first N to keep JSON small.
            if len(beats) > _MAX_BEATS_OUTPUT:
                self._emit(f"capping beats to {_MAX_BEATS_OUTPUT}")
                beats = beats[:_MAX_BEATS_OUTPUT]

            # 4. Generate cuts.
            cuts: list[dict[str, Any]] = []
            if use_cuts and beats:
                cuts = _to_cuts(
                    beats,
                    every_n=max(1, cfg.cut_every_n_beats),
                    min_interval=cfg.cut_min_interval,
                )
                self._emit(f"generated {len(cuts)} beat-synced cuts")

            return self._write_outputs(
                out_dir,
                beats=beats,
                cuts=cuts,
                duration_seconds=duration,
                detector_name=detector_name,
                sampled=True,
            )
        finally:
            # 5. Clean up the transient WAV (only if we extracted it AND
            #    it lives in our output dir — leave external audio alone).
            self._cleanup_temp_wav(audio_path, out_dir)

    def _cleanup_temp_wav(self, audio_path: Path | None, out_dir: Path) -> None:
        """Remove the transient ``_audio.wav`` if we extracted it ourselves."""
        if audio_path is not None:
            return
        candidate = out_dir / "_audio.wav"
        if candidate.exists():
            with contextlib.suppress(OSError):
                candidate.unlink()

    # ------------------------------------------------------------------
    # Output writer
    # ------------------------------------------------------------------

    def _write_outputs(
        self,
        out_dir: Path,
        *,
        beats: list[Beat],
        cuts: list[dict[str, Any]],
        duration_seconds: float,
        detector_name: str,
        sampled: bool,
    ) -> dict[str, Any]:
        """Persist beats.json / cuts.json / plan.json and return the manifest."""
        beats_payload = [b.to_dict() for b in beats]
        beats_path = out_dir / "beats.json"
        beats_path.write_text(json.dumps(beats_payload, indent=2, ensure_ascii=False))

        cuts_path: Path | None = None
        if self.config.generate_cuts:
            cuts_path = out_dir / "cuts.json"
            cuts_path.write_text(json.dumps(cuts, indent=2, ensure_ascii=False))

        avg_bpm = 0.0
        if len(beats) >= 2:
            intervals = [beats[i + 1].timestamp - beats[i].timestamp for i in range(len(beats) - 1)]
            intervals = [d for d in intervals if d > 0]
            if intervals:
                avg = sum(intervals) / len(intervals)
                avg_bpm = 60.0 / avg if avg > 0 else 0.0

        plan = {
            "duration_seconds": duration_seconds,
            "sampled": sampled,
            "detector": detector_name,
            "beat_count": len(beats),
            "avg_bpm": round(avg_bpm, 2),
            "config": {
                "sample_rate": self.config.sample_rate,
                "hop_length": self.config.hop_length,
                "onset_delta": self.config.onset_delta,
                "min_bpm": self.config.min_bpm,
                "max_bpm": self.config.max_bpm,
                "generate_cuts": self.config.generate_cuts,
                "cut_every_n_beats": self.config.cut_every_n_beats,
                "cut_min_interval": self.config.cut_min_interval,
            },
            "beats_path": str(beats_path),
            "cuts_path": str(cuts_path) if cuts_path else None,
            "cut_count": len(cuts),
        }
        plan_path = out_dir / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False))
        self._emit(f"wrote {plan_path}")
        return plan


__all__ = [
    "Beat",
    "BeatDetector",
    "BeatSyncConfig",
    "BeatSyncError",
    "BeatSyncSkill",
    "_EnergyDetector",
    "_LibrosaDetector",
    "_extract_audio_to_wav",
    "_filter_bpm",
    "_has_librosa",
    "_read_wav_samples",
    "_resolve_detector",
    "_to_cuts",
]


# ---------------------------------------------------------------------------
# Pure helpers exposed for testing.
# ---------------------------------------------------------------------------


def _intervals_to_bpm(intervals: list[float]) -> float:
    """Return average BPM from a list of inter-beat intervals (seconds)."""
    intervals = [d for d in intervals if d > 0]
    if not intervals:
        return 0.0
    avg = sum(intervals) / len(intervals)
    return 60.0 / avg if avg > 0 else 0.0


def _round_bpm(bpm: float, step: float = 0.5) -> float:
    """Round a BPM value to the nearest ``step`` (default 0.5)."""
    if step <= 0:
        return bpm
    return round(bpm / step) * step


def _is_onset(beat: Beat) -> bool:
    """Return ``True`` if a beat is flagged as an onset."""
    return beat.is_onset


def _count_onsets(beats: list[Beat]) -> int:
    """Return the number of onsets in a beat list."""
    return sum(1 for b in beats if b.is_onset)


def _beat_to_dict(beat: Beat) -> dict[str, Any]:
    """Serialize a :class:`Beat` to a plain dict."""
    return beat.to_dict()


def _seconds_to_samples(seconds: float, sample_rate: int) -> int:
    """Convert seconds to a sample index."""
    return int(seconds * sample_rate)


def _samples_to_seconds(samples: int, sample_rate: int) -> float:
    """Convert a sample index to seconds."""
    if sample_rate <= 0:
        return 0.0
    return samples / sample_rate
