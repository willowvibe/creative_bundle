"""Tests for the screen tutorial skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from screen_tutorial.click_detector import detect_clicks
from screen_tutorial.cursor_tracker import CursorFrame
from screen_tutorial.keystroke_overlay import Keystroke, render_keystrokes
from screen_tutorial.screen_tutorial import (
    ScreenTutorialSkill,
    _ease_in_out,
    _smooth_track,
)
from video_editor_state.job import JobContext


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="screen-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def _frames(samples: list[tuple[float, float, float]]) -> list[CursorFrame]:
    return [CursorFrame(time=t, x=x, y=y) for t, x, y in samples]


# ---------------------------------------------------------------------------
# click detection
# ---------------------------------------------------------------------------


def test_detect_clicks_no_audio_accepts_dwell() -> None:
    """With no audio signal, a sustained cursor dwell is a click."""
    frames = _frames([(0.0, 100, 100), (0.1, 100, 100), (0.2, 100, 100), (0.3, 100, 100)])
    clicks = detect_clicks(frames, dwell_threshold_seconds=0.2)
    assert len(clicks) == 1
    assert clicks[0].x == 100
    assert clicks[0].y == 100


def test_detect_clicks_moving_cursor_no_click() -> None:
    """A continuously moving cursor produces no clicks."""
    frames = _frames([(0.0, 0, 0), (0.1, 50, 50), (0.2, 100, 100), (0.3, 150, 150)])
    clicks = detect_clicks(frames, dwell_threshold_seconds=0.2)
    assert clicks == []


def test_detect_clicks_short_dwell_ignored() -> None:
    """A dwell shorter than the threshold is not a click."""
    frames = _frames([(0.0, 100, 100), (0.1, 100, 100), (0.2, 200, 200)])
    clicks = detect_clicks(frames, dwell_threshold_seconds=0.3)
    assert clicks == []


def test_detect_clicks_empty() -> None:
    assert detect_clicks([]) == []


def test_detect_clicks_audio_spike_overlap(tmp_path: Path) -> None:
    """Clicks inside a loud audio window are detected."""
    import subprocess

    wav = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=0.2",
            "-ar",
            "8000",
            "-ac",
            "1",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    frames = _frames([(0.0, 100, 100), (0.1, 100, 100), (0.2, 100, 100)])
    clicks = detect_clicks(frames, audio_path=wav, dwell_threshold_seconds=0.1)
    assert clicks


# ---------------------------------------------------------------------------
# smoothing / easing helpers
# ---------------------------------------------------------------------------


def test_ease_in_out_bounds() -> None:
    assert _ease_in_out(0.0) == 0.0
    assert _ease_in_out(1.0) == 1.0
    assert 0.0 < _ease_in_out(0.5) < 1.0


def test_smooth_track_reduces_jitter() -> None:
    frames = _frames([(0.0, 0, 0), (0.1, 100, 0), (0.2, 0, 0), (0.3, 100, 0)])
    smoothed = _smooth_track(frames, window=3)
    assert len(smoothed) == len(frames)
    assert smoothed[1].x < 100  # averaged down


def test_smooth_track_empty() -> None:
    assert _smooth_track([]) == []


# ---------------------------------------------------------------------------
# keystroke overlay
# ---------------------------------------------------------------------------


def test_render_keystrokes_empty_is_passthrough(
    tmp_path: Path,
) -> None:
    import subprocess

    source = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = render_keystrokes(source, out, [])
    assert result == out
    assert out.exists()


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
def test_render_keystrokes_burns_overlays(tmp_path: Path) -> None:
    import subprocess

    source = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    result = render_keystrokes(source, out, [Keystroke(text="Ctrl+S", start=0.0, end=0.8)])
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# skill end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
def test_screen_tutorial_skill_name(ctx: JobContext) -> None:
    skill = ScreenTutorialSkill(ctx)
    assert skill.name == "screen_tutorial"


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
def test_screen_tutorial_skill_end_to_end(ctx: JobContext, tmp_path: Path) -> None:
    """The skill produces a polished video and metadata JSON."""
    import subprocess

    source = tmp_path / "screen.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=640x360:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    skill = ScreenTutorialSkill(
        ctx,
        enable_zoom=False,  # avoid zoompan on synthetic content
        enable_clicks=True,
        enable_keystrokes=True,
        output_width=640,
        output_height=360,
    )
    result = skill.run(
        source,
        keystrokes=[Keystroke(text="Enter", start=0.0, end=0.8)],
    )
    output = Path(result["output"])
    assert output.exists()
    metadata = json.loads(Path(result["metadata"]).read_text())
    assert metadata["output"] == str(output)
    assert "cursor" in metadata
    assert "clicks" in metadata
    assert metadata["keystrokes"][0]["text"] == "Enter"


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
def test_screen_tutorial_skill_disable_all(ctx: JobContext, tmp_path: Path) -> None:
    """All effects disabled still produces an output."""
    import subprocess

    source = tmp_path / "screen.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    skill = ScreenTutorialSkill(
        ctx,
        enable_zoom=False,
        enable_clicks=False,
        enable_keystrokes=False,
    )
    result = skill.run(source)
    assert Path(result["output"]).exists()
    assert result["count"] == 0


def test_screen_tutorial_missing_input_raises(ctx: JobContext, tmp_path: Path) -> None:
    skill = ScreenTutorialSkill(ctx)
    with pytest.raises(FileNotFoundError):
        skill.run(tmp_path / "missing.mp4")
