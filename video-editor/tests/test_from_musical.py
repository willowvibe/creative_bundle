"""Tests for the musical → Composition adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_core.adapters.from_musical import from_musical
from video_editor_core.composition import Canvas, Composition
from video_editor_core.musical import MusicalSegment, MusicalSource, plan_segments


def _canvas() -> Canvas:
    return Canvas(width=1920, height=1080, fps=30)


def _sources() -> list[MusicalSource]:
    return [
        MusicalSource(path=Path("a.png"), kind="image"),
        MusicalSource(path=Path("b.mp4"), kind="video", duration=30.0),
    ]


def _plan(
    *,
    music: float = 16.0,
    segment: float = 8.0,
    transition: float = 1.5,
) -> list[MusicalSegment]:
    return plan_segments(
        _sources(),
        music_duration=music,
        segment_seconds=segment,
        transition_seconds=transition,
    )


def test_scenes_follow_the_planned_windows():
    comp = from_musical(_plan(), [], job_id="job", canvas=_canvas())
    assert [(scene.start, scene.end) for scene in comp.scenes] == [(0.0, 8.0), (8.0, 16.0)]
    assert comp.duration == pytest.approx(16.0)
    assert comp.scenes[1].transition_in is not None
    assert comp.scenes[1].transition_in.name == "crossfade"
    assert comp.scenes[1].transition_in.duration == pytest.approx(1.5)
    assert comp.scenes[0].transition_in is None


def test_stills_get_the_halved_drift_and_videos_do_not():
    comp = from_musical(_plan(), [], job_id="job", canvas=_canvas())
    still = comp.scenes[0].layers[0]
    video = comp.scenes[1].layers[0]
    assert still.media_kind == "image"
    assert [motion.name for motion in still.motions] == ["drift"]
    assert still.motions[0].params["from"].startswith("scale(1.00)")
    assert still.motions[0].params["to"].startswith("scale(1.04)")
    assert still.motions[0].ease == "linear"
    assert video.media_kind == "video"
    assert video.motions == []


def test_video_segments_are_slowed_and_trim_from_the_start():
    comp = from_musical(_plan(), [], job_id="job", canvas=_canvas())
    video = comp.scenes[1].layers[0]
    assert video.style["playback_rate"] == 0.5
    assert video.style["media_start"] == 0.0
    assert video.style["object_fit"] == "cover"


def test_captions_are_evenly_spaced_and_bottom_placed():
    comp = from_musical(_plan(), ["one", "two", "three", "four"], job_id="job", canvas=_canvas())
    captions = [layer for scene in comp.scenes for layer in scene.layers if layer.kind == "text"]
    assert [caption.time.start for caption in captions] == [0.0, 4.0, 8.0, 12.0]
    assert [caption.time.end for caption in captions] == [4.0, 8.0, 12.0, 16.0]
    first = captions[0]
    # The text top sits 50 px above the bottom edge; the box adds 20 px of
    # padding above and below it (matching drawtext's boxborderw).
    assert first.rect.y == 1080 - 2 * 48 - 50 - 20
    assert first.rect.x == 86
    assert first.rect.width == 1920 - 2 * 86
    assert first.rect.height == 136
    assert first.style["font"] == "Noto Sans Devanagari"
    assert first.style["font_size"] == 40
    assert first.style["padding"] == 20
    assert first.style["valign"] == "top"
    assert first.style["background_hug"] is True
    assert first.motions[0].name == "pop"
    assert first.motions[0].params["from_scale"] == 0.55
    assert first.motions[0].duration == pytest.approx(0.35)


def test_captions_are_attached_to_the_scene_that_contains_their_start():
    comp = from_musical(_plan(), ["one", "two", "three", "four"], job_id="job", canvas=_canvas())
    first_ids = [layer.id for layer in comp.scenes[0].layers]
    second_ids = [layer.id for layer in comp.scenes[1].layers]
    assert first_ids == ["segment_0", "caption_0", "caption_1"]
    assert second_ids == ["segment_1", "caption_2", "caption_3"]


def test_no_lyrics_means_no_caption_layers():
    comp = from_musical(_plan(), [], job_id="job", canvas=_canvas())
    assert all(len(scene.layers) == 1 for scene in comp.scenes)


def test_audio_stays_external():
    comp = from_musical(_plan(), [], job_id="job", canvas=_canvas())
    assert comp.audio == []
    assert comp.audio_mode == "external"


def test_the_result_satisfies_the_composition_invariants():
    comp = from_musical(_plan(), ["one"], job_id="job", canvas=_canvas())
    Composition.model_validate(comp.model_dump())


def test_the_transition_is_quantised_to_the_frame_grid():
    # The music stops 0.12 s into the second segment. Both the scene end and
    # the crossfade snap to the 30 fps grid (4 frames = 0.1333 s), so the
    # transition fills the short scene exactly.
    comp = from_musical(_plan(music=8.12), [], job_id="job", canvas=_canvas())
    assert comp.duration == pytest.approx(4 / 30 + 8.0)
    transition = comp.scenes[1].transition_in
    assert transition is not None
    assert transition.duration == pytest.approx(4 / 30)


def test_an_empty_plan_is_refused():
    with pytest.raises(ValueError, match="at least one segment"):
        from_musical([], [], job_id="job", canvas=_canvas())


def test_a_non_contiguous_plan_is_refused():
    source = _sources()[0]
    segments = [
        MusicalSegment(
            source=source,
            start=0.0,
            end=4.0,
            clip_duration=4.0,
            transition_in=None,
            motion_index=0,
        ),
        MusicalSegment(
            source=source,
            start=5.0,
            end=8.0,
            clip_duration=3.0,
            transition_in=0.5,
            motion_index=1,
        ),
    ]
    with pytest.raises(ValueError, match="starts at 5.000s, expected 4.000s"):
        from_musical(segments, [], job_id="job", canvas=_canvas())
