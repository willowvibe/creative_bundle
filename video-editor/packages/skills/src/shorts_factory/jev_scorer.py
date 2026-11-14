"""TypeSafe/Jev-backed moment scorer for the Shorts factory.

Scores every candidate window for short-form *hook strength* and classifies
its moment type, all in a handful of batched classifier calls (one request per
chunk of candidates) instead of per-window LLM calls. Falls back cleanly: the
caller keeps ``_HeuristicScorer`` whenever this scorer is unavailable.
"""

from __future__ import annotations

from typing import Any

from video_editor_llm.classify import (
    ClassifyClient,
    question_choice,
    question_score,
    run_sync,
)

_SCORE_LEVELS: list[str] = [
    "no hook - nothing grabs attention",
    "weak - generic or slow start",
    "fair - some interest but not gripping",
    "strong - clear hook that makes you keep watching",
    "irresistible - an immediate, compelling reason to watch",
]

_TYPE_CRITERIA: dict[str, str] = {
    "hook": "An attention-grabbing opening claim or question.",
    "story": "A personal story or narrative beat.",
    "insight": "A concrete tip, lesson, or how-to.",
    "conflict": "Tension, failure, or a contrarian take.",
    "cta": "A call to action or outro.",
    "filler": "Rambling, housekeeping, or low-value content.",
}


def _norm_score(answer: dict[str, Any]) -> float:
    raw = answer.get("score")
    if not isinstance(raw, (int, float)):
        return 0.0
    span = max(len(_SCORE_LEVELS) - 1, 1)
    return max(0.0, min(1.0, float(raw) / span))


class JevMomentScorer:
    """Batched Jev scorer implementing the ``MomentScorer`` protocol."""

    name = "jev"

    def __init__(self, client: ClassifyClient | None = None, *, batch_size: int = 40) -> None:
        self._client = client
        self.batch_size = batch_size

    def available(self) -> bool:
        if self._client is not None:
            return self._client.available()
        try:
            return ClassifyClient.from_config().available()
        except Exception:  # noqa: BLE001 - config/env probing must not raise
            return ClassifyClient().available()

    async def score_moments_async(
        self,
        transcript: list[dict[str, Any]],
        candidates: list[tuple[float, float]],
    ) -> dict[tuple[float, float], dict[str, Any]]:
        from shorts_factory.shorts_factory import _first_sentence, _window_text

        client = self._client or ClassifyClient.from_config()
        owns_client = self._client is None
        scored: dict[tuple[float, float], dict[str, Any]] = {}
        try:
            for start_ix in range(0, len(candidates), self.batch_size):
                chunk = candidates[start_ix : start_ix + self.batch_size]
                questions: dict[str, Any] = {}
                texts: list[str] = []
                for j, (start, end) in enumerate(chunk):
                    text = _window_text(transcript, start, end)[:1500]
                    texts.append(text)
                    questions[f"score{j}"] = question_score(
                        "How strong a short-form video hook is `excerpt`?",
                        _SCORE_LEVELS,
                        data={"excerpt": text},
                    )
                    questions[f"type{j}"] = question_choice(
                        "What kind of moment is `excerpt`?",
                        _TYPE_CRITERIA,
                        data={"excerpt": text},
                    )
                body = await client.classify({"state": "Score candidate Shorts."}, questions)
                answers = body.get("answers") or {}
                for j, (start, end) in enumerate(chunk):
                    score_answer = answers.get(f"score{j}") or {}
                    type_answer = answers.get(f"type{j}") or {}
                    label = (
                        str(type_answer.get("choice", "")) if isinstance(type_answer, dict) else ""
                    )
                    conf = type_answer.get("confidence") if isinstance(type_answer, dict) else None
                    rationale = f"jev:{label or 'unknown'}"
                    if isinstance(conf, (int, float)):
                        rationale += f" ({float(conf):.2f})"
                    scored[(start, end)] = {
                        "score": _norm_score(
                            score_answer if isinstance(score_answer, dict) else {}
                        ),
                        "title": _first_sentence(texts[j]),
                        "rationale": rationale,
                    }
            return scored
        finally:
            if owns_client:
                await client.close()

    def score_moments(
        self,
        transcript: list[dict[str, Any]],
        candidates: list[tuple[float, float]],
    ) -> dict[tuple[float, float], dict[str, Any]]:
        return run_sync(self.score_moments_async(transcript, candidates))
