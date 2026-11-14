"""Tests for the Jev (TypeSafe) Shorts moment scorer — no network."""

from __future__ import annotations

from typing import Any

from shorts_factory.jev_scorer import JevMomentScorer


class _FakeClassifyClient:
    """Minimal stand-in for ``video_editor_llm.ClassifyClient``."""

    def __init__(self, *, score: float = 4.0, choice: str = "hook", available: bool = True) -> None:
        self._score = score
        self._choice = choice
        self._available = available
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def available(self) -> bool:
        return self._available

    async def classify(self, state: Any, questions: dict[str, Any], **kw: Any) -> dict[str, Any]:
        self.calls.append(questions)
        answers: dict[str, Any] = {}
        for key in questions:
            if key.startswith("score"):
                answers[key] = {"type": "score", "score": self._score, "confidence": 0.9}
            else:
                answers[key] = {"type": "choice", "choice": self._choice, "confidence": 0.8}
        return {"model": "jev-test", "answers": answers, "usage": {}}

    async def close(self) -> None:
        self.closed = True


def _transcript() -> list[dict[str, Any]]:
    return [
        {"start": 0.0, "end": 5.0, "text": "Welcome back to the channel."},
        {"start": 5.0, "end": 10.0, "text": "The truth is most teams get this wrong."},
        {"start": 10.0, "end": 15.0, "text": "So here is the exact system I used."},
    ]


def test_score_moments_maps_score_title_rationale():
    fake = _FakeClassifyClient(score=4.0, choice="hook")
    scorer = JevMomentScorer(client=fake)

    result = scorer.score_moments(_transcript(), [(0.0, 10.0), (5.0, 15.0)])

    assert set(result) == {(0.0, 10.0), (5.0, 15.0)}
    first = result[(0.0, 10.0)]
    assert first["score"] == 1.0  # 4 / (5 levels - 1)
    assert first["rationale"].startswith("jev:hook")
    assert first["title"]


def test_score_moments_batches_all_candidates_into_one_call():
    fake = _FakeClassifyClient()
    scorer = JevMomentScorer(client=fake, batch_size=40)

    candidates = [(float(i), float(i + 5)) for i in range(6)]
    scorer.score_moments(_transcript(), candidates)

    assert len(fake.calls) == 1, "all candidates should collapse into ONE classify call"
    # two questions (score + type) per candidate
    assert len(fake.calls[0]) == 12


def test_score_moments_chunks_by_batch_size():
    fake = _FakeClassifyClient()
    scorer = JevMomentScorer(client=fake, batch_size=2)

    candidates = [(float(i), float(i + 5)) for i in range(5)]
    scorer.score_moments(_transcript(), candidates)

    assert len(fake.calls) == 3  # 2 + 2 + 1


def test_does_not_close_injected_client():
    fake = _FakeClassifyClient()
    JevMomentScorer(client=fake).score_moments(_transcript(), [(0.0, 5.0)])
    assert fake.closed is False


def test_available_reflects_client():
    assert JevMomentScorer(client=_FakeClassifyClient(available=True)).available() is True
    assert JevMomentScorer(client=_FakeClassifyClient(available=False)).available() is False
