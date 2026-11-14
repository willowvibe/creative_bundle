"""Tests for the new ve storyteller-export and ve tts-test commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner


def test_storyteller_export_runs_fablecut_export(tmp_path: Path) -> None:
    from video_editor_cli.main import app

    plan_path = tmp_path / "scene_plan.json"
    plan_path.write_text(
        '{"job_id": "j", "script": "", "scenes": [{"id": "scene_001", "type": "story", "duration_seconds": 5.0}]}',
        encoding="utf-8",
    )
    project_dir = tmp_path / "fablecut"
    project_dir.mkdir()

    runner = CliRunner()
    with patch("video_editor_cli.main.storyteller_to_fablecut", new=AsyncMock()) as mock_export:
        result = runner.invoke(
            app,
            ["storyteller-export", "j", "--project-dir", str(project_dir)],
        )
    assert result.exit_code == 0, result.output
    assert mock_export.await_count == 1


def test_tts_test_writes_audition_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    monkeypatch.chdir(tmp_path)
    from video_editor_cli.main import app

    with patch(
        "video_editor_cli.main._tts_test_synthesize",
        new=AsyncMock(return_value=tmp_path / "audition.mp3"),
    ):
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["tts-test", "--text", "Hello", "--voice", "sarvam:shubh", "--language", "hi-IN"],
        )
    assert result.exit_code == 0, result.output
