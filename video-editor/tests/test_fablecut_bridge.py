"""Tests for the FableCut import/export bridge."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_fablecut_bridge.exporter import export_to_fablecut
from video_editor_fablecut_bridge.importer import import_from_fablecut
from video_editor_fablecut_bridge.models import FableCutProject


def _make_png(tmp_path: Path, name: str = "img.png") -> Path:
    """Create a minimal 100x100 PNG using PIL."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    path = tmp_path / name
    img.save(path, format="PNG")
    return path


def _make_wav(tmp_path: Path, name: str = "audio.wav") -> Path:
    """Create a minimal 1-second silent WAV using Python's wave module."""
    import wave

    path = tmp_path / name
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00" * 44100 * 2)
    return path


def _scene_plan(tmp_path: Path, durations: list[float]) -> ScenePlan:
    """Build a ``ScenePlan`` with one image-backed scene per duration."""
    scenes = []
    for i, duration in enumerate(durations):
        img_path = _make_png(tmp_path, f"scene{i}.png")
        scenes.append(
            Scene(
                id=f"scene_{i}",
                type="narration",
                duration_seconds=duration,
                narration=f"Scene {i} narration",
                visual_description=f"desc {i}",
                assets=[
                    AssetResult(
                        asset_id=f"img_{i}",
                        asset_type=AssetType.IMAGE,
                        local_path=str(img_path),
                        scene_id=f"scene_{i}",
                    )
                ],
            )
        )
    return ScenePlan(job_id="test_job", script="test script", scenes=scenes)


def test_export_writes_project_file(tmp_path: Path) -> None:
    """Exporter must create ``project.json`` and the expected folders."""
    plan = _scene_plan(tmp_path, [2.0, 3.0])
    project_root = tmp_path / "fablecut"

    result = export_to_fablecut(plan, project_root)

    assert result == project_root
    assert (project_root / "project.json").is_file()
    assert (project_root / "media").is_dir()


def test_exported_assets_match_scenes(tmp_path: Path) -> None:
    """Every scene's image asset must be copied into the project media folder."""
    plan = _scene_plan(tmp_path, [1.0, 2.0, 3.0])
    project_root = tmp_path / "fablecut"

    export_to_fablecut(plan, project_root)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    image_media = [m for m in project.media if m.kind == "image"]
    assert len(image_media) == 3
    for media in image_media:
        copied = project_root / media.src.lstrip("/")
        assert copied.is_file()


def test_export_timeline_duration_sums_scenes(tmp_path: Path) -> None:
    """Clip start times must cover the cumulative scene duration."""
    plan = _scene_plan(tmp_path, [2.0, 2.5, 1.5])
    project_root = tmp_path / "fablecut"

    export_to_fablecut(plan, project_root)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    visual_clips = [c for c in project.clips if c.kind in {"video", "image"}]
    assert [c.start for c in visual_clips] == pytest.approx([0.0, 2.0, 4.5])
    assert [c.duration for c in visual_clips] == pytest.approx([2.0, 2.5, 1.5])


def test_export_caption_from_lyrics_path(tmp_path: Path) -> None:
    """A lyrics file produces bottom captions per scene."""
    plan = _scene_plan(tmp_path, [2.0, 3.0])
    # Clear narration so captions come only from lyrics.
    for scene in plan.scenes:
        scene.narration = ""
    lyrics_file = tmp_path / "lyrics.txt"
    lyrics_file.write_text("Line one\n\nLine two\n", encoding="utf-8")
    project_root = tmp_path / "fablecut"

    export_to_fablecut(plan, project_root, lyrics_path=lyrics_file)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    caption_clips = [c for c in project.clips if c.name.startswith("caption:")]
    assert len(caption_clips) == 2
    assert caption_clips[0].props.text == "Line one"
    assert caption_clips[1].props.text == "Line two"
    assert all(c.track == "V2" for c in caption_clips)


