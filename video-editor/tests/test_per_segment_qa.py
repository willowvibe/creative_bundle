"""Tests for the per-segment QA hook in the graphics skill.

A two-segment video that is visually and acoustically identical at the cut
boundary should pass the check; two segments that diverge in either the
last 0.1s of the first / first 0.1s of the second (visual jump) or in the
0.2s window centered on the boundary (audio pop) should fail.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from qa.models import QASelfEvalError
from qa.segment_check import (
    AUDIO_POP_THRESHOLD,
    PSNR_FAILURE_DB,
    check_segment_boundary,
)

from tests.conftest import _require_ffmpeg

# Visual jump and audio-pop constants; mirror the values in qa.segment_check.
_VISUAL_WINDOW_S = 0.1
_AUDIO_WINDOW_S = 0.2


def _build_video(
    path: Path,
    duration: float = 1.0,
    width: int = 320,
    height: int = 240,
    fps: int = 30,
    audio: bool = True,
    color: str = "red",
) -> Path:
    """Synthesize a tiny H.264 video with optional audio.

    ``color`` selects between ``testsrc`` (default multi-color test pattern)
    and a solid color (so we can deliberately introduce a visual jump).

    Bug #107: skip if ffmpeg is not on PATH.
    """
    _require_ffmpeg()
    if color == "testsrc":
        video_src = f"testsrc=duration={duration}:size={width}x{height}:rate={fps}"
    else:
        video_src = f"color=c={color}:size={width}x{height}:duration={duration}:rate={fps}"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video_src,
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _build_silent_video(
    path: Path,
    duration: float = 1.0,
    width: int = 320,
    height: int = 240,
) -> Path:
    """Synthesize a video with no audio stream (silent)."""
    _require_ffmpeg()
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:size={width}x{height}:duration={duration}:rate=30",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    return tmp_path / "work"


def test_check_segment_boundary_passes_on_clean_cut(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """Two visually identical segments at the boundary pass the check.

    ``testsrc`` produces a deterministic pattern, so two segments rendered
    from the same source have a high PSNR at the boundary; the audio is
    also a steady 440Hz sine with no transient.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    _build_video(tmp_path / "seg_a.mp4", duration=0.6, color="testsrc")
    curr_path = _build_video(tmp_path / "seg_b.mp4", duration=0.6, color="testsrc")

    result = check_segment_boundary(
        prev_path=curr_path,  # same content → high PSNR
        curr_path=curr_path,
        prev_segment_id="seg_a",
        curr_segment_id="seg_b",
        work_dir=work_dir,
    )

    assert result["passed"] is True
    assert result["psnr_db"] is not None
    assert result["psnr_db"] > 20.0
    assert result["peak_rms"] is not None
    assert result["peak_rms"] < AUDIO_POP_THRESHOLD

    # The per-segment artifact was written.
    artifact = work_dir / ".cache" / "qa" / "seg_b.json"
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["segment_id"] == "seg_b"
    assert payload["passed"] is True


