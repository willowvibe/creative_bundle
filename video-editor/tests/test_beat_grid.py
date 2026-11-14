"""Beat grid, boundary quantization and beat-aware musical planning (P4.6)."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.beat_grid import (
    build_grid,
    grid_markdown,
    quantize_boundaries,
    snap_cut,
)
from video_editor_core.musical import MusicalPlanError, MusicalSource, plan_segments

SOURCES = [MusicalSource(Path("a.png"), "image")]


def test_build_grid_marks_every_fourth_beat_as_a_downbeat() -> None:
    grid = build_grid([0.0, 0.5, 1.0, 1.5, 2.0, 2.5], bpm=120.0)
    assert grid.ok
    assert grid.beats == (0.0, 0.5, 1.0, 1.5, 2.0, 2.5)
    assert grid.downbeats == (0.0, 2.0)


def test_snap_respects_the_tolerance_and_mode() -> None:
    grid = build_grid([0.0, 0.5, 1.0, 1.5, 2.0])

    snapped, moved = snap_cut(0.58, grid, mode="beat", tolerance=0.12)
    assert (snapped, moved) == (0.5, True)

    outside, moved = snap_cut(0.70, grid, mode="beat", tolerance=0.12)
    assert (outside, moved) == (0.70, False)

    downbeat, moved = snap_cut(1.9, grid, mode="downbeat", tolerance=0.12)
    assert (downbeat, moved) == (2.0, True)


def test_quantize_never_reorders_colliding_cuts() -> None:
    grid = build_grid([0.0, 0.1, 0.2])
    quantized, moved = quantize_boundaries([0.08, 0.11], grid, mode="beat", tolerance=0.10)
    assert quantized == [0.1, 0.11]
    assert moved == 1


def test_the_grid_markdown_has_a_row_per_cut() -> None:
    grid = build_grid([0.0, 0.5, 1.0], bpm=120.0)
    text = grid_markdown(
        grid, planned=[0.4, 0.9], quantized=[0.5, 1.0], mode="beat", tolerance=0.12
    )
    assert "BPM: 120.00" in text
    assert "| 1 | 0.400 | 0.500 | +0.100 |" in text
    assert "| 2 | 0.900 | 1.000 | +0.100 |" in text


def test_plan_segments_accepts_explicit_boundaries() -> None:
    segments = plan_segments(
        SOURCES,
        10.0,
        segment_seconds=5.0,
        transition_seconds=0.5,
        boundaries=[2.0, 6.0],
    )
    assert len(segments) == 3
    assert segments[1].start == pytest.approx(2.0)
    assert segments[2].start == pytest.approx(6.0)
    assert segments[-1].end == pytest.approx(10.0)


def test_plan_segments_rejects_bad_boundaries() -> None:
    with pytest.raises(MusicalPlanError, match="strictly increasing"):
        plan_segments(
            SOURCES,
            10.0,
            segment_seconds=5.0,
            transition_seconds=0.5,
            boundaries=[6.0, 2.0],
        )
    with pytest.raises(MusicalPlanError, match="strictly inside"):
        plan_segments(
            SOURCES,
            10.0,
            segment_seconds=5.0,
            transition_seconds=0.5,
            boundaries=[0.0, 2.0],
        )
