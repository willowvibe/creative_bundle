"""Tests for the chapters skill."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from chapters.chapters import (
    ChapterSkill,
    _format_timestamp,
    _infer_title_from_text,
    _segment_chapters_by_gaps,
)
from video_editor_state.job import JobContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="chapter-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def _segments(
    samples: list[tuple[float, str]] | list[tuple[float, str, float]] | None = None,
) -> list[dict[str, Any]]:
    """Build segment dicts from ``(start, text)`` or ``(start, text, end)`` tuples.

    When ``end`` is omitted or ``None``, it defaults to ``start + 1.0``.
    """
    if samples is None:
        samples = []
    out: list[dict[str, Any]] = []
    for sample in samples:
        if len(sample) == 3:
            start, text, end = sample
        else:
            start, text = sample  # type: ignore[misc]
            end = None
        out.append(
            {
                "start": start,
                "text": text,
                "end": end if end is not None else start + 1.0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------


def test_format_timestamp_seconds() -> None:
    assert _format_timestamp(0) == "0:00"
    assert _format_timestamp(5) == "0:05"
    assert _format_timestamp(59) == "0:59"


def test_format_timestamp_minutes() -> None:
    assert _format_timestamp(60) == "1:00"
    assert _format_timestamp(125) == "2:05"


def test_format_timestamp_hours() -> None:
    assert _format_timestamp(3600) == "1:00:00"
    assert _format_timestamp(3725) == "1:02:05"


def test_format_timestamp_negative_clamps() -> None:
    assert _format_timestamp(-5) == "0:00"


# ---------------------------------------------------------------------------
# Title inference
# ---------------------------------------------------------------------------


def test_infer_title_short_text_returns_sentence() -> None:
    assert _infer_title_from_text(["Hello world"]) == "Hello world"


def test_infer_title_truncates_long_text() -> None:
    long = "This is a very long opening sentence that should be truncated"
    title = _infer_title_from_text([long])
    assert len(title) <= 50


def test_infer_title_empty_returns_intro() -> None:
    assert _infer_title_from_text([]) == "Intro"


# ---------------------------------------------------------------------------
# Gap-based segmentation
# ---------------------------------------------------------------------------


def test_segment_chapters_by_gaps_empty() -> None:
    assert _segment_chapters_by_gaps([]) == []


def test_segment_chapters_by_gaps_no_gaps_one_chapter() -> None:
    segs = _segments([(0.0, "alpha", 1.0), (1.0, "beta", 2.0), (2.0, "gamma", 3.0)])
    chapters = _segment_chapters_by_gaps(segs, gap_threshold=2.0, min_duration=1.0)
    assert len(chapters) == 1
    assert chapters[0]["start"] == 0.0


def test_segment_chapters_by_gaps_splits_at_pauses() -> None:
    segs = _segments(
        [
            (0.0, "first block alpha", 5.0),
            (5.0, "first block beta", 10.0),
            # large gap here -> new chapter
            (15.0, "second block alpha", 20.0),
            (20.0, "second block beta", 25.0),
        ]
    )
    chapters = _segment_chapters_by_gaps(segs, gap_threshold=2.0, min_duration=5.0)
    assert len(chapters) == 2
    assert chapters[0]["start"] == 0.0
    assert chapters[1]["start"] == 15.0


def test_segment_chapters_by_gaps_respects_max_chapters() -> None:
    segs = _segments(
        [
            (0.0, "a", 10.0),
            (12.0, "b", 22.0),
            (24.0, "c", 34.0),
            (36.0, "d", 46.0),
        ]
    )
    chapters = _segment_chapters_by_gaps(segs, gap_threshold=2.0, min_duration=1.0, max_chapters=2)
    assert len(chapters) <= 2


# ---------------------------------------------------------------------------
# LLM segmentation prompt parsing
# ---------------------------------------------------------------------------


class _StubLLM:
    """Minimal LLM stub that returns canned JSON."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[str] = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def test_llm_segmentation_used_when_client_provided(ctx: JobContext) -> None:
    llm = _StubLLM('[{"start": 0.0, "title": "Intro"}, {"start": 60.0, "title": "Demo"}]')
    skill = ChapterSkill(ctx, llm_client=llm)
    transcript = {
        "segments": _segments(
            [
                (0.0, "first"),
                (30.0, "middle"),
                (60.0, "later"),
                (90.0, "end"),
                (120.0, "end2"),
            ]
        )
    }
    result = skill.run(transcript)
    assert llm.calls, "LLM should have been called"
    assert result["count"] == 2
    titles = [ch["title"] for ch in result["chapters"]]
    assert titles == ["Intro", "Demo"]