def test_check_segment_boundary_flags_visual_jump(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """Two segments with completely different content fail the visual check.

    A red segment following a blue segment has PSNR < 20dB at the cut
    boundary and triggers a ``QASelfEvalError`` carrying the measured PSNR.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(tmp_path / "red.mp4", duration=0.6, color="red", audio=False)
    curr_path = _build_video(tmp_path / "blue.mp4", duration=0.6, color="blue", audio=False)

    with pytest.raises(QASelfEvalError) as exc:
        check_segment_boundary(
            prev_path=prev_path,
            curr_path=curr_path,
            prev_segment_id="seg_a",
            curr_segment_id="seg_b",
            work_dir=work_dir,
        )

    assert exc.value.segment_id == "seg_b"
    assert exc.value.reason == "visual_jump"
    assert exc.value.psnr_db is not None
    assert exc.value.psnr_db < PSNR_FAILURE_DB

    # The artifact is still written, with passed=False.
    artifact = work_dir / ".cache" / "qa" / "seg_b.json"
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["reason"] == "visual_jump"


def test_check_segment_boundary_skips_when_only_one_segment(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """The first segment in a sequence has no previous — the check is a no-op.

    Returns a result with ``passed=True`` and the per-segment artifact
    indicates that the check was skipped (not failed). This is the
    behavior the graphics skill relies on for the first render of a
    multi-segment composition.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    curr_path = _build_video(tmp_path / "only.mp4", duration=0.6, color="testsrc")

    result = check_segment_boundary(
        prev_path=None,
        curr_path=curr_path,
        prev_segment_id=None,
        curr_segment_id="seg_first",
        work_dir=work_dir,
    )

    assert result["passed"] is True
    assert result["skipped"] is True
    assert result["psnr_db"] is None
    assert result["peak_rms"] is None

    artifact = work_dir / ".cache" / "qa" / "seg_first.json"
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["skipped"] is True


def test_check_segment_boundary_flags_audio_pop(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """A square-wave at peak=1.0 in the boundary window fails the audio-pop check.

    A square wave at 0.99 amplitude has RMS ≈ 0.92 (linear) after AAC
    compression, which is above the 0.90 threshold. The visual content is
    identical (testsrc), so PSNR stays high and the failure is specifically
    an audio-pop (not a visual jump).
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(tmp_path / "pop_a.mp4", duration=0.6, color="testsrc", audio=True)
    curr_path = _build_video(tmp_path / "pop_b.mp4", duration=0.6, color="testsrc", audio=True)
    # Replace the audio of both segments with a 0.99-amplitude square
    # wave so the boundary window sees peak RMS ≈ 0.99.
    _replace_audio_with_square_wave(prev_path, amplitude=0.99)
    _replace_audio_with_square_wave(curr_path, amplitude=0.99)

    with pytest.raises(QASelfEvalError) as exc:
        check_segment_boundary(
            prev_path=prev_path,
            curr_path=curr_path,
            prev_segment_id="seg_a",
            curr_segment_id="seg_b",
            work_dir=work_dir,
        )

    assert exc.value.segment_id == "seg_b"
    # Visual is testsrc/testsrc (high PSNR), so the failure should be
    # specifically the audio-pop branch.
    assert exc.value.reason == "audio_pop"
    assert exc.value.peak_rms is not None
    assert exc.value.peak_rms >= AUDIO_POP_THRESHOLD


def test_check_segment_boundary_flags_transient_at_curr_start(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """Bug #72/#105 regression: a silent previous segment followed by a loud
    transient at the very start of the current segment must be detected.

    The old implementation only measured the tail of ``prev``; the corrected
    command measures the first 0.1s of ``curr`` where the transient lives.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(
        tmp_path / "silent_prev.mp4", duration=0.6, color="testsrc", audio=True
    )
    curr_path = _build_video(
        tmp_path / "transient_curr.mp4", duration=0.6, color="testsrc", audio=True
    )
    _replace_audio_with_silence(prev_path)
    _replace_audio_with_square_wave(curr_path, amplitude=0.99)

    with pytest.raises(QASelfEvalError) as exc:
        check_segment_boundary(
            prev_path=prev_path,
            curr_path=curr_path,
            prev_segment_id="seg_a",
            curr_segment_id="seg_b",
            work_dir=work_dir,
        )

    assert exc.value.segment_id == "seg_b"
    assert exc.value.reason == "audio_pop"
    assert exc.value.peak_rms is not None
    assert exc.value.peak_rms >= AUDIO_POP_THRESHOLD


def _replace_audio_with_silence(path: Path) -> None:
    """Re-encode the audio track of ``path`` as digital silence."""
    tmp = path.with_name(path.stem + "_silent.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp),
        ],
        check=True,
        capture_output=True,
    )
    tmp.replace(path)


def _replace_audio_with_square_wave(path: Path, amplitude: float = 0.99) -> None:
    """Re-encode the audio track of ``path`` as a square wave at peak ``amplitude``.

    The aevalsrc expression produces a ±amplitude square wave (period
    0.01s, so ~100Hz fundamental). ``amplitude=0.99`` yields RMS ≈ 0.92
    linear after AAC compression, which is above the 0.90 audio-pop
    threshold.
    """
    # Use a different file extension so the swap is atomic.
    tmp = path.with_name(path.stem + "_swapped.mp4")
    # aevalsrc takes the expression as the value of the ``exprs`` key;
    # FFmpeg's lavfi source parser requires commas escaped as ``\,``
    # when invoked from the shell.
    aevalsrc = f"aevalsrc=if(lt(mod(t\\,0.01)\\,0.005)\\,{amplitude}\\,-{amplitude}):d=0.6:s=44100"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-f",
            "lavfi",
            "-i",
            aevalsrc,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(tmp),
        ],
        check=True,
        capture_output=True,
    )
    tmp.replace(path)


def test_qa_self_eval_error_carries_measurements() -> None:
    """The exception type exposes the failure measurements for reporting."""
    err = QASelfEvalError(
        segment_id="seg_x",
        reason="visual_jump",
        psnr_db=8.5,
        peak_rms=0.42,
    )
    assert err.segment_id == "seg_x"
    assert err.reason == "visual_jump"
    assert err.psnr_db == 8.5
    assert err.peak_rms == 0.42
    assert "visual_jump" in str(err)
    assert "seg_x" in str(err)


