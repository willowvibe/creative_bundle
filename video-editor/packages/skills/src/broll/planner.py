"""B-roll moment planning: find spoken moments that need visual coverage.

The planner turns a transcript into a list of B-roll moments. Each moment
has a timeline window (start/end), a human description, and a search query
derived from the spoken nouns/verbs. When an LLM router is available the
moments are extracted with a JSON-instructed prompt; otherwise a
deterministic fallback splits the transcript on segment boundaries and uses
a lightweight noun/phrase heuristic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog
from video_editor_llm import ClassifyClient, question_score
from video_editor_llm.router import ModelRouter

logger = structlog.get_logger(__name__)

DEFAULT_MIN_MOMENT_DURATION = 2.5
DEFAULT_MAX_MOMENT_DURATION = 6.0
DEFAULT_COVERAGE_FRACTION = 0.4

# Words that rarely make good visual search queries on their own.
_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "so",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "it",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "i",
    "you",
    "we",
    "they",
    "he",
    "she",
    "this",
    "that",
    "these",
    "those",
    "like",
    "just",
    "really",
    "very",
    "okay",
    "ok",
    "yeah",
    "um",
    "uh",
    "gonna",
    "wanna",
    "know",
    "think",
    "mean",
    "right",
    "about",
    "there",
    "here",
    "then",
    "when",
    "what",
    "who",
    "how",
    "why",
    "get",
    "got",
    "make",
    "see",
    "want",
    "go",
    "going",
    "say",
    "said",
    "one",
    "two",
}


@dataclass(frozen=True)
class BrollMoment:
    """A single B-roll insertion window."""

    start: float
    end: float
    description: str = ""
    query: str = ""
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "description": self.description,
            "query": self.query,
            "provider": self.provider,
        }


@dataclass
class _Draft:
    """Internal moment candidate before de-duplication."""

    start: float
    end: float
    description: str
    query: str


def _clean_query(text: str, max_words: int = 4) -> str:
    """Turn a phrase into a short stock-search query of salient nouns."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text.lower())
    filtered = [w for w in words if w not in _STOPWORDS and len(w) > 2]
    if not filtered:
        filtered = words[:max_words]
    if not filtered:
        return "b-roll"
    return " ".join(filtered[:max_words])


