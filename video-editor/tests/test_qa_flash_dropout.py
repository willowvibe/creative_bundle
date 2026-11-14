"""Tests for flash-frame and audio-dropout detection (QA skill).

Both checks came from the ffmpeg-skill review (``../tool-lab/trials/2026-11-06-video-agent-repos/README.md``):
flash frames catch a broken generation clip, dropouts catch a bad audio join.
The fixtures are synthesised with FFmpeg so the thresholds are exercised
against real decoded pixels and samples, not mocks.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from qa.qa import (
    QASkill,
    detect_audio_dropouts,
    detect_flash_frames,
)
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _build_flash_video(
    path: Path,
    flash_frames: int = 1,
    duration: float = 2.0,
    fps: int = 25,
) -> Path:
    """Build a test pattern with a white flash of ``flash_frames`` at 1.0s."""
    _require_ffmpeg()
    # ``between`` is inclusive on both ends, so subtract a sliver to land on
    # exactly ``flash_frames`` frames.
    end = 1.0 + flash_frames / fps - 0.001
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x180:rate={fps}:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"color=white:size=320x180:rate={fps}:duration={duration}",
        "-filter_complex",
        f"[0:v][1:v]overlay=enable='between(t,1.0,{end})'",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _build_hard_cut_video(path: Path) -> Path:
    """Two different shots joined by a hard cut (must not read as a flash)."""
    _require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x180:rate=25:duration=1",
        "-f",
        "lavfi",
        "-i",
        "color=red:size=320x180:rate=25:duration=1",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _build_gap_audio(path: Path, gap_start: float, gap_length: float, total: float = 2.0) -> Path:
    """A 440 Hz tone with a window of digital silence at ``gap_start``."""
    _require_ffmpeg()
    gap_end = gap_start + gap_length
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={total}",
        "-af",
        f"volume='if(between(t,{gap_start},{gap_end}),0,1)':eval=frame",
        "-c:a",
        "aac",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="qa-flash-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


# ---------------------------------------------------------------------------
# Flash frames
# ---------------------------------------------------------------------------


def test_detect_flash_frames_finds_single_frame_flash(tmp_path: Path) -> None:
    video = _build_flash_video(tmp_path / "flash.mp4", flash_frames=1)

    assert detect_flash_frames(video) == pytest.approx([1.0], abs=0.08)


def test_detect_flash_frames_reports_one_event_for_two_frames(tmp_path: Path) -> None:
    """A two-frame flash is one event, not two."""
    video = _build_flash_video(tmp_path / "flash2.mp4", flash_frames=2)

    events = detect_flash_frames(video)
    assert len(events) == 1
    assert events[0] == pytest.approx(1.0, abs=0.08)


def test_detect_flash_frames_ignores_hard_cut(tmp_path: Path) -> None:
    """A normal cut changes once and stays changed, so it is not a flash."""
    video = _build_hard_cut_video(tmp_path / "cut.mp4")

    assert detect_flash_frames(video) == []


def test_detect_flash_frames_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="ffmpeg signalstats failed"):
        detect_flash_frames(tmp_path / "missing.mp4")


def test_qa_flash_check_is_advisory_by_default(tmp_path: Path, ctx: JobContext) -> None:
    video = _build_flash_video(tmp_path / "flash.mp4")
    report = QASkill(ctx).run(video)

    check = next(c for c in report.checks if c.name == "no_flash_frames")
    assert check.value == 1
    assert check.passed is True  # measured, but not gated
    assert check.threshold == "advisory"


def test_qa_flash_check_fails_when_limit_is_zero(tmp_path: Path, ctx: JobContext) -> None:
    video = _build_flash_video(tmp_path / "flash.mp4")
    report = QASkill(ctx, max_flashes=0).run(video)

    check = next(c for c in report.checks if c.name == "no_flash_frames")
    assert check.passed is False
    assert report.passed is False


# ---------------------------------------------------------------------------
# Audio dropouts
# ---------------------------------------------------------------------------


def test_detect_audio_dropouts_finds_bracketed_gap(tmp_path: Path) -> None:
    audio = _build_gap_audio(tmp_path / "gap.m4a", gap_start=1.0, gap_length=0.05)

    dropouts = detect_audio_dropouts(audio, total_duration=2.0)
    assert len(dropouts) == 1
    start, end = dropouts[0]
    assert start == pytest.approx(1.0, abs=0.05)
    assert end == pytest.approx(1.05, abs=0.05)


def test_detect_audio_dropouts_ignores_leading_silence(tmp_path: Path) -> None:
    """Silence before the first sound is padding, not a dropout."""
    _require_ffmpeg()
    path = tmp_path / "lead.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-af",
            "adelay=400|400",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )

    assert detect_audio_dropouts(path, total_duration=1.4) == []


def test_detect_audio_dropouts_ignores_long_pause(tmp_path: Path) -> None:
    """A pause longer than the dropout window belongs to audio_not_silent."""
    audio = _build_gap_audio(tmp_path / "pause.m4a", gap_start=0.4, gap_length=1.2)

    assert detect_audio_dropouts(audio, total_duration=2.0) == []


def test_qa_dropout_check_fails_when_limit_is_zero(tmp_path: Path, ctx: JobContext) -> None:
    _require_ffmpeg()
    video = tmp_path / "with_gap.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-af",
            "volume='if(between(t,1,1.05),0,1)':eval=frame",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    report = QASkill(ctx, max_audio_dropouts=0).run(video)
    check = next(c for c in report.checks if c.name == "no_audio_dropouts")
    assert check.value == 1
    assert check.passed is False
