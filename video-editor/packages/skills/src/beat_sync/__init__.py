"""Beat sync skill.

Detects musical beats and onsets in a video or audio file and emits
beat-synced cut points for montage-style edits.
"""

from __future__ import annotations

from .beat_sync import (
    Beat,
    BeatDetector,
    BeatSyncConfig,
    BeatSyncError,
    BeatSyncSkill,
    _EnergyDetector,
    _extract_audio_to_wav,
    _filter_bpm,
    _has_librosa,
    _LibrosaDetector,
    _read_wav_samples,
    _resolve_detector,
    _to_cuts,
)

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
