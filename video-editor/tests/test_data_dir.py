"""H2: one data-dir env moves the run roots without changing the default."""

from __future__ import annotations

from pathlib import Path

from video_editor_state import paths


def test_data_dir_moves_work_and_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(paths.WORK_ROOT_ENV, raising=False)
    monkeypatch.delenv(paths.OUTPUT_ROOT_ENV, raising=False)
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))

    assert paths.default_work_root() == str(tmp_path / "data" / "projects")
    assert paths.default_output_root() == str(tmp_path / "data" / "outputs")


def test_per_root_env_still_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(paths.WORK_ROOT_ENV, str(tmp_path / "work"))
    monkeypatch.setenv(paths.OUTPUT_ROOT_ENV, str(tmp_path / "out"))

    assert paths.default_work_root() == str(tmp_path / "work")
    assert paths.default_output_root() == str(tmp_path / "out")
