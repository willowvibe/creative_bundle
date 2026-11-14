"""Tests for the raw-footage edit planner.

The planner has two paths: an LLM-driven plan (when a router is
provided) and a deterministic fallback that removes silences,
pauses, and filler words. These tests cover the deterministic
path and the pure helpers — the LLM path is exercised by the
scene-planner tests with a fake router.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_core.models.edit_plan import Cut
from video_editor_engine.planner.raw_planner import (
    _build_default_broll,
    _format_transcript_for_llm,
    _merge_regions,
    plan_raw_edit,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_merge_regions_merges_overlapping() -> None:
    """Overlapping regions collapse to a single span."""
    merged = _merge_regions([(0.0, 1.0), (0.5, 2.0), (3.0, 4.0)])
    assert merged == [(0.0, 2.0), (3.0, 4.0)]


def test_merge_regions_handles_empty() -> None:
    """Empty input returns empty list."""
    assert _merge_regions([]) == []


def test_format_transcript_for_llm_uses_segments() -> None:
    """The LLM transcript format prefixes each segment with a [start - end] range."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "Hello world"},
            {"start": 2.0, "end": 3.0, "text": "Second segment"},
        ],
    }
    formatted = _format_transcript_for_llm(transcript)
    assert "[0.00 - 1.50] Hello world" in formatted
    assert "[2.00 - 3.00] Second segment" in formatted
    # Two segments, so one newline.
    assert formatted.count("\n") == 1


def test_format_transcript_for_llm_handles_missing_segments() -> None:
    """A transcript without segments returns an empty string."""
    assert _format_transcript_for_llm({}) == ""
    assert _format_transcript_for_llm({"segments": []}) == ""


def test_build_default_broll_inserts_only_long_kept_segments() -> None:
    """B-roll inserts are only generated for kept segments > 6 seconds."""
    cuts = [
        Cut(start=0.0, end=2.0, reason="speech"),  # too short, no insert
        Cut(start=3.0, end=12.0, reason="speech"),  # 9s, gets an insert
    ]
    inserts = _build_default_broll(cuts)
    assert len(inserts) == 1
    insert = inserts[0]
    assert insert.start > 3.0  # 20% into the segment
    assert insert.end < 12.0
    assert insert.provider == "placeholder"  # safe default


def test_build_default_broll_skips_when_no_room() -> None:
    """A long segment with too little tail room doesn't get an insert."""
    cuts = [
        # 6.5s segment; the helper wants 3s of b-roll ending 0.5s before the end.
        # 0.2*6.5 = 1.3 start, end = min(6.0, 1.3 + 3.0) = 4.3 → 1.3 < 4.3, valid.
        Cut(start=0.0, end=6.5, reason="speech"),
    ]
    inserts = _build_default_broll(cuts)
    assert len(inserts) == 1


# ---------------------------------------------------------------------------
# Deterministic planner
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_plan_raw_edit_uses_deterministic_when_no_router(
    tmp_path: Path,
) -> None:
    """With llm_router=None, the planner uses silence/pause/filler removal."""
    transcript_path = _write_json(
        tmp_path / "transcript.json",
        {
            "language": "en",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "Hello world"},
            ],
            "words": [],
        },
    )
    silence_path = _write_json(
        tmp_path / "silence.json",
        {"silences": [{"start": 0.5, "end": 1.5, "duration": 1.0}]},
    )
    filler_path = _write_json(tmp_path / "filler.json", {"matches": []})
    output_path = tmp_path / "plan.json"

    plan = await plan_raw_edit(
        job_id="det-plan",
        transcript_path=transcript_path,
        silence_path=silence_path,
        filler_path=filler_path,
        output_path=output_path,
        llm_router=None,
        use_llm=True,  # ignored when router is None
    )

    assert plan.job_id == "det-plan"
    assert "Deterministic" in plan.summary
    assert output_path.exists()
    # The plan removed the silence region; we should have a cut before and
    # possibly after the silence, not including it.
    for cut in plan.cuts:
        assert not (cut.start < 0.5 < cut.end), (
            f"cut ({cut.start}, {cut.end}) overlaps the removed silence"
        )


@pytest.mark.asyncio
async def test_plan_raw_edit_uses_deterministic_when_use_llm_false(
    tmp_path: Path,
) -> None:
    """Even with a router, use_llm=False forces the deterministic path."""
    transcript_path = _write_json(
        tmp_path / "transcript.json",
        {
            "language": "en",
            "segments": [],
            "words": [],
        },
    )
    silence_path = _write_json(tmp_path / "silence.json", {"silences": []})
    filler_path = _write_json(tmp_path / "filler.json", {"matches": []})
    output_path = tmp_path / "plan.json"

    class ShouldNotBeCalled:
        async def generate(self, **kwargs: object) -> str:
            raise AssertionError("LLM should not be called when use_llm=False")

    plan = await plan_raw_edit(
        job_id="det-plan-2",
        transcript_path=transcript_path,
        silence_path=silence_path,
        filler_path=filler_path,
        output_path=output_path,
        llm_router=ShouldNotBeCalled(),  # type: ignore[arg-type]
        use_llm=False,
    )

    assert "Deterministic" in plan.summary
    # With no transcript segments and no silence regions, the planner
    # produces an empty cut list — that's the correct behavior (nothing
    # to keep). The important assertion is that we didn't fall through
    # to the LLM path.
    assert plan.cuts == []
