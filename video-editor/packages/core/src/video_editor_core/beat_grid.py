"""Beat grid and boundary quantization (P4.6).

The planner lays scenes at fixed ``segment_seconds``. When the music has a
measured beat grid, snapping each cut to the nearest beat (or downbeat) inside
a small tolerance makes the picture land on the music without moving a cut far
from its planned position.

Pure functions only: detection lives in the ``beat_sync`` skill and the caller
passes the measured timestamps in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QuantizeMode = Literal["off", "beat", "downbeat"]


@dataclass(frozen=True)
class BeatGrid:
    """Measured musical time: BPM, every beat and every bar's first beat."""

    bpm: float | None
    beats: tuple[float, ...]
    downbeats: tuple[float, ...]

    @property
    def ok(self) -> bool:
        return bool(self.beats)


def build_grid(
    beat_times: tuple[float, ...] | list[float],
    *,
    bpm: float | None = None,
    beats_per_bar: int = 4,
) -> BeatGrid:
    """Build a grid from measured beat timestamps (monotonic, de-duplicated)."""
    ordered = sorted({round(float(value), 6) for value in beat_times if float(value) >= 0.0})
    beats = tuple(ordered)
    if beats_per_bar < 1:
        beats_per_bar = 1
    downbeats = tuple(beats[::beats_per_bar])
    return BeatGrid(bpm=bpm, beats=beats, downbeats=downbeats)


def _candidates(grid: BeatGrid, mode: QuantizeMode) -> tuple[float, ...]:
    if mode == "downbeat":
        return grid.downbeats or grid.beats
    return grid.beats


def snap_cut(
    cut: float,
    grid: BeatGrid,
    *,
    mode: QuantizeMode = "downbeat",
    tolerance: float = 0.12,
) -> tuple[float, bool]:
    """Return ``(snapped, moved)`` for one interior cut."""
    if mode == "off" or not grid.ok:
        return cut, False
    candidates = _candidates(grid, mode)
    if not candidates:
        return cut, False
    nearest = min(candidates, key=lambda value: abs(value - cut))
    if abs(nearest - cut) <= tolerance:
        return nearest, abs(nearest - cut) > 1e-6
    return cut, False


def quantize_boundaries(
    boundaries: list[float] | tuple[float, ...],
    grid: BeatGrid,
    *,
    mode: QuantizeMode = "downbeat",
    tolerance: float = 0.12,
) -> tuple[list[float], int]:
    """Snap interior boundaries while keeping them strictly increasing.

    Returns the new boundaries and how many moved. A snap that would collide
    with (or cross) its neighbour keeps the original cut, so quantization never
    reorders the plan.
    """
    quantized: list[float] = []
    moved = 0
    previous = 0.0
    for index, boundary in enumerate(boundaries):
        snapped, changed = snap_cut(boundary, grid, mode=mode, tolerance=tolerance)
        next_boundary = boundaries[index + 1] if index + 1 < len(boundaries) else None
        if snapped <= previous + 1e-6:
            snapped, changed = boundary, False
        if next_boundary is not None and snapped >= next_boundary - 1e-6:
            snapped, changed = boundary, False
        quantized.append(snapped)
        previous = snapped
        moved += int(changed)
    return quantized, moved


def grid_markdown(
    grid: BeatGrid,
    *,
    planned: list[float] | tuple[float, ...],
    quantized: list[float] | tuple[float, ...],
    mode: QuantizeMode,
    tolerance: float,
) -> str:
    """The ``plan/beat_grid.md`` table the estimate step can show for approval."""
    lines = [
        "# Beat grid",
        "",
        f"- Mode: `{mode}` (tolerance {tolerance:.3f}s)",
        f"- BPM: {grid.bpm:.2f}" if grid.bpm else "- BPM: not measured",
        f"- Beats: {len(grid.beats)}, downbeats: {len(grid.downbeats)}",
        "",
        "| Cut | Planned (s) | Quantized (s) | Δ (s) |",
        "| --- | --- | --- | --- |",
    ]
    for index, (before, after) in enumerate(zip(planned, quantized, strict=False), start=1):
        lines.append(f"| {index} | {before:.3f} | {after:.3f} | {after - before:+.3f} |")
    return "\n".join(lines) + "\n"
