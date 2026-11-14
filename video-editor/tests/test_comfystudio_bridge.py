"""Tests for the ComfyStudio import/export bridge."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_comfystudio_bridge.exporter import export_to_comfystudio
from video_editor_comfystudio_bridge.importer import import_from_comfystudio
from video_editor_comfystudio_bridge.models import ComfyStudioProject
from video_editor_core.models.scene_plan import Scene, ScenePlan


def _make_png(tmp_path: Path, name: str = "img.png") -> Path:
    """Create a minimal 100x100 PNG using PIL."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    path = tmp_path / name
    img.save(path, format="PNG")
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
    """Exporter must create ``project.comfystudio`` and the expected folders."""
    plan = _scene_plan(tmp_path, [2.0, 3.0])
    project_root = tmp_path / "comfystudio"

    result = export_to_comfystudio(plan, project_root)

    assert result == project_root
    assert (project_root / "project.comfystudio").is_file()
    assert (project_root / "assets" / "images").is_dir()
    assert (project_root / "cache").is_dir()
    assert (project_root / "renders").is_dir()


def test_exported_assets_match_scenes(tmp_path: Path) -> None:
    """Every scene's image asset must be copied into the project assets folder."""
    plan = _scene_plan(tmp_path, [1.0, 2.0, 3.0])
    project_root = tmp_path / "comfystudio"

    export_to_comfystudio(plan, project_root)

    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    assert len(project.assets) == 3
    for asset in project.assets:
        assert asset.type == "image"
        copied = project_root / asset.url
        assert copied.is_file()


def test_export_timeline_duration_sums_scenes(tmp_path: Path) -> None:
    """Timeline duration must equal the cumulative scene duration."""
    plan = _scene_plan(tmp_path, [2.0, 2.5, 1.5])
    project_root = tmp_path / "comfystudio"

    export_to_comfystudio(plan, project_root)

    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    assert project.timelines[0].duration == pytest.approx(6.0)


def test_export_caption_clips_for_narration(tmp_path: Path) -> None:
    """Scenes with non-empty narration must produce text clips on a caption track."""
    plan = _scene_plan(tmp_path, [2.0])
    project_root = tmp_path / "comfystudio"

    export_to_comfystudio(plan, project_root)

    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    tracks = project.timelines[0].tracks
    caption_tracks = [t for t in tracks if t.name == "Captions"]
    assert caption_tracks
    assert len(caption_tracks[0].clips) == 1
    text_props = caption_tracks[0].clips[0].textProperties
    assert text_props is not None
    assert text_props.text == "Scene 0 narration"


def test_import_roundtrip_updates_durations(tmp_path: Path) -> None:
    """Importing an edited project must update scene durations in the plan."""
    original = _scene_plan(tmp_path, [2.0, 3.0])
    project_root = tmp_path / "comfystudio"
    export_to_comfystudio(original, project_root)

    # Simulate a user edit in ComfyStudio: double the first clip's duration.
    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    project.timelines[0].tracks[0].clips[0].duration = 4.0
    (project_root / "project.comfystudio").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    updated = import_from_comfystudio(project_root / "project.comfystudio", original)

    assert updated.scenes[0].duration_seconds == 4.0
    assert updated.scenes[1].duration_seconds == 3.0
    assert updated.script == original.script
    assert updated.job_id == original.job_id


def test_import_mismatch_warns_but_reconciles(tmp_path: Path) -> None:
    """A project with fewer clips than scenes should reconcile what it can."""
    original = _scene_plan(tmp_path, [2.0, 3.0, 4.0])
    project_root = tmp_path / "comfystudio"
    export_to_comfystudio(original, project_root)

    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    # Drop the last clip to simulate a removed scene.
    project.timelines[0].tracks[0].clips.pop()
    (project_root / "project.comfystudio").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    updated = import_from_comfystudio(project_root / "project.comfystudio", original)

    assert len(updated.scenes) == 2
    assert updated.scenes[0].duration_seconds == 2.0
    assert updated.scenes[1].duration_seconds == 3.0


def test_import_missing_project_file_raises(tmp_path: Path) -> None:
    """Importer must raise a clear error when the project file is missing."""
    plan = _scene_plan(tmp_path, [1.0])
    missing_project = tmp_path / "nope" / "project.comfystudio"

    with pytest.raises(FileNotFoundError):
        import_from_comfystudio(missing_project, plan)


def test_import_empty_timeline_raises(tmp_path: Path) -> None:
    """Importer must reject a project with no timelines."""
    plan = _scene_plan(tmp_path, [1.0])
    project_root = tmp_path / "empty"
    export_to_comfystudio(plan, project_root)
    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    project.timelines = []
    (project_root / "project.comfystudio").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no timelines"):
        import_from_comfystudio(project_root / "project.comfystudio", plan)


def test_import_no_reconciled_scenes_raises(tmp_path: Path) -> None:
    """Importer must raise when no clips can be matched to original scenes."""
    plan = _scene_plan(tmp_path, [1.0])
    project_root = tmp_path / "comfystudio"
    export_to_comfystudio(plan, project_root)

    project = ComfyStudioProject.model_validate_json(
        (project_root / "project.comfystudio").read_text(encoding="utf-8")
    )
    project.timelines[0].tracks[0].clips = []
    (project_root / "project.comfystudio").write_text(
        project.model_dump_json(indent=2, by_alias=True), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="No scenes could be reconciled"):
        import_from_comfystudio(project_root / "project.comfystudio", plan)
