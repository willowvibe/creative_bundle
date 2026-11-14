"""Tests for the B-roll skill: planning, ranking, and clip insertion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from broll.broll import BrollSkill
from broll.planner import (
    _clean_query,
    extract_moments,
    extract_moments_async,
)
from broll.ranker import ClipCandidate, rank_candidates, score_candidate
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_state.job import JobContext


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="broll-test",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


# ---------------------------------------------------------------------------
# Planner helpers
# ---------------------------------------------------------------------------


def test_clean_query_filters_stopwords() -> None:
    """Stopwords are removed; salient nouns survive (max 4 words)."""
    assert (
        _clean_query("cooking pasta with fresh tomatoes and basil")
        == "cooking pasta fresh tomatoes"
    )


def test_clean_query_empty_text_falls_back() -> None:
    """Empty text falls back to a generic query."""
    assert _clean_query("") == "b-roll"
    assert _clean_query("   ") == "b-roll"


def test_extract_moments_from_segments(tmp_path: Path) -> None:
    """Segment-level transcripts produce non-empty deterministic moments."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 10.0, "text": "Today we are cooking pasta with tomatoes."},
            {"start": 10.0, "end": 20.0, "text": "Now I will show you the finished dish."},
        ]
    }
    moments = extract_moments(transcript)
    assert moments
    for moment in moments:
        assert moment.end > moment.start
        assert moment.query
        assert moment.description


def test_extract_moments_from_words(tmp_path: Path) -> None:
    """Word-level transcripts are grouped into pseudo-segments."""
    words = []
    t = 0.0
    for word in ["welcome", "to", "today", "cooking", "show"]:
        words.append({"word": word, "start": t, "end": t + 0.4})
        t += 0.5
    transcript = {"words": words}
    moments = extract_moments(transcript)
    # Too short to cover anything.
    assert moments == []


def test_extract_moments_short_segments_skipped() -> None:
    """Short segments yield no moments when coverage would be too short."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 4.0, "text": "Short intro here."},
        ]
    }
    moments = extract_moments(transcript)
    assert moments == []


@pytest.mark.asyncio
async def test_extract_moments_llm_failure_falls_back() -> None:
    """A failing/parsing LLM router falls back to deterministic planning."""
    transcript = {
        "segments": [
            {"start": 0.0, "end": 12.0, "text": "cooking pasta tomatoes basil."},
        ]
    }

    class FailingRouter:
        async def generate(self, *args: object, **kwargs: object) -> str:
            raise RuntimeError("LLM unavailable")

    moments = await extract_moments_async(transcript, router=FailingRouter())  # type: ignore[arg-type]
    assert moments


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------


def test_score_candidate_prefers_close_duration() -> None:
    """A candidate matching the target duration scores highest."""
    exact = ClipCandidate(path=Path("x.mp4"), duration=4.0, width=1920, height=1080)
    too_short = ClipCandidate(path=Path("y.mp4"), duration=1.0, width=1920, height=1080)
    assert score_candidate(exact, 4.0) > score_candidate(too_short, 4.0)


def test_rank_candidates_picks_best() -> None:
    """rank_candidates returns the highest-scoring candidate."""
    good = ClipCandidate(path=Path("good.mp4"), duration=4.0, width=1920, height=1080)
    bad = ClipCandidate(path=Path("bad.mp4"), duration=0.5, width=640, height=360)
    best = rank_candidates([bad, good], 4.0)
    assert best is not None
    assert best.path.name == "good.mp4"


def test_rank_candidates_empty() -> None:
    assert rank_candidates([], 4.0) is None


# ---------------------------------------------------------------------------
# Skill behavior with a mocked manager
# ---------------------------------------------------------------------------


def _synthetic_clip(tmp_path: Path, name: str, duration: float = 2.0) -> Path:
    import subprocess

    clip = tmp_path / name
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
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    return clip


def _transcript() -> dict[str, object]:
    return {
        "segments": [
            {"start": 0.0, "end": 8.0, "text": "cooking pasta with fresh tomatoes."},
            {"start": 8.0, "end": 16.0, "text": "here is the finished dish."},
        ]
    }


class FakeManager:
    """A manager stub returning a predetermined clip per request."""

    def __init__(self, clip: Path):
        self._clip = clip

    async def resolve(self, requests: list[object]) -> list[AssetResult]:
        results = []
        for request in requests:  # type: ignore[union-attr]
            results.append(
                AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.VIDEO,
                    scene_id=request.scene_id,
                    local_path=str(self._clip),
                )
            )
        return results


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
def test_broll_skill_name(ctx: JobContext) -> None:
    skill = BrollSkill(ctx)
    assert skill.name == "broll"


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
@pytest.mark.asyncio
async def test_broll_skill_end_to_end(ctx: JobContext, tmp_path: Path) -> None:
    """The skill plans moments, resolves clips, and overlays them."""
    base = _synthetic_clip(tmp_path, "base.mp4", 5.0)
    clip = _synthetic_clip(tmp_path, "broll.mp4", 2.0)

    skill = BrollSkill(
        ctx,
        providers=["fake"],
        fallback_provider="",
        min_moment_duration=2.0,
        max_moment_duration=4.0,
        coverage_fraction=0.5,
        manager=FakeManager(clip),  # type: ignore[arg-type]
    )
    result = await skill.run(base, _transcript())
    output = Path(result["output"])
    assert output.exists()
    plan = json.loads(Path(result["plan"]).read_text())
    assert "inserts" in plan
    assert plan["output"] == str(output)


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not available")
@pytest.mark.asyncio
async def test_broll_skill_no_moments_copies_video(ctx: JobContext, tmp_path: Path) -> None:
    """No B-roll moments still produces an output video."""
    base = _synthetic_clip(tmp_path, "base.mp4", 3.0)
    skill = BrollSkill(
        ctx,
        providers=["fake"],
        min_moment_duration=6.0,  # no moment will fit
        manager=FakeManager(base),  # type: ignore[arg-type]
    )
    result = await skill.run(base, {"segments": [{"start": 0.0, "end": 3.0, "text": "hi."}]})
    output = Path(result["output"])
    assert output.exists()
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_broll_skill_missing_input_raises(ctx: JobContext, tmp_path: Path) -> None:
    skill = BrollSkill(ctx)
    with pytest.raises(FileNotFoundError):
        await skill.run(tmp_path / "missing.mp4", {})
