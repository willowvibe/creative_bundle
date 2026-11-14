"""Tests for the segment transition library and transition skill."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from transitions.transitions import TransitionSkill
from video_editor_core.timeline import EDL, Segment
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.transitions import concat_with_transitions
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_segment(tmp_path: Path, color: str, duration: float) -> Path:
    """Create a short test segment with a solid color and tone."""
    _require_ffmpeg()
    path = tmp_path / f"{color}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(job_id="tr-test", work_root=str(tmp_path), output_root=str(tmp_path))


@pytest.mark.parametrize(
    ("transition", "duration"),
    [
        ("none", 0.0),
        ("fade", 0.3),
        ("slideleft", 0.2),
        ("wipeleft", 0.25),
    ],
)
def test_concat_with_transitions_produces_valid_video(
    tmp_path: Path,
    transition: str,
    duration: float,
) -> None:
    """Each transition (and the none fallback) writes a concat output."""
    seg1 = _make_segment(tmp_path, "red", 1.0)
    seg2 = _make_segment(tmp_path, "blue", 1.0)

    output = tmp_path / "out.mp4"
    result = concat_with_transitions(
        [seg1, seg2],
        output,
        transition=transition,  # type: ignore[arg-type]
        transition_duration=duration,
        render_options=RenderOptions(fps=30),
    )

    assert result == output
    assert output.exists()
    info = probe_media(output)
    assert info.duration is not None
    expected = 2.0 - duration
    assert abs(info.duration - expected) < 0.2


def test_concat_with_transitions_falls_back_when_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``xfade`` is unavailable the helper falls back to plain concat."""
    seg1 = _make_segment(tmp_path, "red", 1.0)
    seg2 = _make_segment(tmp_path, "blue", 1.0)
    monkeypatch.setattr("video_editor_ffmpeg.transitions._xfade_available", lambda: False)

    output = tmp_path / "out.mp4"
    concat_with_transitions(
        [seg1, seg2],
        output,
        transition="fade",
        transition_duration=0.3,
    )

    info = probe_media(output)
    assert info.duration is not None
    assert abs(info.duration - 2.0) < 0.1


def test_concat_with_transitions_single_segment_is_copy(
    tmp_path: Path,
) -> None:
    """A single segment short-circuits to the concat demuxer."""
    seg = _make_segment(tmp_path, "green", 1.5)
    output = tmp_path / "out.mp4"
    concat_with_transitions(
        [seg],
        output,
        transition="fade",
        transition_duration=0.3,
    )
    info = probe_media(output)
    assert info.duration is not None
    assert abs(info.duration - 1.5) < 0.1


def test_concat_with_transitions_accepts_per_pair_list(tmp_path: Path) -> None:
    """The helper should accept a list of transitions, one per boundary."""
    seg1 = _make_segment(tmp_path, "red", 1.0)
    seg2 = _make_segment(tmp_path, "blue", 1.0)
    seg3 = _make_segment(tmp_path, "green", 1.0)
    output = tmp_path / "out.mp4"

    concat_with_transitions(
        [seg1, seg2, seg3],
        output,
        transition=["fade", "slideleft"],
        transition_duration=0.2,
    )

    info = probe_media(output)
    assert info.duration is not None
    # 3.0 - 2 * 0.2 = 2.6
    assert abs(info.duration - 2.6) < 0.2


