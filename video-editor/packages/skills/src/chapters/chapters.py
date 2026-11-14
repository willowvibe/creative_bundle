"""Chapter generation skill: create YouTube chapter markers from transcripts.

The skill consumes a transcript (segment-level with ``text`` and ``start``) and
produces:

- A JSON file with chapter boundaries, titles, and timestamps.
- A YouTube-formatted ``Chapters`` text block (``0:00 Title``).

Three segmentation backends are supported, in priority order:

1. **LLM topic segmentation**: groups contiguous transcript segments into
   topic clusters via an LLM call (used only when an ``llm_client`` is given).
2. **Jev topic segmentation** (``classify_client``): one batched classifier
   call judges whether each segment starts a new topic.
3. **Scene detection fallback**: simple boundary detection based on silence
   gaps or long pauses between segments.

The skill is deterministic except for the classifier calls, which can be
mocked in tests.  The output is safe to re-run; previous chapter files are
overwritten.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_llm import ClassifyClient, question_noul, question_score, run_sync
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

DEFAULT_MIN_CHAPTER_DURATION = 30.0
DEFAULT_MAX_CHAPTERS = 20
DEFAULT_JEV_BOUNDARY_THRESHOLD = 0.5

# Ordered rating levels for the optional ``score_title_options`` helper.
_TITLE_HOOK_LEVELS = [
    "flat - no reason to click or keep watching",
    "weak - generic or slow, little pull",
    "fair - clear enough but unremarkable",
    "strong - specific and intriguing",
    "irresistible - a concrete promise you want to see",
]


def _format_timestamp(seconds: float) -> str:
    """Format seconds as ``H:MM:SS`` or ``M:SS`` for YouTube chapters."""
    seconds = max(0.0, seconds)
    total_seconds = int(round(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _merge_chapter_spans(
    segments: list[dict[str, Any]],
    boundaries: list[int],
    *,
    min_duration: float,
    max_chapters: int,
) -> list[tuple[int, int]]:
    """Merge chapter boundaries into ``(start_idx, end_idx)`` spans.

    ``boundaries`` are sorted segment start indices with a trailing
    ``len(segments)`` sentinel (so the first boundary is always ``0``).
    Spans shorter than ``min_duration`` merge forward into the next span;
    when more than ``max_chapters`` remain, the adjacent pair with the
    smallest combined duration merges until the cap is met.
    """
    merged: list[tuple[int, int]] = []
    current_start = boundaries[0]
    for idx in range(1, len(boundaries)):
        end = boundaries[idx]
        start_time = segments[current_start].get("start", 0.0)
        end_time = segments[end - 1].get("end", segments[end - 1].get("start", 0.0))
        duration = end_time - start_time

        if duration < min_duration and idx < len(boundaries) - 1:
            continue
        merged.append((current_start, end))
        current_start = end

    while len(merged) > max_chapters and len(merged) > 1:
        # Combine the pair with the smallest combined duration.
        smallest_idx = 0
        smallest_duration = float("inf")
        for idx in range(len(merged) - 1):
            start_a = merged[idx][0]
            end_b = merged[idx + 1][1]
            duration = segments[end_b - 1].get(
                "end", segments[end_b - 1].get("start", 0.0)
            ) - segments[start_a].get("start", 0.0)
            if duration < smallest_duration:
                smallest_duration = duration
                smallest_idx = idx
        merged[smallest_idx] = (merged[smallest_idx][0], merged[smallest_idx + 1][1])
        merged.pop(smallest_idx + 1)

    return merged


def _chapters_from_spans(
    segments: list[dict[str, Any]],
    spans: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    """Turn ``(start_idx, end_idx)`` spans into chapter dicts.

    Titles come from the deterministic ``_infer_title_from_text`` heuristic
    (never from a generated model).
    """
    chapters: list[dict[str, Any]] = []
    for start_idx, end_idx in spans:
        start_time = segments[start_idx].get("start", 0.0)
        texts = [s.get("text", "").strip() for s in segments[start_idx:end_idx] if s.get("text")]
        title = _infer_title_from_text(texts) if texts else f"Chapter {len(chapters) + 1}"
        chapters.append(
            {
                "start": round(start_time, 3),
                "title": title,
                "text_preview": " ".join(texts)[:120],
            }
        )
    return chapters


def _segment_chapters_by_gaps(
    segments: list[dict[str, Any]],
    gap_threshold: float = 2.0,
    min_duration: float = DEFAULT_MIN_CHAPTER_DURATION,
    max_chapters: int = DEFAULT_MAX_CHAPTERS,
) -> list[dict[str, Any]]:
    """Fallback chapter detection using pause gaps and minimum duration.

    Splits at gaps larger than ``gap_threshold`` seconds, then merges small
    chapters until each is at least ``min_duration`` long or the maximum
    chapter count is reached.
    """
    if not segments:
        return []

    # Initial boundaries at large gaps.
    boundaries: list[int] = [0]
    for idx in range(1, len(segments)):
        prev_end = segments[idx - 1].get("end", segments[idx - 1].get("start", 0.0))
        curr_start = segments[idx].get("start", 0.0)
        if curr_start - prev_end >= gap_threshold:
            boundaries.append(idx)
    boundaries.append(len(segments))

    spans = _merge_chapter_spans(
        segments,
        boundaries,
        min_duration=min_duration,
        max_chapters=max_chapters,
    )
    return _chapters_from_spans(segments, spans)


def _infer_title_from_text(texts: list[str]) -> str:
    """Infer a short chapter title from the first few segment texts."""
    if not texts:
        return "Intro"
    combined = " ".join(texts[:3])
    # Simple heuristic: take the first sentence or first 40 chars.
    sentence = combined.split(".")[0].strip()
    if len(sentence) > 50:
        words = sentence.split()
        sentence = " ".join(words[:6]) + "..."
    if not sentence:
        return "Chapter"
    return sentence[:50]


class ChapterSkill:
    """Generate YouTube chapter markers from a transcript."""

    @property
    def name(self) -> str:
        return "chapters"

    def __init__(
        self,
        ctx: JobContext,
        *,
        min_chapter_duration: float = DEFAULT_MIN_CHAPTER_DURATION,
        max_chapters: int = DEFAULT_MAX_CHAPTERS,
        llm_client: Any | None = None,
        classify_client: ClassifyClient | None = None,
        jev_boundary_threshold: float = DEFAULT_JEV_BOUNDARY_THRESHOLD,
    ):
        self.ctx = ctx
        self.min_chapter_duration = min_chapter_duration
        self.max_chapters = max_chapters
        self.llm_client = llm_client
        self.classify_client = classify_client
        self.jev_boundary_threshold = jev_boundary_threshold
        self.output_dir = ctx.work_dir / "chapters"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _llm_segment_chapters(
        self,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Ask an LLM to segment the transcript into chapters.

        Returns chapter list on success, or ``None`` to fall back to gap
        detection.  When no LLM client is provided, returns ``None``
        immediately.
        """
        if self.llm_client is None:
            return None

        if len(segments) <= 3:
            # Too short for LLM segmentation; use fallback.
            return None

        prompt = self._build_segmentation_prompt(segments)
        try:
            response = self.llm_client.complete(prompt)
            chapters = self._parse_llm_response(response, segments)
            if chapters:
                return chapters
        except Exception as exc:  # noqa: BLE001 - optional chapter metadata
            logger.warning("llm_chapter_segmentation_failed", error=str(exc))
        return None

    def _build_segmentation_prompt(self, segments: list[dict[str, Any]]) -> str:
        """Build a prompt asking the LLM for chapter boundaries."""
        lines: list[str] = []
        for seg in segments:
            start = seg.get("start", 0.0)
            text = seg.get("text", "").strip()
            if text:
                lines.append(f"[{start:.1f}] {text}")
        transcript_text = "\n".join(lines)

        return (
            "Divide the following video transcript into YouTube chapters.\n"
            "Each chapter should have a start timestamp (seconds) and a short title (1-5 words).\n"
            "Chapters must be at least 30 seconds apart and cover the full video.\n"
            "The first chapter must start at 0.0 and be titled 'Intro'.\n"
            "Respond ONLY with a JSON array like:\n"
            '[{"start": 0.0, "title": "Intro"}, {"start": 45.2, "title": "Setup"}]\n\n'
            f"{transcript_text}\n"
        )

    def _parse_llm_response(
        self,
        response: str,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Parse the LLM's JSON chapter response."""
        # Extract JSON from a code block if necessary.
        cleaned = response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[-2].strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, list):
            return None

        chapters: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            start = float(item.get("start", 0.0))
            title = str(item.get("title", "Chapter")).strip()
            chapters.append({"start": round(start, 3), "title": title})

        # Validate: first chapter should be near 0, and chapters increasing.
        if not chapters or chapters[0]["start"] > 5.0:
            return None

        # Clamp to video duration and de-duplicate.
        max_start = segments[-1].get("end", segments[-1].get("start", 0.0)) if segments else 0.0
        seen: set[float] = set()
        valid: list[dict[str, Any]] = []
        for ch in chapters:
            if ch["start"] > max_start:
                continue
            key = round(ch["start"], 1)
            if key in seen:
                continue
            seen.add(key)
            valid.append(ch)

        return valid if valid else None

    def _jev_segment_chapters(
        self,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Segment the transcript with one batched Jev classify call.

        Every segment from the second onward is judged exactly once: does it
        start a new topic distinct from the preceding segment? Flagged
        segments become chapter boundaries (chapter 1 always starts at the
        first segment); too-short chapters are then merged and the chapter
        count is capped. Titles reuse the deterministic
        ``_infer_title_from_text`` heuristic — Jev only decides *where* the
        boundaries are.

        The whole call (client build + classify + close) runs inside one
        coroutine so the async HTTP client never crosses event loops.

        Returns ``None`` (so the caller falls back) when no classifier is
        available, the call errors, or no boundary is found.
        """
        if len(segments) < 2:
            return None

        async def _classify() -> list[int] | None:
            active = self.classify_client
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

            questions: dict[str, Any] = {}
            candidate_indices: list[int] = []
            for idx in range(1, len(segments)):
                text = str(segments[idx].get("text", "")).strip()
                if not text:
                    continue
                preceding = str(segments[idx - 1].get("text", "")).strip()
                candidate_indices.append(idx)
                questions[f"s{idx}"] = question_noul(
                    "Does `segment` start a new topic that is distinct from the "
                    "preceding `preceding` segment?",
                    data={"segment": text, "preceding": preceding},
                    true_label="New topic",
                    false_label="Same topic",
                )
            if not questions:
                if owns_client:
                    await active.close()
                return None

            try:
                body = await active.classify({"state": "Chapter boundary review."}, questions)
            finally:
                if owns_client:
                    await active.close()

            answers = body.get("answers") or {}
            boundaries: list[int] = []
            for idx in candidate_indices:
                prob = float((answers.get(f"s{idx}") or {}).get("noul", 0.0) or 0.0)
                if prob >= self.jev_boundary_threshold:
                    boundaries.append(idx)
            return boundaries

        try:
            boundaries = run_sync(_classify())
        except Exception as exc:  # noqa: BLE001 - any classify error → fall back
            logger.warning("chapters_jev_segmentation_failed", error=str(exc))
            return None
        if not boundaries:
            return None

        spans = _merge_chapter_spans(
            segments,
            [0, *boundaries, len(segments)],
            min_duration=self.min_chapter_duration,
            max_chapters=self.max_chapters,
        )
        chapters = _chapters_from_spans(segments, spans)
        if not chapters:
            return None
        logger.info(
            "chapter_jev_segmentation",
            count=len(chapters),
            boundaries=len(boundaries),
        )
        return chapters

    def score_title_options(self, titles: list[str]) -> dict[str, float]:
        """Score chapter titles for hook clarity with one batched Jev call.

        This is a helper only — ``run()`` never calls it. The returned scores
        are normalised to ``0.0..1.0`` (``0.0`` when the classifier returned no
        score for a title). Titles that are blank are dropped; an unavailable
        or erroring classifier yields an empty dict.
        """
        cleaned = [title.strip() for title in titles if title and title.strip()]
        if not cleaned:
            return {}

        async def _score() -> list[dict[str, Any]] | None:
            active = self.classify_client
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
            questions: dict[str, Any] = {
                f"t{i}": question_score(
                    "How clear and compelling is `title` as a chapter title?",
                    _TITLE_HOOK_LEVELS,
                    data={"title": title},
                )
                for i, title in enumerate(cleaned)
            }
            try:
                body = await active.classify({"state": "Chapter title review."}, questions)
            finally:
                if owns_client:
                    await active.close()
            answers = body.get("answers") or {}
            return [answers.get(f"t{i}") or {} for i in range(len(cleaned))]

        try:
            answers = run_sync(_score())
        except Exception as exc:  # noqa: BLE001 - any classify error → no scores
            logger.warning("chapters_jev_title_scoring_failed", error=str(exc))
            return {}
        if answers is None:
            return {}

        span = max(len(_TITLE_HOOK_LEVELS) - 1, 1)
        scored: dict[str, float] = {}
        for title, answer in zip(cleaned, answers, strict=False):
            raw = answer.get("score") if isinstance(answer, dict) else None
            value = float(raw) / span if isinstance(raw, (int, float)) else 0.0
            scored[title] = max(0.0, min(1.0, value))
        return scored

    def _write_outputs(
        self,
        chapters: list[dict[str, Any]],
    ) -> tuple[Path, Path]:
        """Write JSON and YouTube text chapter files."""
        json_path = self.output_dir / "chapters.json"
        txt_path = self.output_dir / "chapters.txt"

        json_path.write_text(
            json.dumps(
                {
                    "job_id": self.ctx.job_id,
                    "count": len(chapters),
                    "chapters": chapters,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        lines = [f"{_format_timestamp(ch['start'])} {ch['title']}" for ch in chapters]
        txt_path.write_text("\n".join(lines), encoding="utf-8")

        return json_path, txt_path

    def run(
        self,
        transcript: dict[str, Any],
        *,
        duration: float | None = None,
    ) -> dict[str, Any]:
        """Generate chapter markers from a transcript.

        Args:
            transcript: Dict with ``segments`` list of dicts containing
                ``text`` and ``start`` (and optionally ``end``).
            duration: Optional total video duration in seconds.

        Returns:
            Skill output dict with ``chapters_json``, ``chapters_txt``, and
            the chapter list.
        """
        self.ctx.ensure_dirs()
        segments = transcript.get("segments") or []
        if not segments:
            raise ValueError("transcript must contain non-empty 'segments'")

        # Precedence: LLM topic segmentation → Jev topic segmentation →
        # deterministic gap fallback.
        chapters = self._llm_segment_chapters(segments)
        if chapters is not None:
            logger.info("chapter_llm_segmentation", count=len(chapters))
        else:
            chapters = self._jev_segment_chapters(segments)
            if chapters is None:
                chapters = _segment_chapters_by_gaps(
                    segments,
                    min_duration=self.min_chapter_duration,
                    max_chapters=self.max_chapters,
                )
                logger.info("chapter_fallback_gap_segmentation", count=len(chapters))

        if duration is not None and chapters:
            # Clamp last chapter duration and remove chapters past duration.
            chapters = [ch for ch in chapters if ch["start"] <= duration]

        json_path, txt_path = self._write_outputs(chapters)

        return {
            "chapters_json": str(json_path),
            "chapters_txt": str(txt_path),
            "chapters": chapters,
            "count": len(chapters),
        }
