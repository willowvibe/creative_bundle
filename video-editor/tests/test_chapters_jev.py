"""Tests for the Jev-backed chapter segmentation — no network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chapters.chapters import (
    ChapterSkill,
    _infer_title_from_text,
    _segment_chapters_by_gaps,
)
from video_editor_state.job import JobContext


class _FakeClassifyClient:
    """Minimal stand-in for ``video_editor_llm.ClassifyClient``.

    ``boundaries`` holds the segment texts that should be judged as starting a
    new topic (``noul`` ~ 0.95); everything else scores ~ 0.05. ``scores`` maps
    a title to the raw ``score`` level returned for a score question.
    """

    def __init__(
        self,
        *,
        boundaries: set[str] | None = None,
        scores: dict[str, float] | None = None,
        available: bool = True,
        raise_error: bool = False,
    ) -> None:
        self._boundaries = boundaries or set()
        self._scores = scores or {}
        self._available = available
        self._raise = raise_error
        self.calls: list[int] = []

    def available(self) -> bool:
        return self._available

    async def classify(
        self,
        state: Any,
        questions: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append(len(questions))
        if self._raise:
            raise RuntimeError("boom")
        answers: dict[str, Any] = {}
        for key, question in questions.items():
            instructions = question.get("instructions") or {}
            if question.get("type") == "score":
                title = str(instructions.get("title", ""))
                answers[key] = {
                    "type": "score",
                    "score": self._scores.get(title, 0.0),
                }
            else:
                segment = str(instructions.get("segment", ""))
                answers[key] = {
                    "type": "noul",
                    "noul": 0.95 if segment in self._boundaries else 0.05,
                }
        return {"answers": answers}

    async def close(self) -> None:  # pragma: no cover
        pass


class _StubLLM:
    """Minimal LLM stub that returns canned JSON."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        return self.response


def _ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="chapters-jev",
        work_root=tmp_path / "work",
        output_root=tmp_path / "out",
    )


def _segments(
    samples: list[tuple[float, str, float]],
) -> list[dict[str, Any]]:
    return [{"start": start, "text": text, "end": end} for start, text, end in samples]


def _topic_segments() -> list[dict[str, Any]]:
    """Two topics: an intro (0-20s) then a cooking topic (20-40s)."""
    return _segments(
        [
            (0.0, "Welcome to the show", 10.0),
            (10.0, "Today we talk about cooking", 20.0),
            (20.0, "First preheat the oven", 30.0),
            (30.0, "Then mix the flour", 40.0),
        ]
    )


def _gap_segments() -> list[dict[str, Any]]:
    """A long pause after 40s gives the gap fallback two chapters."""
    return _segments(
        [
            (0.0, "alpha", 20.0),
            (20.0, "beta", 40.0),
            (45.0, "gamma", 60.0),
        ]
    )


# ---------------------------------------------------------------------------
# _jev_segment_chapters
# ---------------------------------------------------------------------------


