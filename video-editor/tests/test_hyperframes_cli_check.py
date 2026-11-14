"""Tests for the check/snapshot CLI wrappers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_core import hyperframes_cli
from video_editor_core.hyperframes_cli import (
    HyperFramesCheckError,
    check_project,
    snapshot_project,
)


def test_check_project_runs_the_checker_and_returns_its_report(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="0 issues\n", stderr="")

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    report = check_project(tmp_path, strict=True)
    assert report == "0 issues\n"
    assert Path(seen[0][0]).name == "hyperframes"
    assert seen[0][1] == "check"
    assert "--strict" in seen[0]


def test_check_project_raises_with_the_cli_messages(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        from video_editor_ffmpeg.run import CommandError

        raise CommandError(
            "exit 1", cmd, stdout="", stderr="layout: text overflows\ncontrast: 2.1\n"
        )

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    with pytest.raises(HyperFramesCheckError) as exc:
        check_project(tmp_path)
    assert "text overflows" in str(exc.value)
    assert any("contrast" in message for message in exc.value.messages)


def test_snapshot_project_collects_pngs(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "at-0s.png").write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    shots = snapshot_project(tmp_path / "proj", tmp_path / "shots", at=[0.0, 2.5])
    assert [path.name for path in shots] == ["at-0s.png"]


def test_snapshot_project_never_returns_a_previous_runs_frames(tmp_path: Path, monkeypatch) -> None:
    """A stale PNG in the output dir must not be read as this run's evidence."""
    stale_dir = tmp_path / "shots"
    stale_dir.mkdir()
    (stale_dir / "at-0s.png").write_bytes(b"old")

    def fake_run(cmd, **kwargs):
        output = Path(cmd[cmd.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "fresh.png").write_bytes(b"new")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    shots = snapshot_project(tmp_path / "proj", stale_dir, at=[0.0])
    assert [path.name for path in shots] == ["fresh.png"]
    assert shots[0].parent != stale_dir


def test_snapshot_project_passes_exact_times(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        output = Path(cmd[cmd.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "at-0s.png").write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    snapshot_project(tmp_path / "proj", tmp_path / "shots", at=[1.5, 12.25])
    assert "--at" in seen[0] and "1.5,12.25" in seen[0]
    assert "--no-end" in seen[0]


def test_snapshot_project_fails_when_no_frames_appear(tmp_path: Path, monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(hyperframes_cli, "run_subprocess", fake_run)
    with pytest.raises(hyperframes_cli.RenderSegmentError):
        snapshot_project(tmp_path / "proj", tmp_path / "shots", frames=3)
