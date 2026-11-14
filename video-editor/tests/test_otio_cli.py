"""Tests for the OTIO export/import plan round-trip (ve export-otio / import-otio)."""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_fablecut_bridge.exporter import build_fablecut_project
from video_editor_fablecut_bridge.importer import (
    import_otio_plan,
    plan_from_fablecut_project,
)
from video_editor_fablecut_bridge.models import (
    FableCutClip,
    FableCutMedia,
    FableCutProject,
)
from video_editor_fablecut_bridge.otio import export_otio, import_otio


def _plan() -> ScenePlan:
    return ScenePlan(
        job_id="otio-job",
        script="demo",
        scenes=[
            Scene(id="s1", type="narration", duration_seconds=2.0, narration="a"),
            Scene(id="s2", type="narration", duration_seconds=3.0, narration="b"),
        ],
    )


def _project() -> FableCutProject:
    return FableCutProject(
        name="otio-job",
        width=1080,
        height=1920,
        fps=30,
        media=[FableCutMedia(id="m1", name="a.png", kind="image", src="/media/a.png")],
        clips=[
            FableCutClip(
                id="c1",
                mediaId="m1",
                kind="image",
                track="V1",
                start=0.0,
                in_=0.0,
                duration=2.5,
                name="s1",
            ),
            FableCutClip(
                id="c2",
                mediaId="m1",
                kind="image",
                track="V1",
                start=2.5,
                in_=0.0,
                duration=3.5,
                name="s2",
            ),
        ],
    )


def test_plan_from_fablecut_project_updates_durations() -> None:
    updated = plan_from_fablecut_project(_project(), _plan())
    assert [s.duration_seconds for s in updated.scenes] == [2.5, 3.5]
    # Identity fields survive the reconcile.
    assert [s.id for s in updated.scenes] == ["s1", "s2"]


def test_export_otio_resolves_absolute_media(tmp_path: Path) -> None:
    out = export_otio(_project(), tmp_path / "project.otio", media_root=tmp_path)
    timeline = otio.adapters.read_from_file(str(out))
    clip = next(
        item for track in timeline.tracks for item in track if hasattr(item, "media_reference")
    )
    assert clip.media_reference.target_url == str((tmp_path / "media" / "a.png").resolve())


def test_import_otio_plan_roundtrip(tmp_path: Path) -> None:
    otio_file = export_otio(_project(), tmp_path / "edited.otio")
    assert import_otio(otio_file).name == "otio-job"
    updated = import_otio_plan(otio_file, _plan())
    assert [s.duration_seconds for s in updated.scenes] == [2.5, 3.5]


def test_build_fablecut_project_without_assets(tmp_path: Path) -> None:
    """The builder is usable standalone (no media copied when there are no assets)."""
    project = build_fablecut_project(_plan(), tmp_path / "proj")
    assert isinstance(project, FableCutProject)
    assert project.name == "otio-job"
    assert (tmp_path / "proj" / "media").is_dir()
