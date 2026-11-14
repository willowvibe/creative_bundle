"""Integration tests for the ve preview command (FableCut edit loop)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from video_editor_cli.commands.preview import (
    _export_project,
    _find_fablecut_dir,
    _is_fablecut_running,
)


class TestPreviewHelpers:
    def test_find_fablecut_dir_env_var(self, tmp_path: Path) -> None:
        """FABLECUT_PATH env var should take precedence."""
        fablecut_dir = tmp_path / "my-fablecut"
        fablecut_dir.mkdir()
        with patch.dict("os.environ", {"FABLECUT_PATH": str(fablecut_dir)}):
            result = _find_fablecut_dir()
            assert result == fablecut_dir

    def test_find_fablecut_dir_default(self, tmp_path: Path) -> None:
        """Default path is REPO_ROOT/tools/FableCut."""
        fablecut_dir = tmp_path / "tools" / "FableCut"
        fablecut_dir.mkdir(parents=True)
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("video_editor_cli.commands.preview.FABLECUT_DEFAULT_DIR", fablecut_dir),
        ):
            result = _find_fablecut_dir()
            assert result == fablecut_dir

    def test_find_fablecut_dir_not_found(self, tmp_path: Path) -> None:
        """Should raise SystemExit when FableCut is not found."""
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "video_editor_cli.commands.preview.FABLECUT_DEFAULT_DIR", tmp_path / "nonexistent"
            ),
            pytest.raises(typer.Exit),
        ):
            _find_fablecut_dir()

    def test_is_fablecut_running_true(self) -> None:
        """Should detect running server via HTTP check."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("httpx.get", return_value=mock_response):
            assert _is_fablecut_running() is True

    def test_is_fablecut_running_false(self) -> None:
        """Should return False when server is not reachable."""
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            assert _is_fablecut_running() is False

    def test_export_project_creates_files(self, tmp_path: Path) -> None:
        """Export should create project.json and media/ directory."""
        from video_editor_core.models.scene_plan import Scene, ScenePlan

        plan = ScenePlan(
            job_id="test",
            script="test",
            aspect_ratio="16:9",
            music_mood="cinematic",
            scenes=[
                Scene(
                    id="scene_001",
                    type="narration",
                    duration_seconds=5.0,
                    narration="Hello",
                    visual_description="A test image",
                )
            ],
        )
        project_dir = tmp_path / "fablecut"
        result = _export_project(plan, project_dir)
        assert (result / "project.json").exists()
        assert (result / "media").is_dir()
