"""Error-path tests for the rough cut skill.

The happy paths are covered in `test_rough_cut.py`. This file adds
regression tests for the failure modes that have caused real outages:
missing source, malformed transcripts, partial-rerender without a
prior EDL, and zero-word transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rough_cut.rough_cut import RoughCutSkill
from video_editor_core.timeline import EDL, Segment
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
    ctx = JobContext(
        job_id="test-rough-err",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    # Bug #89 regression: JobContext no longer mkdirs in __init__; tests
    # must explicitly ensure_dirs() before writing into the context.
    ctx.ensure_dirs()
    return ctx


def test_run_raises_when_source_missing(
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """Passing a non-existent source path raises FileNotFoundError.

    This is the most common user error: they hand a typo'd path to
    ``ve rough-cut`` and expect a clear failure, not a hung ffmpeg.
    """
    skill = RoughCutSkill(ctx)
    missing = tmp_path / "does_not_exist.mp4"

    with pytest.raises(FileNotFoundError):
        skill.run(missing)


def test_run_succeeds_with_empty_transcript(
    tmp_media: Path,
    ctx: JobContext,
) -> None:
    """A transcript with no words produces a single full-duration segment.

    This protects against the "0 words" corner case where the EDL
    would be empty (and ``compute_timeline_segments`` would error).
    """
    transcript = _build_transcript([])
    transcript_path = ctx.work_dir.parent / "empty.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    skill = RoughCutSkill(ctx)
    locked = skill.run(tmp_media, transcript_path=transcript_path)

    assert locked.exists()
    edl_path = ctx.artifact("plan", "edl.json")
    edl = EDL(**json.loads(edl_path.read_text(encoding="utf-8")))
    # One segment covering the whole source.
    assert len(edl.segments) == 1
    assert edl.segments[0].source_start == pytest.approx(0.0)


def test_update_and_render_raises_without_prior_edl(
    tmp_media: Path,
    ctx: JobContext,
) -> None:
    """Calling update_and_render before run() raises FileNotFoundError.

    A Second Pass refinement on a job that never had a rough cut
    would previously crash with an obscure KeyError or empty-EDL
    error. The clear ``FileNotFoundError`` makes the user-visible
    contract explicit.
    """
    skill = RoughCutSkill(ctx)
    # Note: run() is intentionally NOT called.
    new_edl = EDL(
        job_id=ctx.job_id,
        source_path=str(tmp_media),
        segments=[
            Segment(
                id="s0",
                source_start=0.0,
                source_end=1.0,
                timeline_start=0.0,
                timeline_end=1.0,
            ),
        ],
    )

    with pytest.raises(FileNotFoundError):
        skill.update_and_render(new_edl)


def test_run_propagates_bad_transcript_json(
    tmp_media: Path,
    ctx: JobContext,
    tmp_path: Path,
) -> None:
    """Malformed transcript JSON causes a clear JSONDecodeError.

    The rough cut skill only does ``shutil.copyfile`` for an explicit
    override (no validation), so the error surfaces in ``analyze()``
    when ``json.load`` is called. A regression that silently skipped
    JSON validation would let bad data through to graphics/captions.
    """
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")

    skill = RoughCutSkill(ctx)
    with pytest.raises(json.JSONDecodeError):
        skill.run(tmp_media, transcript_path=bad)
