"""Tests for the integer-tick time base (`video_editor_core.ticks`)."""

from __future__ import annotations

from fractions import Fraction

import pytest
from video_editor_core.render_graph import Cut, RenderGraph
from video_editor_core.ticks import (
    TICKS_PER_SECOND,
    TickConversionError,
    format_seconds,
    fps_fraction,
    frame_count,
    frames_for_ticks,
    seconds_to_ticks,
    snap_to_frame,
    ticks_for_frames,
    ticks_to_seconds,
)
from video_editor_core.timeline import EDL, Segment

NTSC_24 = Fraction(24000, 1001)
NTSC_30 = Fraction(30000, 1001)
NTSC_60 = Fraction(60000, 1001)


def test_tick_base_divides_common_rates() -> None:
    """Every common frame and sample rate divides the tick base exactly."""
    for rate in (24000, 30000, 60000, 120000, 24, 25, 30, 48, 50, 60, 120, 44100, 48000):
        assert TICKS_PER_SECOND % rate == 0, rate


def test_fps_fraction_snaps_ntsc() -> None:
    assert fps_fraction(23.976) == NTSC_24
    assert fps_fraction(29.97) == NTSC_30
    assert fps_fraction(59.94) == NTSC_60
    assert fps_fraction(30) == Fraction(30)
    assert fps_fraction(25) == Fraction(25)
    with pytest.raises(TickConversionError):
        fps_fraction(0)


def test_ticks_for_frames_exact_for_ntsc() -> None:
    """A frame span at NTSC rates is a whole number of ticks (no rounding)."""
    for fps in (NTSC_24, NTSC_30, NTSC_60):
        span = Fraction(1) * TICKS_PER_SECOND / fps
        assert span.denominator == 1
        assert ticks_for_frames(1, fps) == span.numerator
        assert ticks_for_frames(1000, fps) == span.numerator * 1000


def test_frames_ticks_roundtrip_large() -> None:
    """Round-tripping frames -> ticks -> frames is exact even for long timelines."""
    for fps in (NTSC_24, NTSC_30, 25, 50):
        n = 1_000_000
        assert frames_for_ticks(ticks_for_frames(n, fps), fps) == n


def test_seconds_ticks_roundtrip() -> None:
    assert seconds_to_ticks(1.0) == TICKS_PER_SECOND
    assert ticks_to_seconds(TICKS_PER_SECOND) == 1.0
    assert ticks_to_seconds(seconds_to_ticks(12.345678)) == pytest.approx(12.345678)


def test_frame_count_modes() -> None:
    assert frame_count(1.0, 30) == 30
    assert frame_count(0.5, 30) == 15
    assert frame_count(0.99, 25, mode="floor") == 24
    assert frame_count(0.01, 25, mode="ceil") == 1
    with pytest.raises(TickConversionError):
        frames_for_ticks(1, 30, mode="nope")


def test_format_seconds_and_snap() -> None:
    assert format_seconds(seconds_to_ticks(1.5)) == "1.500000000"
    # Snapping to a 23.976 frame boundary is frame-aligned and idempotent.
    snapped = snap_to_frame(1.0, NTSC_24)
    assert frames_for_ticks(seconds_to_ticks(snapped), NTSC_24) == frame_count(1.0, NTSC_24)
    assert snap_to_frame(snapped, NTSC_24) == pytest.approx(snapped)


def _graph(cuts: list[Cut], fps: int = 30) -> RenderGraph:
    return RenderGraph(
        job_id="ticks-job",
        width=1920,
        height=1080,
        fps=fps,
        source="in.mp4",
        cuts=cuts,
    )


def test_render_graph_tick_helpers() -> None:
    graph = _graph(
        [
            Cut(source_start=0.0, source_end=2.0, timeline_start=0.0, timeline_end=2.0),
            Cut(source_start=10.0, source_end=13.0, timeline_start=2.0, timeline_end=5.0),
        ]
    )
    assert graph.duration_ticks == seconds_to_ticks(5.0)
    assert graph.frame_count() == 150
    assert graph.cuts_contiguous_exact() is True


def test_render_graph_detects_tick_dust() -> None:
    """Boundaries that differ below the float tolerance still fail the exact check."""
    dust = 0.0005  # below _TIMELINE_EPSILON (1e-3) so float validation passes
    graph = _graph(
        [
            Cut(source_start=0.0, source_end=2.0, timeline_start=0.0, timeline_end=2.0),
            Cut(
                source_start=2.0,
                source_end=4.0,
                timeline_start=2.0 + dust,
                timeline_end=4.0 + dust,
            ),
        ]
    )
    # Float validation tolerated the dust; the exact tick check catches it.
    assert graph.cuts_contiguous_exact() is False


def test_edl_and_segment_tick_helpers() -> None:
    edl = EDL(
        job_id="ticks-job",
        source_path="in.mp4",
        segments=[
            Segment(
                id="s0",
                source_start=0.0,
                source_end=2.0,
                timeline_start=0.0,
                timeline_end=2.0,
            ),
            Segment(
                id="s1",
                source_start=2.0,
                source_end=5.0,
                timeline_start=2.0,
                timeline_end=5.0,
            ),
        ],
    )
    assert edl.duration_ticks() == seconds_to_ticks(5.0)
    assert edl.frame_count(30) == 150
    assert edl.frame_count(NTSC_24) == frames_for_ticks(seconds_to_ticks(5.0), NTSC_24)
    assert edl.segments[1].duration_ticks == seconds_to_ticks(3.0)