def test_jev_segments_into_chapters(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(boundaries={"First preheat the oven"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, min_chapter_duration=5.0)
    chapters = skill._jev_segment_chapters(_topic_segments())

    assert chapters is not None
    assert [c["start"] for c in chapters] == [0.0, 20.0]
    # Titles reuse the deterministic heuristic, never a generated title.
    assert chapters[0]["title"] == _infer_title_from_text(
        ["Welcome to the show", "Today we talk about cooking"]
    )
    assert chapters[1]["title"] == _infer_title_from_text(
        ["First preheat the oven", "Then mix the flour"]
    )


def test_jev_judges_every_segment_in_one_call(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(boundaries={"First preheat the oven"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, min_chapter_duration=5.0)
    skill._jev_segment_chapters(_topic_segments())

    # One classify call, with one question per segment after the first.
    assert fake.calls == [3]


def test_jev_unavailable_returns_none_without_calling(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(available=False)
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    assert skill._jev_segment_chapters(_topic_segments()) is None
    assert fake.calls == []


def test_jev_no_boundaries_returns_none(tmp_path: Path) -> None:
    fake = _FakeClassifyClient()  # nothing crosses the threshold
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    assert skill._jev_segment_chapters(_topic_segments()) is None
    assert fake.calls == [3]


def test_jev_below_threshold_yields_no_boundary(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(boundaries={"First preheat the oven"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, jev_boundary_threshold=0.99)

    assert skill._jev_segment_chapters(_topic_segments()) is None


def test_jev_error_returns_none(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(raise_error=True)
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    assert skill._jev_segment_chapters(_topic_segments()) is None


def test_jev_merges_chapter_shorter_than_min_duration(tmp_path: Path) -> None:
    # The flagged boundary would make chapter 1 only 5s long; it must merge.
    segments = _segments(
        [
            (0.0, "Intro", 5.0),
            (5.0, "A brand new topic", 40.0),
        ]
    )
    fake = _FakeClassifyClient(boundaries={"A brand new topic"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, min_chapter_duration=30.0)
    chapters = skill._jev_segment_chapters(segments)

    assert chapters is not None
    assert len(chapters) == 1
    assert chapters[0]["start"] == 0.0


def test_jev_respects_max_chapters(tmp_path: Path) -> None:
    segments = _segments(
        [
            (0.0, "a", 10.0),
            (10.0, "b", 20.0),
            (20.0, "c", 30.0),
            (30.0, "d", 40.0),
            (40.0, "e", 50.0),
        ]
    )
    fake = _FakeClassifyClient(boundaries={"b", "c", "d", "e"})
    skill = ChapterSkill(
        _ctx(tmp_path),
        classify_client=fake,
        min_chapter_duration=5.0,
        max_chapters=2,
    )
    chapters = skill._jev_segment_chapters(segments)

    assert chapters is not None
    assert len(chapters) == 2


def test_jev_blank_segment_not_judged(tmp_path: Path) -> None:
    segments = _segments(
        [
            (0.0, "intro", 10.0),
            (10.0, "   ", 20.0),
            (20.0, "real topic", 30.0),
        ]
    )
    fake = _FakeClassifyClient(boundaries={"real topic"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, min_chapter_duration=5.0)
    chapters = skill._jev_segment_chapters(segments)

    assert chapters is not None
    assert fake.calls == [1]  # only the non-blank second segment was judged


# ---------------------------------------------------------------------------
# run() precedence: LLM > Jev > gap fallback
# ---------------------------------------------------------------------------


def test_run_prefers_llm_over_jev(tmp_path: Path) -> None:
    llm = _StubLLM('[{"start": 0.0, "title": "Intro"}, {"start": 40.0, "title": "Demo"}]')
    fake = _FakeClassifyClient(boundaries={"First preheat the oven"})
    skill = ChapterSkill(
        _ctx(tmp_path),
        llm_client=llm,
        classify_client=fake,
        min_chapter_duration=5.0,
    )
    result = skill.run({"segments": _topic_segments()})

    assert llm.calls == 1
    assert fake.calls == []
    assert [c["title"] for c in result["chapters"]] == ["Intro", "Demo"]


def test_run_uses_jev_when_no_llm(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(boundaries={"First preheat the oven"})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake, min_chapter_duration=5.0)
    result = skill.run({"segments": _topic_segments()})

    assert fake.calls == [3]
    assert [c["start"] for c in result["chapters"]] == [0.0, 20.0]


def test_run_falls_back_to_gaps_when_jev_unavailable(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(available=False)
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)
    segments = _gap_segments()
    result = skill.run({"segments": segments})

    assert fake.calls == []
    assert result["chapters"] == _segment_chapters_by_gaps(
        segments,
        min_duration=skill.min_chapter_duration,
        max_chapters=skill.max_chapters,
    )


def test_run_without_clients_matches_gap_fallback(tmp_path: Path) -> None:
    # No llm_client, no classify_client (and TYPESAFE_API_KEY is unset by the
    # hermetic fixture): behavior must be identical to the pre-Jev skill.
    skill = ChapterSkill(_ctx(tmp_path))
    segments = _gap_segments()
    result = skill.run({"segments": segments})

    assert result["chapters"] == _segment_chapters_by_gaps(
        segments,
        min_duration=skill.min_chapter_duration,
        max_chapters=skill.max_chapters,
    )


# ---------------------------------------------------------------------------
# score_title_options helper
# ---------------------------------------------------------------------------


def test_score_title_options_normalizes(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(scores={"Setup": 4.0, "Mid": 2.0})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    scored = skill.score_title_options(["Setup", "Mid", "Flat"])

    assert scored == {"Setup": 1.0, "Mid": 0.5, "Flat": 0.0}
    assert fake.calls == [3]  # one batched call for all titles


def test_score_title_options_unavailable_returns_empty(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(available=False)
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    assert skill.score_title_options(["Setup"]) == {}
    assert fake.calls == []


def test_score_title_options_empty_input(tmp_path: Path) -> None:
    fake = _FakeClassifyClient(scores={"Setup": 4.0})
    skill = ChapterSkill(_ctx(tmp_path), classify_client=fake)

    assert skill.score_title_options([]) == {}
    assert fake.calls == []
