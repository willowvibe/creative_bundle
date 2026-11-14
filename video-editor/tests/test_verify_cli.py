"""Tests for the verification CLI commands and worker payload schema."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner
from video_editor_state.queue import InMemoryJobQueue

from tests.conftest import _require_ffmpeg


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fresh_queue(monkeypatch: pytest.MonkeyPatch) -> InMemoryJobQueue:
    from video_editor_cli import main

    q = InMemoryJobQueue()
    monkeypatch.setattr(main, "_queue_instance", q, raising=False)
    return q


def _make_video(path: Path, duration: float = 1.0) -> Path:
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=320x240:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=1000:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


class TestRequirementsCommand:
    """Tests for ``ve requirements``."""

    def test_captures_requirements_json(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "requirements",
                "req-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--min-duration",
                "10.0",
                "--max-duration",
                "30.0",
                "--require-aspect",
                "16:9",
                "--require-element",
                "captions",
                "--require-element",
                "logo",
                "--must-mention",
                "subscribe",
                "--judge-check",
                "quality",
            ],
        )
        assert result.exit_code == 0, result.output
        requirements_path = tmp_path / "work" / "req-job" / "verify" / "requirements.json"
        assert requirements_path.exists()
        data = json.loads(requirements_path.read_text(encoding="utf-8"))
        assert data["target_duration_range"] == {"min": 10.0, "max": 30.0}
        assert data["aspect_ratio"] == "16:9"
        assert data["required_elements"] == ["captions", "logo"]
        assert data["content_constraints"]["must_mention"] == ["subscribe"]
        assert data["judge_checklist"] == ["quality"]

    def test_empty_requirements_validation_fails(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "requirements",
                "empty-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        # Empty spec fails validation.
        assert result.exit_code != 0


class TestVerifyCommand:
    """Tests for ``ve verify``."""

    def test_verify_runs_deterministic_checks(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        from video_editor_cli.main import app

        work = tmp_path / "work" / "verify-cli-job"
        output = tmp_path / "output" / "verify-cli-job"
        work.mkdir(parents=True)
        output.mkdir(parents=True)
        _make_video(output / "final.mp4", duration=2.0)

        # Capture requirements first.
        cli_runner.invoke(
            app,
            [
                "requirements",
                "verify-cli-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--min-duration",
                "1.0",
                "--max-duration",
                "3.0",
            ],
        )

        result = cli_runner.invoke(
            app,
            [
                "verify",
                "verify-cli-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "PASS" in result.output
        report_path = work / "verify" / "verification_report.json"
        assert report_path.exists()

    def test_verify_fails_when_requirements_not_captured(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        from video_editor_cli.main import app

        work = tmp_path / "work" / "no-reqs-job"
        output = tmp_path / "output" / "no-reqs-job"
        work.mkdir(parents=True)
        output.mkdir(parents=True)
        _make_video(output / "final.mp4", duration=1.0)

        result = cli_runner.invoke(
            app,
            [
                "verify",
                "no-reqs-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
            ],
        )
        assert result.exit_code == 1


class TestVerifyPayloadSchema:
    """Bug regression: ``verify`` payload must validate against the schema."""

    def test_verify_payload_validates(
        self,
        cli_runner: CliRunner,
        fresh_queue: InMemoryJobQueue,
        tmp_path: Path,
    ) -> None:
        from video_editor_cli.main import _PAYLOAD_SCHEMAS

        assert "verify" in _PAYLOAD_SCHEMAS
        schema = _PAYLOAD_SCHEMAS["verify"]
        schema.model_validate(
            {
                "work_root": str(tmp_path / "work"),
                "output_root": str(tmp_path / "output"),
            }
        )

    def test_verify_handler_registered(
        self,
    ) -> None:
        from video_editor_cli.main import _HANDLERS

        assert "verify" in _HANDLERS
