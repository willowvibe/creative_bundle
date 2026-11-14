"""Tests for the reframe skill."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from reframe.reframe import ReframeSkill
from reframe.tracker import FaceTracker, FocalPoint
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_16x9_video(tmp_path: Path) -> Path:
    """Create a short 16:9 test video."""
    _require_ffmpeg()
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=1920x1080:d=1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return video


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(job_id="rf-test", work_root=str(tmp_path), output_root=str(tmp_path))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_reframe_9x16_output(ctx: JobContext, tmp_path: Path) -> None:
    """Reframing a 16:9 video to 9:16 should produce a vertical MP4."""
    _require_ffmpeg()
    video = _make_16x9_video(tmp_path)

    skill = ReframeSkill(ctx, enable_zoom=False)
    result = skill.run(video, output_name="reframed.mp4")

    assert result.exists()
    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(result)
    assert info.width == 1080
    assert info.height == 1920
    assert (ctx.work_dir / "reframe" / "keyframes.json").exists()
    assert (ctx.work_dir / "reframe" / "reframe.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_reframe_with_zoom(ctx: JobContext, tmp_path: Path) -> None:
    """Zoom-enabled reframe should also produce a valid vertical MP4."""
    _require_ffmpeg()
    video = _make_16x9_video(tmp_path)

    skill = ReframeSkill(ctx, enable_zoom=True, zoom_factor=1.05)
    result = skill.run(video, output_name="reframed_zoom.mp4")

    assert result.exists()
    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(result)
    assert info.width == 1080
    assert info.height == 1920


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_reframe_manifest_records_energy(ctx: JobContext, tmp_path: Path) -> None:
    """Transcript energy should be recorded in the manifest."""
    _require_ffmpeg()
    video = _make_16x9_video(tmp_path)

    skill = ReframeSkill(ctx, enable_zoom=False)
    skill.run(video, transcript_energy=[0.2, 0.8, 0.6])

    import json

    manifest = json.loads((ctx.work_dir / "reframe" / "reframe.json").read_text())
    assert manifest["mean_energy"] == pytest.approx(0.533, abs=0.01)


def test_reframe_missing_input(ctx: JobContext, tmp_path: Path) -> None:
    """A missing input file should raise FileNotFoundError."""
    skill = ReframeSkill(ctx)
    with pytest.raises(FileNotFoundError):
        skill.run(tmp_path / "missing.mp4")


def test_smooth_points_reduces_jitter() -> None:
    """Smoothing should dampen large focal jumps."""
    from reframe.reframe import ReframeSkill

    raw = [
        FocalPoint(0.5, 0.5),
        FocalPoint(0.9, 0.9),
        FocalPoint(0.5, 0.5),
    ]
    smoothed = ReframeSkill.smooth_points(raw, smoothing=0.6)
    assert smoothed[1].x > 0.5
    assert smoothed[1].x < 0.9


def test_tracker_fallback_returns_points(tmp_path: Path) -> None:
    """Without MediaPipe the tracker should still return focal points."""
    _require_ffmpeg()
    video = _make_16x9_video(tmp_path)
    tracker = FaceTracker()
    tracker._mediapipe = None
    tracker._face_detection = None
    points = tracker.track(video)
    assert points
    assert all(0.0 <= p.x <= 1.0 and 0.0 <= p.y <= 1.0 for p in points)