def test_export_video_clips_are_muted(tmp_path: Path) -> None:
    """Video clips must have volume=0 so only the supplied music is heard."""
    from video_editor_asset_ingestion.models import AssetResult, AssetType

    video_path = tmp_path / "clip.mp4"
    # Create a tiny valid MP4 via FFmpeg.
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=100x100:rate=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    scene = Scene(
        id="scene_0",
        type="music_visual",
        duration_seconds=1.0,
        narration="",
        visual_description="",
        assets=[
            AssetResult(
                asset_id="vid_0",
                asset_type=AssetType.VIDEO,
                local_path=str(video_path),
                scene_id="scene_0",
            )
        ],
    )
    plan = ScenePlan(job_id="test", script="test", scenes=[scene])
    project_root = tmp_path / "fablecut"

    export_to_fablecut(plan, project_root)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    video_clips = [c for c in project.clips if c.kind == "video"]
    assert len(video_clips) == 1
    assert video_clips[0].props.volume == pytest.approx(0.0)


def test_export_music_clip_on_separate_audio_track(tmp_path: Path) -> None:
    """A supplied music path must produce a background-music clip."""
    plan = _scene_plan(tmp_path, [2.0, 3.0])
    project_root = tmp_path / "fablecut"
    music_path = _make_wav(tmp_path, "music.wav")

    export_to_fablecut(plan, project_root, music_path=music_path)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    music_clips = [c for c in project.clips if c.kind == "audio" and c.name == "music"]
    assert len(music_clips) == 1
    assert music_clips[0].track == "A3"
    assert music_clips[0].start == 0.0
    assert music_clips[0].duration == pytest.approx(5.0)


def test_import_roundtrip_updates_durations(tmp_path: Path) -> None:
    """Importing an edited project must update scene durations in the plan."""
    original = _scene_plan(tmp_path, [2.0, 3.0])
    project_root = tmp_path / "fablecut"
    export_to_fablecut(original, project_root)

    # Simulate a user edit in FableCut: double the first visual clip's duration.
    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    visual_clips = [c for c in project.clips if c.kind in {"video", "image"}]
    visual_clips[0].duration = 4.0
    (project_root / "project.json").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    updated = import_from_fablecut(project_root / "project.json", original)

    assert updated.scenes[0].duration_seconds == 4.0
    assert updated.scenes[1].duration_seconds == 3.0
    assert updated.script == original.script
    assert updated.job_id == original.job_id


def test_import_mismatch_warns_but_reconciles(tmp_path: Path) -> None:
    """A project with fewer clips than scenes should reconcile what it can."""
    original = _scene_plan(tmp_path, [2.0, 3.0, 4.0])
    project_root = tmp_path / "fablecut"
    export_to_fablecut(original, project_root)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    # Drop the last scene's clips (visual, caption, audio) to simulate removal.
    project.clips = [
        c
        for c in project.clips
        if not (
            c.name.startswith("scene_2:")
            or c.name.startswith("caption:scene_2")
            or c.name.startswith("audio:scene_2")
        )
    ]
    (project_root / "project.json").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    updated = import_from_fablecut(project_root / "project.json", original)

    assert len(updated.scenes) == 2
    assert updated.scenes[0].duration_seconds == 2.0
    assert updated.scenes[1].duration_seconds == 3.0


def test_import_missing_project_file_raises(tmp_path: Path) -> None:
    """Importer must raise a clear error when the project file is missing."""
    plan = _scene_plan(tmp_path, [1.0])
    missing_project = tmp_path / "nope" / "project.json"

    with pytest.raises(FileNotFoundError):
        import_from_fablecut(missing_project, plan)


def test_import_no_visual_clips_raises(tmp_path: Path) -> None:
    """Importer must raise when no visual clips can be matched."""
    plan = _scene_plan(tmp_path, [1.0])
    project_root = tmp_path / "fablecut"
    export_to_fablecut(plan, project_root)

    project = FableCutProject.model_validate_json(
        (project_root / "project.json").read_text(encoding="utf-8")
    )
    project.clips = [c for c in project.clips if not c.track.startswith("V")]
    (project_root / "project.json").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="No scenes could be reconciled"):
        import_from_fablecut(project_root / "project.json", plan)
