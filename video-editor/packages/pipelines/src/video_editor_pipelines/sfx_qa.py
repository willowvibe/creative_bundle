"""Advisory measurement of the placed SFX cues.

Reads the finished file with ``hyperframes audio``: the 5 ms envelope gives the
level in a cue window versus the bed before it, and the onset detector gives the
nearest attack. The report goes into ``qa.json`` as ``sfx`` and is **advisory**:
a missing onset on a soft whoosh is common, so it does not change the build's
pass/fail. Level and sync are reported per cue for a listening review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.hyperframes_cli import analyze_audio
from video_editor_core.sfx_cues import SfxCue

logger = structlog.get_logger(__name__)

MIN_LIFT_DB = 0.5
#: A subtle SFX bus adds well under this to the programme loudness.
MAX_SFX_LUFS_ADDED = 2.0
CUE_WINDOW_SECONDS = 0.2
BED_WINDOW_SECONDS = 0.2
BED_LOOKBACK_SECONDS = 0.45
ONSET_SEARCH_SECONDS = 0.2
ONSET_STRENGTH_FLOOR = 0.25
DEFAULT_SYNC_TOLERANCE = 0.08
SYNC_TOLERANCE = {"whoosh": 0.12}


@dataclass(frozen=True)
class CueMeasurement:
    sound: str
    start: float
    level_db: float | None
    bed_db: float | None
    onset: float | None
    reference_db: float | None = None

    @property
    def lift_db(self) -> float | None:
        if self.level_db is None or self.bed_db is None:
            return None
        return self.level_db - self.bed_db

    @property
    def added_db(self) -> float | None:
        """The SFX contribution: mixed minus the voice-only window."""
        if self.level_db is None or self.reference_db is None:
            return None
        return self.level_db - self.reference_db

    @property
    def sync_error(self) -> float | None:
        return None if self.onset is None else self.onset - self.start

    @property
    def tolerance(self) -> float:
        return SYNC_TOLERANCE.get(self.sound, DEFAULT_SYNC_TOLERANCE)

    @property
    def ok(self) -> bool:
        """Sync only; the level columns are for review."""
        return self.sync_error is None or abs(self.sync_error) <= self.tolerance


@dataclass(frozen=True)
class SfxReport:
    measurements: tuple[CueMeasurement, ...]
    mixed_lufs: float | None = None
    reference_lufs: float | None = None

    @property
    def added_lufs(self) -> float | None:
        if self.mixed_lufs is None or self.reference_lufs is None:
            return None
        return self.mixed_lufs - self.reference_lufs

    @property
    def ok(self) -> bool:
        if not self.measurements or not all(item.ok for item in self.measurements):
            return False
        # The hard part of the level gate is the whole-programme ceiling: a
        # subtle SFX bus leaves LUFS almost unchanged, an overpowering one
        # lifts it by more than a couple of units.
        return self.added_lufs is None or self.added_lufs <= MAX_SFX_LUFS_ADDED

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mixed_lufs": _round(self.mixed_lufs),
            "reference_lufs": _round(self.reference_lufs),
            "added_lufs": _round(self.added_lufs),
            "cues": [
                {
                    "sound": item.sound,
                    "start": item.start,
                    "level_db": _round(item.level_db),
                    "bed_db": _round(item.bed_db),
                    "lift_db": _round(item.lift_db),
                    "reference_db": _round(item.reference_db),
                    "added_db": _round(item.added_db),
                    "onset": _round(item.onset),
                    "sync_error": _round(item.sync_error),
                    "ok": item.ok,
                }
                for item in self.measurements
            ],
        }


def analyze_sfx(
    video: Path | str,
    cues: list[SfxCue] | tuple[SfxCue, ...],
    *,
    reference: Path | str | None = None,
    cli_path: str = "hyperframes",
) -> SfxReport:
    """Measure each cue's level, nearest onset and SFX contribution.

    ``reference`` is the voice-only file before the SFX mix. When given, the
    SFX contribution is ``mixed - reference`` in the cue window, which is
    stable across voice dynamics; without it the report falls back to the
    lift over the preceding bed (noisier).
    """
    if not cues:
        return SfxReport(())
    payload = analyze_audio(
        video,
        cli_path=cli_path,
        only=("onsets", "envelope", "loudness"),
    )
    items = payload.get("items") or []
    if not items:
        raise ValueError(f"no audio measurement for {video}")
    item = items[0]
    envelope = item.get("envelope") or {}
    values = envelope.get("values") or []
    hop_ms = float(envelope.get("hopMs") or 5.0)
    starts_at = float(envelope.get("startsAt") or 0.0)
    events = (item.get("onsets") or {}).get("events") or []

    mixed_lufs = (item.get("loudness") or {}).get("lufsIntegrated")
    reference_values: list[float] = []
    reference_hop = hop_ms
    reference_starts = starts_at
    reference_lufs: float | None = None
    if reference is not None:
        ref_payload = analyze_audio(
            reference,
            cli_path=cli_path,
            only=("envelope", "loudness"),
        )
        ref_items = ref_payload.get("items") or []
        if ref_items:
            ref_item = ref_items[0]
            ref_envelope = ref_item.get("envelope") or {}
            reference_values = ref_envelope.get("values") or []
            reference_hop = float(ref_envelope.get("hopMs") or hop_ms)
            reference_starts = float(ref_envelope.get("startsAt") or starts_at)
            reference_lufs = (ref_item.get("loudness") or {}).get("lufsIntegrated")

    measurements = []
    for cue in cues:
        level = _mean_db(values, starts_at, hop_ms, cue.start, CUE_WINDOW_SECONDS)
        bed_start = cue.start - BED_LOOKBACK_SECONDS
        bed = (
            _mean_db(values, starts_at, hop_ms, bed_start, BED_WINDOW_SECONDS)
            if bed_start >= 0
            else None
        )
        reference_db = (
            _mean_db(
                reference_values,
                reference_starts,
                reference_hop,
                cue.start,
                CUE_WINDOW_SECONDS,
            )
            if reference_values
            else None
        )
        onset = _nearest_onset(events, cue.start)
        measurements.append(CueMeasurement(cue.sound, cue.start, level, bed, onset, reference_db))
    return SfxReport(tuple(measurements), mixed_lufs, reference_lufs)


def _mean_db(
    values: list[float],
    starts_at: float,
    hop_ms: float,
    start: float,
    duration: float,
) -> float | None:
    first = max(0, int((start - starts_at) * 1000 / hop_ms))
    last = min(len(values), int((start + duration - starts_at) * 1000 / hop_ms))
    if last <= first:
        return None
    window = values[first:last]
    if not window:
        return None
    # Average power, not dB, so a short loud attack is not diluted by quiet
    # neighbours; convert back to dB for the report.
    power = sum(10 ** (value / 10) for value in window) / len(window)
    return 10 * math.log10(max(power, 1e-12))


def _nearest_onset(events: list[dict[str, Any]], start: float) -> float | None:
    candidates = [
        float(event["t"])
        for event in events
        if float(event.get("strength") or 0.0) >= ONSET_STRENGTH_FLOOR
        and abs(float(event.get("t") or 0.0) - start) <= ONSET_SEARCH_SECONDS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs(value - start))


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