def _iter_segments(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of segment dicts with start/end/text keys."""
    if "segments" in transcript:
        segments = transcript["segments"]
        if segments and "text" in segments[0]:
            return list(segments)
    words = transcript.get("words") or transcript.get("word_segments") or []
    if not words:
        return []
    # Group words into ~4s pseudo-segments when only word-level data exists.
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    window_start = 0.0
    window_end = 0.0
    for word in words:
        start = float(word.get("start", 0.0))
        end = float(word.get("end", start + 0.3))
        if not current:
            window_start = start
        if start - window_start >= 4.0 and current:
            groups.append(
                {
                    "start": window_start,
                    "end": window_end,
                    "text": " ".join(w.get("word", "") for w in current),
                }
            )
            current = []
            window_start = start
        current.append(word)
        window_end = end
    if current:
        groups.append(
            {
                "start": window_start,
                "end": window_end,
                "text": " ".join(w.get("word", "") for w in current),
            }
        )
    return groups


def _drafts_from_segments(
    segments: list[dict[str, Any]],
    *,
    min_duration: float,
    max_duration: float,
    coverage_fraction: float,
) -> list[_Draft]:
    """Deterministic fallback: cover a fraction of long segments."""
    drafts: list[_Draft] = []
    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 5.0))
        duration = end - start
        if duration < min_duration * 2:
            continue
        coverage = min(max_duration, duration * coverage_fraction)
        if coverage < min_duration:
            continue
        # Insert roughly in the middle so the talking head is not hidden
        # at the start of the segment.
        offset = (duration - coverage) / 2
        m_start = start + offset
        m_end = m_start + coverage
        text = seg.get("text", "")
        drafts.append(
            _Draft(
                start=m_start,
                end=m_end,
                description=(text.strip()[:80] or "B-roll moment"),
                query=_clean_query(text),
            )
        )
    return drafts


def _dedupe(drafts: list[_Draft], gap: float = 1.5) -> list[BrollMoment]:
    """Merge overlapping drafts and require a minimum gap between moments."""
    ordered = sorted(drafts, key=lambda d: d.start)
    moments: list[BrollMoment] = []
    last_end = float("-inf")
    for draft in ordered:
        if draft.start < last_end + gap:
            continue
        moments.append(
            BrollMoment(
                start=round(draft.start, 3),
                end=round(draft.end, 3),
                description=draft.description,
                query=draft.query,
            )
        )
        last_end = draft.end
    return moments


async def _extract_with_llm(
    router: ModelRouter,
    transcript: dict[str, Any],
    *,
    min_duration: float,
    max_duration: float,
) -> list[BrollMoment] | None:
    """Ask the LLM to propose B-roll moments as JSON, or return None on failure."""
    lines = []
    for seg in _iter_segments(transcript):
        lines.append(
            f"[{seg.get('start', 0.0):.2f}-{seg.get('end', 0.0):.2f}] {seg.get('text', '')}"
        )
    transcript_text = "\n".join(lines) or ""

    system = (
        "You are a video editor selecting B-roll coverage. Return ONLY valid JSON: "
        "a list of objects with keys start, end, description, query. "
        "start/end are seconds within the video; description is a short "
        "human label; query is a short stock-video search phrase (3-4 nouns). "
        f"Each moment must be between {min_duration:.1f} and {max_duration:.1f}s long. "
        "Do not invent timestamps outside the transcript range."
    )
    try:
        raw = await router.generate(
            task_type="utility",
            prompt=(
                "Transcript segments (start-end | text):\n"
                f"{transcript_text}\n\n"
                "Propose 0-8 B-roll moments as JSON."
            ),
            system=system,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("broll_llm_extraction_failed", error=str(exc))
        return None

    try:
        text = raw.strip()
        text = text[text.find("[") : text.rfind("]") + 1]
        payload = json.loads(text)
        moments: list[BrollMoment] = []
        for item in payload:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start + min_duration))
            if end <= start:
                end = start + min_duration
            moments.append(
                BrollMoment(
                    start=round(start, 3),
                    end=round(end, 3),
                    description=item.get("description", ""),
                    query=item.get("query", "") or _clean_query(item.get("description", "")),
                )
            )
        return moments
    except Exception as exc:  # noqa: BLE001
        logger.warning("broll_llm_parse_failed", error=str(exc))
        return None


_DRIVE_LEVELS: list[str] = [
    "no b-roll needed - abstract or purely verbal",
    "weak - a visual would not add much",
    "some value - a supporting visual could help",
    "good moment - a concrete subject worth showing",
    "strong visual moment - a specific thing, place, or action",
]


async def _extract_with_jev(
    transcript: dict[str, Any],
    *,
    client: ClassifyClient | None = None,
    min_duration: float,
    max_duration: float,
    top_n: int = 8,
    min_score: float = 0.4,
) -> list[BrollMoment] | None:
    """Pick B-roll moments with one batched Jev classify call over all
    segments; queries come from the deterministic ``_clean_query`` heuristic.

    Returns ``None`` when no classifier is available (caller falls back to the
    LLM or heuristic paths); returns ``[]`` when the classifier ran and found
    nothing worth covering.
    """
    segments = _iter_segments(transcript)
    items = [
        (float(s.get("start", 0.0)), float(s.get("end", 0.0)), str(s.get("text", "")).strip())
        for s in segments
    ]
    items = [(a, b, t) for a, b, t in items if t]
    if not items:
        return None

    active = client
    owns_client = active is None
    if active is None:
        try:
            active = ClassifyClient.from_config()
        except Exception:  # noqa: BLE001 - config probing must not raise
            active = ClassifyClient()
    if not active.available():
        if owns_client:
            await active.close()
        return None
    try:
        answers = await active.classify_batch(
            [text for _, _, text in items],
            lambda text: question_score(
                "How good is `segment` as a spoken moment to cover with "
                "illustrative B-roll footage?",
                _DRIVE_LEVELS,
                data={"segment": text},
            ),
        )
    finally:
        if owns_client:
            await active.close()

    span = max(len(_DRIVE_LEVELS) - 1, 1)
    scored: list[tuple[float, float, float, str]] = []
    for (start, end, text), answer in zip(items, answers, strict=False):
        raw = answer.get("score") if isinstance(answer, dict) else None
        score = float(raw) / span if isinstance(raw, (int, float)) else 0.0
        if score >= min_score:
            scored.append((score, start, end, text))

    scored.sort(key=lambda t: t[0], reverse=True)
    drafts: list[_Draft] = []
    for _score, start, end, text in scored[:top_n]:
        moment_end = min(end, start + max_duration) if end > start else start + min_duration
        if moment_end - start < min_duration:
            moment_end = start + min_duration
        drafts.append(
            _Draft(
                start=round(start, 3),
                end=round(moment_end, 3),
                description=text[:80],
                query=_clean_query(text),
            )
        )
    return _dedupe(drafts)


def extract_moments(
    transcript: dict[str, Any],
    *,
    min_duration: float = DEFAULT_MIN_MOMENT_DURATION,
    max_duration: float = DEFAULT_MAX_MOMENT_DURATION,
    coverage_fraction: float = DEFAULT_COVERAGE_FRACTION,
) -> list[BrollMoment]:
    """Extract B-roll moments from a transcript using deterministic heuristics."""
    segments = _iter_segments(transcript)
    drafts = _drafts_from_segments(
        segments,
        min_duration=min_duration,
        max_duration=max_duration,
        coverage_fraction=coverage_fraction,
    )
    return _dedupe(drafts)


async def extract_moments_async(
    transcript: dict[str, Any],
    *,
    router: ModelRouter | None = None,
    classify_client: ClassifyClient | None = None,
    min_duration: float = DEFAULT_MIN_MOMENT_DURATION,
    max_duration: float = DEFAULT_MAX_MOMENT_DURATION,
    coverage_fraction: float = DEFAULT_COVERAGE_FRACTION,
) -> list[BrollMoment]:
    """Extract B-roll moments, preferring the cheapest available planner.

    Order: batched Jev classifier → LLM extraction (when a router is given) →
    deterministic segment heuristics.
    """
    jev_moments = await _extract_with_jev(
        transcript,
        client=classify_client,
        min_duration=min_duration,
        max_duration=max_duration,
    )
    if jev_moments is not None:
        return jev_moments

    if router is not None:
        llm_moments = await _extract_with_llm(
            router, transcript, min_duration=min_duration, max_duration=max_duration
        )
        if llm_moments:
            return llm_moments

    return extract_moments(
        transcript,
        min_duration=min_duration,
        max_duration=max_duration,
        coverage_fraction=coverage_fraction,
    )
