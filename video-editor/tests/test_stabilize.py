"""Tests for the vidstab stabilisation wrapper.

The fixture is a fixed crop jittering inside a larger frame, which is a real
camera shake in every way vidstab cares about (translation only).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import FFmpegError
from video_editor_ffmpeg.stabilize import stabilize_video, vidstab_available

from tests.conftest import _require_ffmpeg


def _build_shaky_video(path: Path, duration: float = 1.0) -> Path:
    _require_ffmpeg()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=400x300:rate=25:duration={duration}",
            "-vf",
            "crop=320:240:x='40+8*sin(2*PI*3*t)':y='30+6*sin(2*PI*2.5*t)'",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_vidstab_is_available_in_this_build() -> None:
    _require_ffmpeg()
    if not vidstab_available():
        pytest.skip("FFmpeg built without libvidstab")
    assert vidstab_available() is True


def test_stabilize_writes_a_playable_copy(tmp_path: Path) -> None:
    _require_ffmpeg()
    if not vidstab_available():
        pytest.skip("FFmpeg built without libvidstab")
    source = _build_shaky_video(tmp_path / "shaky.mp4")
    output = tmp_path / "stable.mp4"

    written = stabilize_video(source, output)

    assert written == output
    assert output.exists() and output.stat().st_size > 0
    info = probe_media(output)
    assert info.duration == pytest.approx(1.0, abs=0.1)
    assert (info.width, info.height) == (320, 240)


def test_trf_file_is_temporary_by_default(tmp_path: Path) -> None:
    _require_ffmpeg()
    if not vidstab_available():
        pytest.skip("FFmpeg built without libvidstab")
    source = _build_shaky_video(tmp_path / "shaky.mp4")
    output = tmp_path / "stable.mp4"

    stabilize_video(source, output)

    assert list(tmp_path.glob("*.trf")) == []


def test_trf_file_is_kept_when_requested(tmp_path: Path) -> None:
    _require_ffmpeg()
    if not vidstab_available():
        pytest.skip("FFmpeg built without libvidstab")
    source = _build_shaky_video(tmp_path / "shaky.mp4")
    output = tmp_path / "stable.mp4"
    trf = tmp_path / "motion.trf"

    stabilize_video(source, output, trf_path=trf)

    assert trf.exists() and trf.stat().st_size > 0


def test_stabilize_raises_on_missing_input(tmp_path: Path) -> None:
    _require_ffmpeg()
    if not vidstab_available():
        pytest.skip("FFmpeg built without libvidstab")

    with pytest.raises(FFmpegError):
        stabilize_video(tmp_path / "missing.mp4", tmp_path / "out.mp4")
