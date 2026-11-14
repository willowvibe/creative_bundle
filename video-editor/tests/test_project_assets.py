"""Tests for the project asset copier."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.project_assets import AssetCopier


def test_the_same_source_is_copied_once(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    # ``AssetCopier`` writes into an existing directory; every real caller
    # creates it first (``hyperframes_project``, ``scene_plan_composition`` and
    # the Task 6 compiler all ``mkdir(parents=True, exist_ok=True)``).
    (tmp_path / "assets").mkdir()
    copier = AssetCopier(tmp_path / "assets")
    assert copier.copy(str(source)) == "assets/clip.mp4"
    assert copier.copy(str(source)) == "assets/clip.mp4"
    assert len(list((tmp_path / "assets").iterdir())) == 1


def test_two_sources_sharing_a_basename_stay_distinct(tmp_path: Path) -> None:
    first = tmp_path / "a" / "still.png"
    second = tmp_path / "b" / "still.png"
    for path in (first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
    (tmp_path / "assets").mkdir()
    copier = AssetCopier(tmp_path / "assets")
    names = [copier.copy(str(first)), copier.copy(str(second))]
    assert names[0] == "assets/still.png"
    assert names[1] != names[0]
    assert (tmp_path / "assets" / "still.png").is_file()
    assert (tmp_path / names[1]).is_file()
