"""Tests for the Jev-backed thumbnail title-hook scoring — no network/ffmpeg."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from thumbnail.thumbnail import (
    _TITLE_HOOK_LEVELS,
    ThumbnailCandidate,
    ThumbnailConfig,
    ThumbnailSkill,
    _select_candidate,
    score_titles,
)

# ---------------------------------------------------------------------------
# Fake classify client
# ---------------------------------------------------------------------------


class _FakeClassifyClient:
    """Minimal stand-in for ``video_editor_llm.ClassifyClient``.

    Answers every ``score`` question from the ``title`` data field so the
    normalisation logic can be exercised without touching the network.
    """

    def __init__(
        self,
        scores: dict[str, float | None] | None = None,
        *,
        default: float | None = 0.0,
        available: bool = True,
        error: bool = False,
    ) -> None:
        self._scores = scores or {}
        self._default = default
        self._available = available
        self._error = error
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def available(self) -> bool:
        return self._available

    async def classify(
        self, state: Any, questions: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(questions)
        if self._error:
            raise RuntimeError("boom")
        answers: dict[str, Any] = {}
        for key, question in questions.items():
            title = str((question.get("instructions") or {}).get("title", ""))
            answers[key] = {
                "type": "score",
                "score": self._scores.get(title, self._default),
                "confidence": 0.9,
            }
        return {"model": "jev-test", "answers": answers, "usage": {}}

    async def close(self) -> None:  # pragma: no cover - exercised via flag
        self.closed = True


# ---------------------------------------------------------------------------
# score_titles (module-level)
# ---------------------------------------------------------------------------


def test_score_titles_normalises_to_unit_range() -> None:
    fake = _FakeClassifyClient({"banger": float(len(_TITLE_HOOK_LEVELS) - 1), "meh": 0.0})
    scores = score_titles(["banger", "meh"], classify_client=fake)

    assert scores == {"banger": 1.0, "meh": 0.0}


def test_score_titles_uses_one_batched_call() -> None:
    fake = _FakeClassifyClient()
    score_titles(["one", "two", "three"], classify_client=fake)

    assert len(fake.calls) == 1, "all titles must collapse into ONE classify call"
    assert len(fake.calls[0]) == 3
    # Every question is a score question carrying the title as data.
    for question in fake.calls[0].values():
        assert question["type"] == "score"
        assert "title" in question["instructions"]


def test_score_titles_deduplicates_and_skips_empty() -> None:
    fake = _FakeClassifyClient()
    score_titles(["same", "same", "", "other"], classify_client=fake)

    assert len(fake.calls[0]) == 2
    assert fake.calls[0].get("t0")["instructions"]["title"] == "same"
    assert fake.calls[0].get("t1")["instructions"]["title"] == "other"


def test_score_titles_empty_input_makes_no_call() -> None:
    fake = _FakeClassifyClient()
    assert score_titles([], classify_client=fake) == {}
    assert score_titles(["", ""], classify_client=fake) == {}
    assert fake.calls == []


def test_score_titles_unavailable_returns_empty() -> None:
    fake = _FakeClassifyClient(available=False)
    assert score_titles(["a"], classify_client=fake) == {}
    assert fake.calls == []


def test_score_titles_skips_non_numeric_answers() -> None:
    fake = _FakeClassifyClient({"good": 4.0, "bad": None}, default=None)
    scores = score_titles(["good", "bad"], classify_client=fake)

    assert scores == {"good": 1.0}


def test_score_titles_swallows_errors() -> None:
    fake = _FakeClassifyClient(error=True)
    assert score_titles(["a", "b"], classify_client=fake) == {}


def test_score_titles_does_not_close_injected_client() -> None:
    fake = _FakeClassifyClient()
    score_titles(["a"], classify_client=fake)
    assert fake.closed is False


# ---------------------------------------------------------------------------
# _select_candidate
# ---------------------------------------------------------------------------


def _cand(cid: str, score: float, title: str = "") -> ThumbnailCandidate:
    return ThumbnailCandidate(
        candidate_id=cid,
        timestamp=score,
        output_path=Path(f"{cid}.jpg"),
        score=score,
        title=title,
    )


def test_select_candidate_without_hooks_matches_max_score() -> None:
    cands = [_cand("a", 0.9, "x"), _cand("b", 0.5, "y")]
    assert _select_candidate(cands, {}, 0.5).candidate_id == "a"


def test_select_candidate_blends_hook_scores() -> None:
    cands = [_cand("a", 0.9, "dull"), _cand("b", 0.6, "banger")]
    hooks = {"dull": 0.0, "banger": 1.0}
    # 0.5*0.9 + 0.5*0.0 = 0.45 vs 0.5*0.6 + 0.5*1.0 = 0.80 -> b wins.
    assert _select_candidate(cands, hooks, 0.5).candidate_id == "b"


def test_select_candidate_untitled_candidates_keep_frame_score() -> None:
    cands = [_cand("a", 0.9), _cand("b", 0.6, "banger")]
    # a has no title -> keeps 0.9; b becomes 0.5*0.6 + 0.5*1.0 = 0.80.
    assert _select_candidate(cands, {"banger": 1.0}, 0.5).candidate_id == "a"


# ---------------------------------------------------------------------------
# Integration through ThumbnailSkill.run()
# ---------------------------------------------------------------------------


class _StubCtx:
    def __init__(self, tmp_path: Path) -> None:
        self.work_dir = tmp_path / "work"
        self.output_dir = tmp_path / "out"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def ensure_dirs(self) -> None:
        for d in (self.work_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


def _fake_subprocess(duration: float = 60.0) -> Callable[..., Any]:
    class _Result:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if cmd and cmd[0] == "ffprobe":
            return _Result(returncode=0, stdout=str(duration))
        if cmd and cmd[0] == "ffmpeg":
            for tok in reversed(cmd):
                if tok.endswith(".jpg") or tok.endswith(".png"):
                    Path(tok).write_bytes(b"\xff\xd8fake")
                    break
        return _Result(returncode=0)

    return fake_run


class _PerTimestampScorer:
    """Titles/scores keyed by candidate timestamp."""

    def __init__(self, meta: dict[float, dict[str, Any]]) -> None:
        self._meta = meta

    def score_candidates(
        self, video_path: Path, candidates: list[tuple[float, Path]]
    ) -> dict[float, dict[str, Any]]:
        return {ts: self._meta[ts] for ts, _ in candidates}


def _seed_video(tmp_path: Path) -> Path:
    p = tmp_path / "final.mp4"
    p.write_bytes(b"fake video")
    return p


def _scorer() -> _PerTimestampScorer:
    # duration 60, candidates 3 -> timestamps 15, 30, 45.
    return _PerTimestampScorer(
        {
            15.0: {"score": 0.9, "title": "frame fifteen", "rationale": "base"},
            30.0: {"score": 0.8, "title": "frame thirty", "rationale": "base"},
            45.0: {"score": 0.7, "title": "frame forty-five", "rationale": "base"},
        }
    )


def test_run_reranks_by_jev_hook_score(tmp_path: Path, monkeypatch: Any) -> None:
    fake = _fake_subprocess()
    monkeypatch.setattr("thumbnail.thumbnail.run_ffmpeg", fake)
    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", lambda _path: 60.0)
    ctx = _StubCtx(tmp_path)
    fake = _FakeClassifyClient({"frame fifteen": 0.0, "frame thirty": 0.0, "frame forty-five": 4.0})
    skill = ThumbnailSkill(
        config=ThumbnailConfig(candidates=3),
        scorer=_scorer(),
        classify_client=fake,
    )
    plan = skill.run(ctx, video_path=_seed_video(tmp_path))

    # frame forty-five: 0.5*0.7 + 0.5*1.0 = 0.85 beats frame fifteen's 0.45.
    assert plan["selected"]["candidate_id"] == "candidate_003"
    assert plan["selected"]["title"] == "frame forty-five"
    assert plan["title_hook_scores"]["frame forty-five"] == 1.0
    assert len(fake.calls) == 1


def test_run_unchanged_when_jev_unavailable(tmp_path: Path, monkeypatch: Any) -> None:
    fake = _fake_subprocess()
    monkeypatch.setattr("thumbnail.thumbnail.run_ffmpeg", fake)
    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", lambda _path: 60.0)
    ctx = _StubCtx(tmp_path)
    fake = _FakeClassifyClient(available=False)
    skill = ThumbnailSkill(
        config=ThumbnailConfig(candidates=3),
        scorer=_scorer(),
        classify_client=fake,
    )
    plan = skill.run(ctx, video_path=_seed_video(tmp_path))

    assert plan["selected"]["candidate_id"] == "candidate_001"  # highest base score
    assert plan["title_hook_scores"] == {}
    assert fake.calls == []


def test_run_makes_no_jev_call_without_titles(tmp_path: Path, monkeypatch: Any) -> None:
    fake = _fake_subprocess()
    monkeypatch.setattr("thumbnail.thumbnail.run_ffmpeg", fake)
    monkeypatch.setattr("thumbnail.thumbnail.probe_duration", lambda _path: 60.0)
    ctx = _StubCtx(tmp_path)
    scorer = _PerTimestampScorer(
        {
            15.0: {"score": 0.9, "title": "", "rationale": "base"},
            30.0: {"score": 0.5, "title": "", "rationale": "base"},
            45.0: {"score": 0.5, "title": "", "rationale": "base"},
        }
    )
    fake = _FakeClassifyClient()
    skill = ThumbnailSkill(
        config=ThumbnailConfig(candidates=3), scorer=scorer, classify_client=fake
    )
    plan = skill.run(ctx, video_path=_seed_video(tmp_path))

    assert plan["selected"]["candidate_id"] == "candidate_001"
    assert plan["title_hook_scores"] == {}
    assert fake.calls == []
