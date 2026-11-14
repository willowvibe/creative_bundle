"""Tests for the QA skill.

The QA skill runs eight checks on a rendered video (file presence,
duration, dimensions, audio, aspect ratio, fps, silence, black
frames). Previously uncovered (Issue #19). These tests cover the
core flow against a synthetic video built with ``ffmpeg lavfi``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from qa.qa import QASkill
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _build_video(
    path: Path,
    duration: float = 2.0,
    width: int = 320,
    height: int = 240,
    audio: bool = True,
    fps: int = 30,
) -> Path:
    """Synthesize a small H.264 video with or without audio."""
    _require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="qa-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def test_qa_run_passes_on_healthy_video(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A clean test pattern with audio passes all checks.

    The synthetic 440Hz sine is measured at ~-22 LUFS — pass a matching
    loudness target so the new audio_loudness check stays green.
    """
    video = _build_video(tmp_path / "healthy.mp4", duration=1.0, audio=True)
    skill = QASkill(
        ctx,
        expected_aspect_ratio="4:3",  # 320x240 = 4:3
        loudness_target_lufs=-22.0,
    )

    report = skill.run(video)

    assert report.passed is True
    assert report.video_path == str(video)
    assert len(report.checks) == 17
    failed = [c.name for c in report.checks if not c.passed]
    assert failed == [], f"unexpected failures: {failed}"

    # Report was written to the artifact path.
    report_path = ctx.artifact("qa", "qa_report.json")
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["passed"] is True
    # 17 checks: the original 8, plus audio_loudness, no_frozen_frames,
    # no_flash_frames, no_audio_dropouts, no_stub_assets, cost_tracking and
    # the three film-gate checks (film_motion, film_empty_frames,
    # film_blue_purple).
    assert len(data["checks"]) == 17