def test_concat_with_transitions_splits_on_none(tmp_path: Path) -> None:
    """A 'none' boundary should split the render into concat groups."""
    seg1 = _make_segment(tmp_path, "red", 1.0)
    seg2 = _make_segment(tmp_path, "blue", 1.0)
    seg3 = _make_segment(tmp_path, "green", 1.0)
    output = tmp_path / "out.mp4"

    concat_with_transitions(
        [seg1, seg2, seg3],
        output,
        transition=["fade", "none"],
        transition_duration=0.2,
    )

    info = probe_media(output)
    assert info.duration is not None
    # seg1+seg2 with 0.2 overlap + seg3 plain = 2.0 - 0.2 + 1.0 = 2.8
    assert abs(info.duration - 2.8) < 0.2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_name(ctx: JobContext) -> None:
    """The skill should report the correct protocol name."""
    skill = TransitionSkill(ctx)
    assert skill.name == "transitions"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_explicit_fade(ctx: JobContext, tmp_path: Path) -> None:
    """An explicit fade transition should render a combined output."""
    seg1 = _make_segment(tmp_path, "red", duration=1.5)
    seg2 = _make_segment(tmp_path, "blue", duration=1.5)

    skill = TransitionSkill(ctx, default="fade", duration=0.3)
    result = skill.run([seg1, seg2], transitions=["fade"])

    output = Path(result["output"])
    assert output.exists()
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration < 3.0
    assert info.duration > 2.5

    plan = json.loads(Path(result["plan"]).read_text())
    assert plan["boundaries"][0]["transition"] == "fade"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_auto_cut_for_speech(ctx: JobContext, tmp_path: Path) -> None:
    """Heuristic selector should pick a hard cut for speech-to-speech."""
    seg1 = _make_segment(tmp_path, "red", duration=1.5)
    seg2 = _make_segment(tmp_path, "blue", duration=1.5)

    edl = EDL(
        job_id=ctx.job_id,
        source_path=str(seg1),
        segments=[
            Segment(
                id="seg_0",
                source_start=0.0,
                source_end=1.5,
                timeline_start=0.0,
                timeline_end=1.5,
                label="speech",
            ),
            Segment(
                id="seg_1",
                source_start=0.0,
                source_end=1.5,
                timeline_start=1.5,
                timeline_end=3.0,
                label="talking-head",
            ),
        ],
    )

    skill = TransitionSkill(ctx, default="auto")
    selected = skill.select_transitions([seg1, seg2], edl=edl)
    assert selected == ["none"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_auto_fade_for_broll(ctx: JobContext, tmp_path: Path) -> None:
    """Heuristic selector should pick a fade for B-roll segments."""
    seg1 = _make_segment(tmp_path, "green", duration=1.5)
    seg2 = _make_segment(tmp_path, "blue", duration=1.5)

    edl = EDL(
        job_id=ctx.job_id,
        source_path=str(seg1),
        segments=[
            Segment(
                id="seg_0",
                source_start=0.0,
                source_end=1.5,
                timeline_start=0.0,
                timeline_end=1.5,
                label="b-roll calm scenery",
            ),
            Segment(
                id="seg_1",
                source_start=0.0,
                source_end=1.5,
                timeline_start=1.5,
                timeline_end=3.0,
                label="intro music",
            ),
        ],
    )

    skill = TransitionSkill(ctx, default="auto")
    selected = skill.select_transitions([seg1, seg2], edl=edl)
    assert selected == ["fade"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_auto_motion_for_action(ctx: JobContext, tmp_path: Path) -> None:
    """Heuristic selector should pick a motion transition for action labels."""
    seg1 = _make_segment(tmp_path, "red", duration=1.5)
    seg2 = _make_segment(tmp_path, "yellow", duration=1.5)

    edl = EDL(
        job_id=ctx.job_id,
        source_path=str(seg1),
        segments=[
            Segment(
                id="seg_0",
                source_start=0.0,
                source_end=1.5,
                timeline_start=0.0,
                timeline_end=1.5,
                label="montage fast beat",
            ),
            Segment(
                id="seg_1",
                source_start=0.0,
                source_end=1.5,
                timeline_start=1.5,
                timeline_end=3.0,
                label="sports action",
            ),
        ],
    )

    skill = TransitionSkill(ctx, default="auto")
    selected = skill.select_transitions([seg1, seg2], edl=edl)
    assert selected == ["slideleft"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_overrides_validation(ctx: JobContext, tmp_path: Path) -> None:
    """Explicit transition list must match segment count - 1."""
    seg1 = _make_segment(tmp_path, "red", duration=1.5)
    seg2 = _make_segment(tmp_path, "blue", duration=1.5)
    seg3 = _make_segment(tmp_path, "green", duration=1.5)

    skill = TransitionSkill(ctx)
    with pytest.raises(ValueError):
        skill.select_transitions([seg1, seg2, seg3], overrides=["fade"])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_mixed_with_none(ctx: JobContext, tmp_path: Path) -> None:
    """A 'none' boundary should split the render into two concat groups."""
    seg1 = _make_segment(tmp_path, "red", duration=1.5)
    seg2 = _make_segment(tmp_path, "blue", duration=1.5)
    seg3 = _make_segment(tmp_path, "green", duration=1.5)

    skill = TransitionSkill(ctx, duration=0.3)
    result = skill.run(
        [seg1, seg2, seg3],
        transitions=["fade", "none"],
    )

    output = Path(result["output"])
    assert output.exists()
    plan = json.loads(Path(result["plan"]).read_text())
    assert plan["boundaries"][0]["transition"] == "fade"
    assert plan["boundaries"][1]["transition"] == "none"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_transition_skill_default_fixed(ctx: JobContext, tmp_path: Path) -> None:
    """A non-auto default should be applied to every boundary."""
    seg1 = _make_segment(tmp_path, "red", duration=1.2)
    seg2 = _make_segment(tmp_path, "blue", duration=1.2)
    seg3 = _make_segment(tmp_path, "green", duration=1.2)

    skill = TransitionSkill(ctx, default="wipeleft", duration=0.2)
    selected = skill.select_transitions([seg1, seg2, seg3])
    assert selected == ["wipeleft", "wipeleft"]

    result = skill.run([seg1, seg2, seg3])
    plan = json.loads(Path(result["plan"]).read_text())
    assert all(b["transition"] == "wipeleft" for b in plan["boundaries"])


def test_transition_skill_single_segment_raises(ctx: JobContext, tmp_path: Path) -> None:
    """A single segment cannot have a transition."""
    skill = TransitionSkill(ctx)
    with pytest.raises(ValueError):
        skill.run([tmp_path / "only.mp4"])


def _make_silent_segment(tmp_path: Path, color: str, duration: float) -> Path:
    """Create a short video-only segment (no audio stream)."""
    _require_ffmpeg()
    path = tmp_path / f"{color}_silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_concat_with_transitions_video_only_segments(tmp_path: Path) -> None:
    """Silent segments must crossfade without requiring an audio stream."""
    seg1 = _make_silent_segment(tmp_path, "red", 1.0)
    seg2 = _make_silent_segment(tmp_path, "blue", 1.0)
    output = tmp_path / "out.mp4"

    concat_with_transitions(
        [seg1, seg2],
        output,
        transition="fade",
        transition_duration=0.3,
        render_options=RenderOptions(fps=30),
    )

    info = probe_media(output)
    assert not info.has_audio
    assert info.duration is not None
    assert abs(info.duration - 1.7) < 0.2


def test_concat_with_transitions_mixed_audio_segments(tmp_path: Path) -> None:
    """A silent segment next to one with audio is padded with silence."""
    seg1 = _make_silent_segment(tmp_path, "red", 1.0)
    seg2 = _make_segment(tmp_path, "blue", 1.0)
    output = tmp_path / "out.mp4"

    concat_with_transitions(
        [seg1, seg2],
        output,
        transition="fade",
        transition_duration=0.3,
        render_options=RenderOptions(fps=30),
    )

    info = probe_media(output)
    assert info.has_audio
    assert info.duration is not None
    assert abs(info.duration - 1.7) < 0.2
