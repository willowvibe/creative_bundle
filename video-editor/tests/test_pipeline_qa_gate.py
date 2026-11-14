"""Tests for the SkillsPipeline QA gate (Task #7).

The pipeline gains a new optional ``run_qa=True`` flag. When set, the
pipeline calls ``QASkill.run`` between music and export. If the QA
report has any failing check, ``SkillsPipeline.run`` raises and the
export is skipped (so the deliverable is never written on a known-bad
render).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_pipelines.pipeline import SkillsPipeline
from video_editor_state.job import JobContext


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="pipeline-qa-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


@pytest.fixture
def transcript_with_filler(tmp_path: Path) -> Path:
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("pause", 2.0, 2.5),
            ("Again", 3.0, 3.4),
        ]
    )
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps(transcript), encoding="utf-8")
    return path


def test_pipeline_run_qa_default_is_off(
    tmp_path: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_media: Path,
) -> None:
    """``SkillsPipeline.run(run_qa=...)`` defaults to ``False`` — no QA
    stage is run, so a low-quality video is still exported.

    Confirms the no-op contract: existing call sites that omit the flag
    are unaffected.
    """
    pipeline = SkillsPipeline(ctx)
    # Call without specifying run_qa; should be a no-op.
    result = pipeline.run(
        tmp_media,
        transcript_path=transcript_with_filler,
    )

    # Export ran.
    assert Path(result["final"]).exists()
    # No qa_report.json was written by the pipeline (the user didn't
    # ask for QA).
    qa_report = ctx.work_dir / "qa" / "qa_report.json"
    assert not qa_report.exists()


def test_pipeline_qa_gate_fails_build(
    tmp_path: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_media: Path,
) -> None:
    """With ``run_qa=True``, a QA-failing input blocks the export.

    Strategy: produce a 320x240 video (wrong aspect ratio for the default
    16:9 expectation), run the full pipeline with ``run_qa=True`` against
    it, and assert that the pipeline raises and the export artifact is
    NOT written.
    """
    # Make a deliberately wrong-aspect-ratio input: 320x180 (16:9 OK)
    # is the default — we need a different shape. The pipeline renders
    # to a 16:9 output regardless, so a different shape on the input
    # doesn't help. Instead, force a failing QA by setting a very tight
    # loudness target that the synthetic 440Hz sine cannot meet.
    pipeline = SkillsPipeline(ctx)

    # Use a much-too-loud target so the ebur128 check will fail
    # (the synthetic 440Hz sine measures ~-22 LUFS; set a target of
    # +5 LUFS, far outside the ±2 LU tolerance).
    with pytest.raises(Exception) as exc:
        pipeline.run(
            tmp_media,
            transcript_path=transcript_with_filler,
            run_qa=True,
            loudness_target_lufs=5.0,  # +5 LUFS — guaranteed to fail
        )

    assert "QA" in str(exc.value) or "qa" in str(exc.value).lower()
    # The export was NOT written.
    final = ctx.output_dir / "final.mp4"
    assert not final.exists()
    # The QA report WAS written so the user can see what failed.
    qa_report = ctx.work_dir / "qa" / "qa_report.json"
    assert qa_report.exists()
    report = json.loads(qa_report.read_text(encoding="utf-8"))
    assert report["passed"] is False


def test_pipeline_qa_gate_passes_on_healthy_input(
    tmp_path: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_media: Path,
) -> None:
    """With ``run_qa=True`` and a healthy input, the pipeline succeeds.

    The default ``short_explainer`` preset is 9:16 (1080x1920), so the
    QA aspect-ratio check is matched against 9:16. The captions stage
    normalizes the audio to ~-16 LUFS (the YouTube/Spotify target),
    so the default -16 LUFS target is used here.
    """
    pipeline = SkillsPipeline(ctx)
    result = pipeline.run(
        tmp_media,
        transcript_path=transcript_with_filler,
        run_qa=True,
        expected_aspect_ratio="9:16",
    )

    assert Path(result["final"]).exists()
    qa_report = ctx.work_dir / "qa" / "qa_report.json"
    assert qa_report.exists()
    report = json.loads(qa_report.read_text(encoding="utf-8"))
    assert report["passed"] is True


def test_pipeline_qa_gate_targets_music_video_when_music_ran(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ctx: JobContext,
    transcript_with_filler: Path,
    tmp_media: Path,
) -> None:
    """Bug #40 regression: when music ran, the gate measures the
    music-mixed video, not the captions video.

    The canonical graph path folds the music bed into the single composed
    render, so the gate must measure ``graph/composed.mp4``.
    """
    import subprocess

    # A 1-second silent audio file is enough to make MusicSkill.run
    # produce ``with_music.mp4`` (it always writes a file when run).
    music_file = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono:d=1",
            str(music_file),
        ],
        check=True,
        capture_output=True,
    )

    pipeline = SkillsPipeline(ctx, music_path=str(music_file))

    captured: dict[str, object] = {}

    class _FakeQASkill:
        def __init__(self, ctx_arg: object, **kwargs: object) -> None:
            self.kwargs = kwargs

        def run(self, video_path: object = None) -> object:
            captured["video_path"] = video_path

            class _Rep:
                passed = True
                checks: list[object] = []

            return _Rep()

    monkeypatch.setattr("qa.qa.QASkill", _FakeQASkill, raising=False)

    pipeline.run(
        tmp_media,
        transcript_path=transcript_with_filler,
        run_qa=True,
    )

    # The gate was called with the music-mixed video path.
    expected = ctx.work_dir / "graph" / "composed.mp4"
    assert captured.get("video_path") == expected, (
        f"QA gate should target the music video; got {captured.get('video_path')!r}"
    )