def test_qa_fails_when_a_cached_asset_came_from_a_stub(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """#281: QA must not pass a render built from placeholder providers."""
    video = _build_video(tmp_path / "stubbed.mp4", duration=1.0, audio=True)
    cache = ctx.work_dir / ".cache" / "assets"
    cache.mkdir(parents=True)
    (cache / "still.meta.json").write_text(
        json.dumps({"asset_id": "still_rat", "metadata": {"provider": "stub_image"}}),
        encoding="utf-8",
    )

    report = QASkill(ctx, expected_aspect_ratio="4:3", loudness_target_lufs=-22.0).run(video)

    assert report.passed is False
    assert "no_stub_assets" in {check.name for check in report.checks if not check.passed}
    assert any("still_rat" in warning for warning in report.warnings)


def test_qa_run_flags_wrong_aspect_ratio(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A 320x240 video is 4:3, not 16:9 — the aspect check should fail."""
    video = _build_video(tmp_path / "wide_expect.mp4", width=320, height=240)
    skill = QASkill(ctx, expected_aspect_ratio="16:9")

    report = skill.run(video)

    aspect_check = next(c for c in report.checks if c.name == "aspect_ratio")
    assert aspect_check.passed is False
    assert aspect_check.threshold == "16:9"
    assert report.passed is False  # overall fails because of aspect


def test_qa_run_flags_missing_audio(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A video with no audio stream fails the has_audio check."""
    video = _build_video(tmp_path / "silent.mp4", audio=False)
    skill = QASkill(ctx, expected_aspect_ratio="4:3")

    report = skill.run(video)

    audio_check = next(c for c in report.checks if c.name == "has_audio")
    assert audio_check.passed is False
    assert report.passed is False


def test_qa_run_raises_on_missing_video(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """Pointing QA at a non-existent file raises FileNotFoundError."""
    skill = QASkill(ctx)
    missing = tmp_path / "does_not_exist.mp4"

    with pytest.raises(FileNotFoundError, match="QA target"):
        skill.run(missing)


def test_qa_default_video_path_falls_back_to_captions(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """With no --video-path, QA looks in captions/with_captions.mp4
    (regression guard for Bug #2 — the v1 default was a dead
    path that no skill produced)."""
    captions_dir = ctx.work_dir / "captions"
    captions_dir.mkdir(parents=True)
    _build_video(captions_dir / "with_captions.mp4", duration=0.5)

    skill = QASkill(
        ctx,
        expected_aspect_ratio="4:3",
        loudness_target_lufs=-22.0,
    )
    report = skill.run()  # no video_path argument

    assert report.video_path.endswith("with_captions.mp4")
    assert report.passed is True


def test_qa_audio_loudness_present_when_audio_present(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A video with audio produces an ``audio_loudness`` check."""
    video = _build_video(tmp_path / "x.mp4", duration=1.0, audio=True)
    skill = QASkill(ctx)
    report = skill.run(video)
    names = {c.name for c in report.checks}
    assert "audio_loudness" in names


def test_qa_audio_loudness_skipped_when_silent(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """A silent (no-audio) video skips the audio_loudness check entirely.

    ebur128 cannot measure loudness on a file with no audio stream."""
    video = _build_video(tmp_path / "x.mp4", duration=1.0, audio=False)
    skill = QASkill(ctx)
    report = skill.run(video)
    names = {c.name for c in report.checks}
    assert "audio_loudness" not in names


def test_qa_brand_kwarg_sets_loudness_target(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """QASkill(brand=...) reads the loudness target from
    brand.defaults['final_loudness']."""
    from graphics.models import Brand

    video = _build_video(tmp_path / "x.mp4", duration=1.0, audio=True)
    brand = Brand(
        brand="custom",
        colors={"text": "#fff"},
        fonts={"captions": "X"},
        logo={"path": "", "width": 0, "height": 0},
        defaults={"final_loudness": -14.0, "music_duck_db": -14, "caption_animation": "pop_in"},
    )
    skill = QASkill(ctx, brand=brand)
    assert skill.loudness_target_lufs == -14.0
    report = skill.run(video)
    loudness_check = next(c for c in report.checks if c.name == "audio_loudness")
    # The threshold string includes the brand-derived target.
    assert "-14" in str(loudness_check.threshold)


def test_qa_explicit_loudness_target_wins_over_brand(
    tmp_path: Path,
    ctx: JobContext,
) -> None:
    """An explicit ``loudness_target_lufs=`` argument overrides the brand."""
    from graphics.models import Brand

    video = _build_video(tmp_path / "x.mp4", duration=1.0, audio=True)
    brand = Brand(
        brand="custom",
        colors={"text": "#fff"},
        fonts={"captions": "X"},
        logo={"path": "", "width": 0, "height": 0},
        defaults={"final_loudness": -14.0, "music_duck_db": -14, "caption_animation": "pop_in"},
    )
    skill = QASkill(ctx, brand=brand, loudness_target_lufs=-23.0)
    assert skill.loudness_target_lufs == -23.0
    report = skill.run(video)
    loudness_check = next(c for c in report.checks if c.name == "audio_loudness")
    assert "-23" in str(loudness_check.threshold)


def test_parse_ffmpeg_regions_handles_trailing_silence() -> None:
    """Bug #55 regression: trailing silence_start with no matching
    silence_end must not crash the strict zip."""
    from qa.qa import _parse_ffmpeg_regions

    stderr = "silence_start: 1.200\nsilence_end: 2.100\nsilence_start: 4.500\n"
    regions = _parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:")
    assert regions == [(1.2, 2.1), (4.5, 4.6)]


def _build_silent_video(path: Path, duration: float = 1.5) -> Path:
    """Synthesize a video whose audio track is digital silence."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(duration),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _build_black_video(path: Path, duration: float = 0.6) -> Path:
    """Synthesize a video whose visual track is solid black."""
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
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_qa_flags_unexpected_silence_by_default(tmp_path: Path, ctx: JobContext) -> None:
    """A video that is mostly silence fails ``audio_not_silent`` by default."""
    video = _build_silent_video(tmp_path / "silent.mp4", duration=1.5)
    skill = QASkill(ctx, expected_aspect_ratio="4:3")
    report = skill.run(video)
    silence_check = next(c for c in report.checks if c.name == "audio_not_silent")
    assert silence_check.passed is False


def test_qa_permits_deliberate_silence_with_custom_threshold(
    tmp_path: Path, ctx: JobContext
) -> None:
    """Bug #98: deliberate quiet/fade intros can pass with a raised threshold."""
    video = _build_silent_video(tmp_path / "silent.mp4", duration=1.5)
    skill = QASkill(
        ctx,
        expected_aspect_ratio="4:3",
        silence_max_duration_seconds=2.0,
    )
    report = skill.run(video)
    silence_check = next(c for c in report.checks if c.name == "audio_not_silent")
    assert silence_check.passed is True
    assert silence_check.value == pytest.approx(1.5, abs=0.2)


def test_qa_flags_unexpected_black_frames_by_default(tmp_path: Path, ctx: JobContext) -> None:
    """A black-screen segment fails ``no_black_frames`` by default."""
    video = _build_black_video(tmp_path / "black.mp4", duration=0.6)
    skill = QASkill(ctx, expected_aspect_ratio="4:3", loudness_target_lufs=-22.0)
    report = skill.run(video)
    black_check = next(c for c in report.checks if c.name == "no_black_frames")
    assert black_check.passed is False


def test_qa_permits_deliberate_black_frames_with_custom_threshold(
    tmp_path: Path, ctx: JobContext
) -> None:
    """Bug #98: fade-to-black can pass when the max black duration is raised."""
    video = _build_black_video(tmp_path / "black.mp4", duration=0.6)
    skill = QASkill(
        ctx,
        expected_aspect_ratio="4:3",
        loudness_target_lufs=-22.0,
        black_max_duration_seconds=1.0,
    )
    report = skill.run(video)
    black_check = next(c for c in report.checks if c.name == "no_black_frames")
    assert black_check.passed is True
    assert black_check.value == pytest.approx(0.6, abs=0.1)


def test_detect_silence_regions_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    """C39 regression: silencedetect must not silently pass when ffmpeg fails."""
    from qa.qa import detect_silence_regions

    missing = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError, match="ffmpeg silencedetect failed"):
        detect_silence_regions(missing)


def test_detect_black_regions_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    """C39 regression: blackdetect must not silently pass when ffmpeg fails."""
    from qa.qa import detect_black_regions

    missing = tmp_path / "missing.mp4"
    with pytest.raises(RuntimeError, match="ffmpeg blackdetect failed"):
        detect_black_regions(missing)