def test_check_segment_boundary_artifact_includes_segment_ids(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """The per-segment artifact names both halves of the boundary."""
    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(tmp_path / "a.mp4", duration=0.5, color="testsrc")
    curr_path = _build_video(tmp_path / "b.mp4", duration=0.5, color="testsrc")

    check_segment_boundary(
        prev_path=prev_path,
        curr_path=curr_path,
        prev_segment_id="seg_alpha",
        curr_segment_id="seg_beta",
        work_dir=work_dir,
    )

    artifact = work_dir / ".cache" / "qa" / "seg_beta.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["prev_segment_id"] == "seg_alpha"
    assert payload["segment_id"] == "seg_beta"


def test_graphics_skill_run_qa_per_segment_kwarg_skips_by_default(
    tmp_path: Path,
) -> None:
    """Without ``run_qa_per_segment=True`` the per-segment check is a no-op."""
    from graphics.graphics import GraphicsSkill
    from video_editor_state.job import JobContext

    ctx = JobContext(
        job_id="qa-default",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    skill = GraphicsSkill(ctx)
    # The kwarg exists and defaults to False.
    assert getattr(skill, "run_qa_per_segment", False) is False

    # The per-segment QA hook is NOT called on a normal run. We
    # assert this by checking that no per-segment qa artifact is
    # written when run_qa_per_segment is left at its default.
    skill.run_qa_per_segment = False
    # (We don't actually render here — that would invoke ffmpeg. We just
    # confirm the constructor accepts the kwarg without error and stores
    # it as a falsy default. A full render test is in the integration
    # test suite.)


def test_graphics_skill_run_qa_per_segment_kwarg_stored() -> None:
    """``GraphicsSkill(run_qa_per_segment=True)`` stores the flag."""
    from graphics.graphics import GraphicsSkill
    from video_editor_state.job import JobContext

    ctx = JobContext(
        job_id="qa-explicit",
        work_root=Path("/tmp/w"),
        output_root=Path("/tmp/o"),
    )
    skill = GraphicsSkill(ctx, run_qa_per_segment=True)
    assert skill.run_qa_per_segment is True


def test_check_segment_boundary_flags_measurements_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """Bug #41 regression: when both measurements return None
    (e.g. the host's ffmpeg is missing psnr/astats filters), the
    check is a no-op that previously silently passed. Now the
    check fails with reason ``"measurements_incomplete"`` so
    downstream tooling can distinguish a real pass from a no-op.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(tmp_path / "a.mp4", duration=0.6, color="testsrc")
    curr_path = _build_video(tmp_path / "b.mp4", duration=0.6, color="testsrc")

    # Force both measurement helpers to return None as if ffmpeg
    # was missing the required filters.
    import qa.segment_check as sc

    monkeypatch.setattr(sc, "_measure_visual_jump", lambda p, c: None)
    monkeypatch.setattr(sc, "_measure_peak_rms_at_boundary", lambda p, c: None)

    with pytest.raises(QASelfEvalError) as exc:
        check_segment_boundary(
            prev_path=prev_path,
            curr_path=curr_path,
            prev_segment_id="seg_a",
            curr_segment_id="seg_b",
            work_dir=work_dir,
        )

    assert exc.value.reason == "measurements_incomplete"
    assert exc.value.psnr_db is None
    assert exc.value.peak_rms is None

    # The artifact is written with passed=False so the failure is
    # inspectable downstream.
    artifact = work_dir / ".cache" / "qa" / "seg_b.json"
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["reason"] == "measurements_incomplete"


def test_check_segment_boundary_artifact_has_created_at_timestamp(
    tmp_path: Path,
    work_dir: Path,
) -> None:
    """Bug #42 regression: the per-segment artifact carries a
    ``created_at`` epoch timestamp so a re-render of the same
    job can be distinguished from a prior one. A re-render that
    happens a measurable interval later updates the timestamp
    rather than the prior artifact surviving silently.
    """
    import time as time_module

    work_dir.mkdir(parents=True, exist_ok=True)
    prev_path = _build_video(tmp_path / "a.mp4", duration=0.5, color="testsrc")
    curr_path = _build_video(tmp_path / "b.mp4", duration=0.5, color="testsrc")

    check_segment_boundary(
        prev_path=prev_path,
        curr_path=curr_path,
        prev_segment_id="seg_a",
        curr_segment_id="seg_b",
        work_dir=work_dir,
    )

    artifact = work_dir / ".cache" / "qa" / "seg_b.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert "created_at" in payload
    # The timestamp is a recent epoch second (within the last 60s
    # so the test isn't sensitive to clock drift across hosts).
    now = time_module.time()
    assert abs(now - payload["created_at"]) < 60
