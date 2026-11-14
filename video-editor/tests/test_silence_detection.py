"""Tests for the silence analysis helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from video_editor_engine.analysis.silence import detect_silence


def _make_fake_run(stderr: str, returncode: int = 0) -> Any:
    """Build a fake subprocess.run that returns the given stderr."""

    def fake_run(
        cmd: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout="",
            stderr=stderr,
        )

    return fake_run


def test_detect_silence_keeps_all_unpaired_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C42 regression: intermediate unpaired ``silence_start`` events must not
    be silently dropped. Every unpaired start should become a region of at
    least ``min_duration`` length."""
    from video_editor_engine.analysis import silence as silence_module

    stderr = (
        "[silencedetect] silence_start: 1.2\n"
        "[silencedetect] silence_start: 3.4\n"
        "[silencedetect] silence_end: 4.5\n"
    )
    monkeypatch.setattr(silence_module, "run_ffmpeg", _make_fake_run(stderr))

    audio = tmp_path / "fake.wav"
    audio.write_text("fake")
    output = tmp_path / "out.json"

    data = detect_silence(audio, output, silence_threshold=-35.0, min_duration=0.3)

    regions = [(s["start"], s["end"]) for s in data["silences"]]
    # The first unpaired start (no matching end) becomes a 0.3s region.
    assert (1.2, 1.5) in regions
    # The second unpaired start is paired with the only silence_end.
    assert (3.4, 4.5) in regions


def test_detect_silence_raises_on_ffmpeg_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero ffmpeg exit code must raise a clear error."""
    from video_editor_engine.analysis import silence as silence_module
    from video_editor_engine.analysis.silence import SilenceDetectionError

    monkeypatch.setattr(
        silence_module,
        "run_ffmpeg",
        _make_fake_run("some error", returncode=1),
    )

    audio = tmp_path / "fake.wav"
    audio.write_text("fake")
    output = tmp_path / "out.json"

    with pytest.raises(SilenceDetectionError, match="ffmpeg silencedetect failed"):
        detect_silence(audio, output)