def test_llm_segmentation_json_in_code_block(ctx: JobContext) -> None:
    llm = _StubLLM(
        "Here you go:\n```json\n"
        '[{"start": 0.0, "title": "Intro"}, {"start": 30.0, "title": "Setup"}]\n'
        "```\n"
    )
    skill = ChapterSkill(ctx, llm_client=llm)
    transcript = {"segments": _segments([(0.0, "a"), (20.0, "b"), (40.0, "c"), (60.0, "d")])}
    result = skill.run(transcript)
    assert result["count"] == 2


def test_llm_failure_falls_back_to_gaps(ctx: JobContext) -> None:
    llm = _StubLLM("not valid json at all")
    skill = ChapterSkill(ctx, llm_client=llm)
    transcript = {
        "segments": _segments(
            [
                (0.0, "first", 5.0),
                (5.0, "first2", 10.0),
                (15.0, "second", 20.0),  # large gap -> boundary
                (20.0, "second2", 25.0),
            ]
        )
    }
    result = skill.run(transcript)
    assert result["count"] >= 1
    # Should have fallen back (gap-based) rather than crashed.
    assert isinstance(result["chapters"], list)


def test_no_llm_uses_gap_fallback(ctx: JobContext) -> None:
    skill = ChapterSkill(ctx)  # no llm_client
    transcript = {
        "segments": _segments(
            [
                (0.0, "alpha", 5.0),
                (5.0, "alpha2", 10.0),
                (15.0, "beta", 20.0),
            ]
        )
    }
    result = skill.run(transcript)
    assert result["count"] >= 1


def test_short_transcript_skips_llm(ctx: JobContext) -> None:
    llm = _StubLLM('[{"start": 0.0, "title": "Should not be used"}]')
    skill = ChapterSkill(ctx, llm_client=llm)
    transcript = {"segments": _segments([(0.0, "a", 1.0), (1.0, "b", 2.0)])}
    skill.run(transcript)
    assert llm.calls == [], "LLM should be skipped when transcript is too short"


# ---------------------------------------------------------------------------
# run() outputs
# ---------------------------------------------------------------------------


def test_run_writes_json_and_txt(ctx: JobContext) -> None:
    skill = ChapterSkill(ctx)
    transcript = {
        "segments": _segments(
            [
                (0.0, "first", 5.0),
                (5.0, "first2", 10.0),
                (15.0, "second", 20.0),
                (20.0, "second2", 25.0),
            ]
        )
    }
    result = skill.run(transcript)
    json_path = Path(result["chapters_json"])
    txt_path = Path(result["chapters_txt"])
    assert json_path.exists()
    assert txt_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["job_id"] == ctx.job_id
    assert payload["count"] == len(result["chapters"])
    # YouTube txt is plain "0:00 Title" lines
    lines = txt_path.read_text(encoding="utf-8").strip().splitlines()
    assert all(":" in line for line in lines)


def test_run_empty_segments_raises(ctx: JobContext) -> None:
    skill = ChapterSkill(ctx)
    with pytest.raises(ValueError):
        skill.run({"segments": []})


def test_run_clamps_chapters_to_duration(ctx: JobContext) -> None:
    llm = _StubLLM(
        '[{"start": 0.0, "title": "Intro"}, '
        '{"start": 30.0, "title": "Mid"}, '
        '{"start": 90.0, "title": "PastEnd"}]'
    )
    skill = ChapterSkill(ctx, llm_client=llm)
    transcript = {
        "segments": _segments(
            [
                (0.0, "a"),
                (20.0, "b"),
                (40.0, "c"),
                (60.0, "d"),
                (80.0, "e"),
            ]
        )
    }
    result = skill.run(transcript, duration=60.0)
    starts = [ch["start"] for ch in result["chapters"]]
    assert all(s <= 60.0 for s in starts)


def test_run_output_dir_created(ctx: JobContext) -> None:
    skill = ChapterSkill(ctx)
    transcript = {
        "segments": _segments(
            [
                (0.0, "alpha", 5.0),
                (5.0, "alpha2", 10.0),
            ]
        )
    }
    skill.run(transcript)
    assert (ctx.work_dir / "chapters").exists()


def test_skill_name_property(ctx: JobContext) -> None:
    skill = ChapterSkill(ctx)
    assert skill.name == "chapters"
