"""Tests for the HyperFrames CLI wrapper.

The wrapper shells out to a `hyperframes` binary on PATH. These
tests guard the dispatch and subprocess contract without requiring
the real CLI to be installed — the binary is mocked via a
`subprocess.run` patch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from video_editor_core import hyperframes_cli


@pytest.fixture(autouse=True)
def stub_probe_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a valid MediaInfo-like object so render_segment doesn't call ffprobe."""

    def fake_probe(_path: Path) -> object:
        class FakeInfo:
            duration = 1.0
            width = 640
            height = 360

        return FakeInfo()

    monkeypatch.setattr("video_editor_core.hyperframes_cli.probe_media", fake_probe)


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def test_hyperframes_available_returns_bool() -> None:
    """The probe is a thin wrapper around shutil.which — must return a bool."""
    result = hyperframes_cli.hyperframes_available()
    assert isinstance(result, bool)


def test_hyperframes_available_false_for_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A custom CLI path that does not exist resolves to False."""
    assert hyperframes_cli.hyperframes_available(cli_path="definitely-not-a-real-binary") is False


def test_hyperframes_available_true_for_existing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CLI path that exists on PATH resolves to True."""
    real = shutil.which("sh")
    assert real is not None  # `sh` is on every Unix CI
    assert hyperframes_cli.hyperframes_available(cli_path="sh") is True


# ---------------------------------------------------------------------------
# render_segment
# ---------------------------------------------------------------------------


def _fake_segment_output(output: Path) -> None:
    """Write a tiny placeholder so the wrapper's return path is exercised."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"FAKE_MP4")


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal HyperFrames HTML project directory."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "index.html").write_text("<html></html>", encoding="utf-8")
    return project


def test_render_segment_invokes_subprocess_with_expected_cmd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_segment builds the documented CLI and runs it via subprocess.run."""
    plan = _make_project(tmp_path)
    output = tmp_path / "out" / "segment.mp4"

    captured: dict[str, Any] = {}

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        captured["check"] = check
        captured["timeout"] = timeout
        # Simulate the real CLI writing the output file.
        _fake_segment_output(Path(cmd[cmd.index("-o") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    result = hyperframes_cli.render_segment(plan, output, cli_path="hyperframes", timeout=120)

    assert result == output
    assert output.exists()
    assert captured["check"] is True
    assert captured["timeout"] == 120
    # The modern CLI contract: hyperframes render <project-dir> -o <out>
    assert Path(captured["cmd"][0]).name == "hyperframes"
    assert captured["cmd"][1] == "render"
    assert captured["cmd"][2] == str(plan.resolve())
    assert captured["cmd"][3] == "-o"
    assert captured["cmd"][4].endswith("segment.mp4")


def test_render_segment_appends_extra_args(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_args are appended to the CLI as --key value pairs."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    captured: dict[str, list[str]] = {}

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        _fake_segment_output(Path(cmd[cmd.index("-o") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    hyperframes_cli.render_segment(
        plan,
        output,
        cli_path="hyperframes",
        extra_args={"fps": 30, "codec": "h264"},
    )

    # The two extras each become --<key> <value> pairs, appended in order.
    assert "--fps" in captured["cmd"]
    assert "30" in captured["cmd"]
    assert "--codec" in captured["cmd"]
    assert "h264" in captured["cmd"]


def test_render_segment_stringifies_extra_arg_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extra_arg values are always stringified before being appended."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    captured: dict[str, list[str]] = {}

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        _fake_segment_output(Path(cmd[cmd.index("-o") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    hyperframes_cli.render_segment(
        plan,
        output,
        extra_args={"bitrate": 4_000_000, "verbose": True},
    )

    # Find the indices of the keys and verify the next token is a string.
    for key in ("--bitrate", "--verbose"):
        idx = captured["cmd"].index(key)
        value = captured["cmd"][idx + 1]
        assert isinstance(value, str), f"{key} value should be stringified"


def test_render_segment_creates_parent_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """render_segment creates the output's parent directory if it doesn't exist."""
    plan = _make_project(tmp_path)
    nested_output = tmp_path / "deep" / "nested" / "segment.mp4"

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        # Match real CLI behavior: write to the output path.
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"OK")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    result = hyperframes_cli.render_segment(plan, nested_output)
    assert result == nested_output
    assert nested_output.parent.exists()


def test_render_segment_raises_on_subprocess_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero CLI exit surfaces as RenderSegmentError with stderr."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        # Simulate the wrapper raising on a non-zero CLI exit.
        from video_editor_ffmpeg.run import CommandError

        raise CommandError("exit 1", cmd, stderr="hyperframes crashed")

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    with pytest.raises(hyperframes_cli.RenderSegmentError, match="hyperframes crashed"):
        hyperframes_cli.render_segment(plan, output)


def test_render_segment_default_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default timeout is the module-level DEFAULT_TIMEOUT (600s)."""
    assert hyperframes_cli.DEFAULT_TIMEOUT == 600

    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    captured: dict[str, int] = {}

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        captured["timeout"] = timeout
        _fake_segment_output(Path(cmd[cmd.index("-o") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)
    hyperframes_cli.render_segment(plan, output)

    assert captured["timeout"] == hyperframes_cli.DEFAULT_TIMEOUT


def test_render_segment_accepts_str_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """str input_plan and output_path are accepted (Path-coerced internally)."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    captured: dict[str, list[str]] = {}

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        _fake_segment_output(Path(cmd[cmd.index("-o") + 1]))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    # Pass plain strings, not Path objects.
    hyperframes_cli.render_segment(str(plan), str(output))

    assert captured["cmd"][2].endswith("project")
    assert captured["cmd"][4].endswith("segment.mp4")


def test_render_segment_raises_on_zero_byte_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #62 regression: a 0-byte output must be rejected as a render failure."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"")  # simulate zero-byte output
        return subprocess.CompletedProcess(cmd, 0, stderr=b"simulated empty output")

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)

    with pytest.raises(hyperframes_cli.RenderSegmentError, match="empty output"):
        hyperframes_cli.render_segment(plan, output)


def test_render_segment_raises_on_zero_duration_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #62 regression: a non-zero-size but zero-duration file is invalid."""
    plan = _make_project(tmp_path)
    output = tmp_path / "segment.mp4"

    def fake_run(
        cmd: list[str],
        check: bool,
        timeout: int,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"FAKE_MP4")
        return subprocess.CompletedProcess(cmd, 0)

    def fake_probe(_path: Path) -> object:
        class FakeInfo:
            duration = 0.0
            width = 0
            height = 0

        return FakeInfo()

    monkeypatch.setattr("video_editor_core.hyperframes_cli.run_subprocess", fake_run)
    monkeypatch.setattr("video_editor_core.hyperframes_cli.probe_media", fake_probe)

    with pytest.raises(hyperframes_cli.RenderSegmentError, match="zero-duration"):
        hyperframes_cli.render_segment(plan, output)


def test_render_segment_rejects_a_json_plan(tmp_path: Path) -> None:
    """The JSON-plan CLI interface was removed in HyperFrames 0.8.

    ``render --input`` no longer exists, so a plan file must fail loudly
    instead of building a command the CLI rejects.
    """
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")

    with pytest.raises(hyperframes_cli.RenderSegmentError, match="JSON plan"):
        hyperframes_cli.render_segment(plan, tmp_path / "segment.mp4")
