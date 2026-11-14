"""Tests for the OpenTimelineIO interchange (fablecut_bridge.otio)."""

from __future__ import annotations

from pathlib import Path

from video_editor_fablecut_bridge.models import (
    FableCutClip,
    FableCutMarker,
    FableCutMedia,
    FableCutProject,
    FableCutProps,
)
from video_editor_fablecut_bridge.otio import (
    export_otio,
    from_otio,
    import_otio,
    to_otio,
)


def _project() -> FableCutProject:
    return FableCutProject(
        name="job-1",
        width=1080,
        height=1920,
        fps=30,
        markers=[FableCutMarker(t=1.5, label="beat")],
        media=[
            FableCutMedia(id="m1", name="a.mp4", kind="video", src="media/a.mp4"),
            FableCutMedia(id="m2", name="bg.mp3", kind="audio", src="media/bg.mp3"),
        ],
        clips=[
            FableCutClip(
                id="c1",
                mediaId="m1",
                kind="video",
                track="V1",
                start=0.0,
                in_=0.0,
                duration=2.0,
                name="a",
            ),
            FableCutClip(
                id="c2",
                mediaId="m1",
                kind="video",
                track="V1",
                start=3.0,
                in_=1.0,
                duration=1.5,
                name="b",
            ),
            FableCutClip(
                id="c3",
                mediaId="m2",
                kind="audio",
                track="A1",
                start=0.0,
                in_=0.0,
                duration=5.0,
                name="music",
                props=FableCutProps(volume=0.3),
            ),
            FableCutClip(id="c4", kind="text", track="V2", start=0.5, duration=2.0, name="title"),
        ],
    )


def test_to_otio_maps_tracks_media_and_markers() -> None:
    timeline = to_otio(_project())
    assert timeline.name == "job-1"
    assert timeline.metadata["fablecut"]["fps"] == 30
    names = [t.name for t in timeline.tracks]
    assert names == ["V1", "A1", "V2"]
    assert timeline.tracks[0].kind == "Video"
    assert timeline.tracks[1].kind == "Audio"
    # first V1 clip keeps its external media reference
    first = next(i for i in timeline.tracks[0] if hasattr(i, "media_reference"))
    assert first.media_reference.target_url == "media/a.mp4"
    # a gap is inserted between the two V1 clips (gap from 2.0 to 3.0)
    assert any(type(i).__name__ == "Gap" for i in timeline.tracks[0])
    # markers are mirrored on the first track (OTIO 0.18 dropped Timeline.markers)
    # and kept authoritatively in metadata for lossless round-trips
    assert timeline.tracks[0].markers[0].name == "beat"
    meta_markers = timeline.metadata["fablecut"]["markers"]
    assert float(meta_markers[0]["t"]) == 1.5 and meta_markers[0]["label"] == "beat"


def test_round_trip_preserves_project() -> None:
    original = _project()
    restored = from_otio(to_otio(original))
    assert restored.name == original.name
    assert (restored.width, restored.height, restored.fps) == (1080, 1920, 30)
    assert {m.id for m in restored.media} == {"m1", "m2"}
    assert len(restored.clips) == len(original.clips)
    by_id = {c.id: c for c in restored.clips}
    assert by_id["c2"].start == 3.0 and by_id["c2"].in_ == 1.0
    assert by_id["c3"].props.volume == 0.3
    assert by_id["c4"].kind == "text"
    assert restored.markers[0].t == 1.5 and restored.markers[0].label == "beat"


def test_export_import_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "project.otio"
    export_otio(_project(), path)
    assert path.exists()
    restored = import_otio(path)
    assert restored.name == "job-1"
    assert len(restored.clips) == 4


def test_import_otio_missing_file_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        import_otio(tmp_path / "nope.otio")
