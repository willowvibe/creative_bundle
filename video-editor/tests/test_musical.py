"""Tests for the musical-video segment planner."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.musical import (
    MusicalPlanError,
    MusicalSource,
    discover_media,
    plan_segments,
)


def _image(name: str) -> MusicalSource:
    return MusicalSource(path=Path(name), kind="image")


def _video(name: str, duration: float = 12.0) -> MusicalSource:
    return MusicalSource(path=Path(name), kind="video", duration=duration)


def _windows(segments) -> list[tuple[float, float]]:
    return [(segment.start, segment.end) for segment in segments]


def test_full_segments_match_the_script_offsets():
    segments = plan_segments(
        [_image("a.png")],
        music_duration=32.0,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert _windows(segments) == [(0.0, 8.0), (8.0, 16.0), (16.0, 24.0), (24.0, 32.0)]
    assert [segment.transition_in for segment in segments] == [None, 1.5, 1.5, 1.5]
    # Each non-last segment also carries the tail the next crossfade consumes.
    assert [segment.clip_duration for segment in segments] == [9.5, 9.5, 9.5, 8.0]


def test_a_clamped_intermediate_segment_keeps_the_script_offsets():
    # Bug #193's fixture: music 17 s, 8 s segments, 1.5 s transitions.
    # The script's source durations are [9.5, 9.0, 1.0]; the second xfade
    # clamps to the last segment's 1.0 s of material.
    segments = plan_segments(
        [_image("a.png"), _image("b.png"), _image("c.png")],
        music_duration=17.0,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert _windows(segments) == [(0.0, 8.0), (8.0, 16.0), (16.0, 17.0)]
    assert [segment.transition_in for segment in segments] == [None, 1.5, 1.0]
    assert [segment.clip_duration for segment in segments] == [9.5, 9.0, 1.0]
    assert segments[-1].visible == pytest.approx(1.0)


def test_sources_cycle_in_order():
    sources = [_image("a.png"), _video("b.mp4")]
    segments = plan_segments(
        sources,
        music_duration=32.0,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert [segment.source.path.name for segment in segments] == [
        "a.png",
        "b.mp4",
        "a.png",
        "b.mp4",
    ]
    assert [segment.motion_index for segment in segments] == [0, 1, 2, 3]


def test_a_music_shorter_than_one_segment_is_one_scene():
    segments = plan_segments(
        [_image("a.png")],
        music_duration=3.25,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert _windows(segments) == [(0.0, 3.25)]
    assert segments[0].transition_in is None
    assert segments[0].clip_duration == pytest.approx(3.25)


def test_windows_are_contiguous_and_cover_the_timeline():
    segments = plan_segments(
        [_image("a.png")],
        music_duration=17.0,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert segments[0].start == 0.0
    for previous, following in zip(segments[:-1], segments[1:], strict=True):
        assert following.start == pytest.approx(previous.end)
    assert segments[-1].end == pytest.approx(17.0)


@pytest.mark.parametrize("transition", [0.0, -0.5, 8.0, 9.5])
def test_the_transition_must_be_positive_and_shorter_than_a_segment(transition):
    with pytest.raises(MusicalPlanError, match="transition_seconds"):
        plan_segments(
            [_image("a.png")],
            music_duration=32.0,
            segment_seconds=8.0,
            transition_seconds=transition,
        )


@pytest.mark.parametrize("segment", [0.0, -1.0, float("nan")])
def test_the_segment_length_must_be_positive_and_finite(segment):
    with pytest.raises(MusicalPlanError, match="segment_seconds"):
        plan_segments(
            [_image("a.png")],
            music_duration=32.0,
            segment_seconds=segment,
            transition_seconds=1.5,
        )


@pytest.mark.parametrize("music", [0.0, -1.0, float("inf")])
def test_the_music_length_must_be_positive_and_finite(music):
    with pytest.raises(MusicalPlanError, match="music_duration"):
        plan_segments(
            [_image("a.png")],
            music_duration=music,
            segment_seconds=8.0,
            transition_seconds=1.5,
        )


def test_at_least_one_source_is_required():
    with pytest.raises(MusicalPlanError, match="at least one media source"):
        plan_segments(
            [],
            music_duration=32.0,
            segment_seconds=8.0,
            transition_seconds=1.5,
        )


def test_an_unknown_media_kind_is_refused():
    with pytest.raises(MusicalPlanError, match="unknown kind"):
        plan_segments(
            [MusicalSource(path=Path("a.gif"), kind="gif")],  # type: ignore[arg-type]
            music_duration=8.0,
            segment_seconds=8.0,
            transition_seconds=1.5,
        )


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan")])
def test_a_probed_source_duration_must_be_positive(duration):
    with pytest.raises(MusicalPlanError, match="duration must be a positive"):
        plan_segments(
            [_video("a.mp4", duration=duration)],
            music_duration=8.0,
            segment_seconds=8.0,
            transition_seconds=1.5,
        )


def test_a_transition_equal_to_the_last_scene_is_allowed():
    # The music stops half a second into the second segment, so the crossfade
    # clamps to that half second; the pre-pass accepts a transition equal to
    # either scene's length.
    segments = plan_segments(
        [_image("a.png")],
        music_duration=8.5,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert [segment.transition_in for segment in segments] == [None, 0.5]
    assert segments[-1].visible == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Media discovery
# ---------------------------------------------------------------------------


def test_discover_media_sorts_and_marks_each_kind(tmp_path):
    (tmp_path / "b.mp4").write_bytes(b"mp4")
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "notes.txt").write_text("ignore me")
    found = discover_media(tmp_path)
    assert [source.path.name for source in found] == ["a.png", "b.mp4"]
    assert [source.kind for source in found] == ["image", "video"]


def test_discover_media_skips_generated_intermediates(tmp_path):
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "a_fixed_audio.mp4").write_bytes(b"mp4")
    (tmp_path / "a_remux.mp4").write_bytes(b"mp4")
    assert [source.path.name for source in discover_media(tmp_path)] == ["a.png"]


def test_discover_media_excludes_the_output_and_music(tmp_path):
    (tmp_path / "a.png").write_bytes(b"png")
    (tmp_path / "music.mp3").write_bytes(b"mp3")
    output = tmp_path / "final.mp4"
    output.write_bytes(b"mp4")
    found = discover_media(tmp_path, exclude={tmp_path / "music.mp3", output})
    assert [source.path.name for source in found] == ["a.png"]


def test_discover_media_refuses_an_empty_folder(tmp_path):
    (tmp_path / "notes.txt").write_text("ignore me")
    with pytest.raises(MusicalPlanError, match="no supported media"):
        discover_media(tmp_path)


def test_discover_media_refuses_a_missing_folder(tmp_path):
    with pytest.raises(MusicalPlanError, match="media directory not found"):
        discover_media(tmp_path / "missing")
