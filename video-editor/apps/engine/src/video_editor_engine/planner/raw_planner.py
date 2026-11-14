"""Raw footage edit planner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.models.edit_plan import BRollInsert, Cut, EditPlan
from video_editor_llm.router import ModelRouter

logger = structlog.get_logger(__name__)


def _merge_regions(regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or adjacent regions."""
    if not regions:
        return []
    regions = sorted(regions)
    merged: list[tuple[float, float]] = [regions[0]]
    for start, end in regions[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _build_default_broll(cuts: list[Cut]) -> list[BRollInsert]:
    """Add a short placeholder b-roll insert to any kept segment > 6 seconds."""
    inserts: list[BRollInsert] = []
    for cut in cuts:
        duration = cut.end - cut.start
        if duration > 6:
            start = cut.start + duration * 0.2
            end = min(cut.end - 0.5, start + 3.0)
            if end > start:
                inserts.append(
                    BRollInsert(
                        start=start,
                        end=end,
                        description="Auto b-roll placeholder",
                        asset_query="placeholder",
                        provider="placeholder",
                    )
                )
    return inserts


def _build_default_cuts(
    transcript_path: Path,
    silence_path: Path,
    filler_path: Path,
    pauses_path: Path | None = None,
    target_duration: float | None = None,
) -> list[Cut]:
    """Fallback deterministic planner: remove silences, pauses, and filler words."""
    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    bad_regions: list[tuple[float, float]] = []

    if silence_path.exists():
        with open(silence_path, encoding="utf-8") as f:
            silence = json.load(f)
        for region in silence.get("silences", []):
            bad_regions.append((region["start"], region["end"]))

    if pauses_path and pauses_path.exists():
        with open(pauses_path, encoding="utf-8") as f:
            pauses = json.load(f)
        for pause in pauses.get("pauses", []):
            # Keep a small breathing room at the start of the pause.
            keep = 0.15
            if pause["duration"] > keep:
                bad_regions.append((pause["start"] + keep, pause["end"]))

    if filler_path.exists():
        with open(filler_path, encoding="utf-8") as f:
            filler = json.load(f)
        for match in filler.get("matches", []):
            bad_regions.append((match["start"], match["end"]))

    merged = _merge_regions(bad_regions)

    words = transcript.get("words", [])
    if not words:
        return []

    video_start = words[0]["start"]
    video_end = words[-1]["end"]

    cuts = []
    cursor = video_start
    for start, end in merged:
        if cursor < start:
            cuts.append(Cut(start=cursor, end=start, reason="keep speech"))
        cursor = max(cursor, end)
    if cursor < video_end:
        cuts.append(Cut(start=cursor, end=video_end, reason="keep speech"))

    # Optional jump-cut tightening: if target duration is set and we are still over,
    # we would apply additional heuristics here. For v1 we leave cuts as-is.
    return cuts


def _format_transcript_for_llm(transcript: dict[str, Any]) -> str:
    """Render a compact, timestamped transcript for LLM planning."""
    lines = []
    for seg in transcript.get("segments", []):
        start = seg["start"]
        end = seg["end"]
        text = seg["text"].strip()
        lines.append(f"[{start:.2f} - {end:.2f}] {text}")
    return "\n".join(lines)


async def plan_raw_edit(
    job_id: str,
    transcript_path: Path,
    silence_path: Path,
    filler_path: Path,
    output_path: Path,
    pauses_path: Path | None = None,
    llm_router: ModelRouter | None = None,
    target_duration: float | None = None,
    use_llm: bool = True,
) -> EditPlan:
    """Generate an edit plan.

    Uses LLM if router is provided and use_llm=True,
    otherwise falls back to deterministic silence/pause/filler removal.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)
    silence_text = silence_path.read_text(encoding="utf-8") if silence_path.exists() else "{}"
    filler_text = filler_path.read_text(encoding="utf-8") if filler_path.exists() else "{}"
    pauses_text = (
        pauses_path.read_text(encoding="utf-8") if pauses_path and pauses_path.exists() else "{}"
    )

    if llm_router is None or not use_llm:
        logger.info("using_deterministic_planner", job_id=job_id)
        cuts = _build_default_cuts(
            transcript_path, silence_path, filler_path, pauses_path, target_duration
        )
        plan = EditPlan(
            job_id=job_id,
            cuts=cuts,
            broll_inserts=_build_default_broll(cuts),
            summary="Deterministic silence, pause, and filler removal",
        )
    else:
        transcript_for_llm = _format_transcript_for_llm(transcript)
        prompt = f"""You are an expert video editor. Given the transcript, silence regions, pauses, and filler words, propose a concise edit plan.
Return ONLY valid JSON matching this schema:
{{"cuts": [{{"start": float, "end": float, "reason": string}}], "broll_inserts": [{{"start": float, "end": float, "description": string, "asset_query": string, "provider": string}}], "summary": string, "caption_style": {{"font_size": int, "font_color": string, "border_width": int, "position": string}}}}

Rules:
- Each cut is a KEPT segment, not a removed segment.
- Remove long silences (>1s) and filler words ("um", "uh", "so", etc.).
- Keep short natural pauses (<0.5s) for breathing.
- Do not produce cuts that overlap or go outside the video duration.
- The first cut should start at 0.0 or after the first removed region.
- The last cut should end at the video duration or before the last removed region.
- Add 1-3 short b-roll inserts over long kept segments (>6s) to cover visual gaps.
- Leave ``provider`` empty unless you explicitly want a generated clip; the pipeline defaults to placeholder overlays to avoid accidental cloud spend.

Transcript:
{transcript_for_llm[:3000]}

Silence regions:
{silence_text[:1500]}

Pauses:
{pauses_text[:1500]}

Filler words:
{filler_text[:1500]}
"""
        response = await llm_router.generate(
            task_type="edit_plan",
            prompt=prompt,
            system="You output only valid JSON edit plans with start/end times in seconds.",
        )
        try:
            plan_data = json.loads(response)
            parsed = EditPlan(**plan_data)
            plan = EditPlan(
                job_id=job_id,
                cuts=parsed.cuts,
                broll_inserts=parsed.broll_inserts or _build_default_broll(parsed.cuts),
                caption_style=parsed.caption_style,
                summary=parsed.summary or "LLM edit plan",
            )
        except Exception as exc:  # noqa: BLE001 - optional metadata falls back to the default
            logger.warning("llm_plan_parse_failed", error=str(exc), fallback="deterministic")
            cuts = _build_default_cuts(
                transcript_path, silence_path, filler_path, pauses_path, target_duration
            )
            plan = EditPlan(
                job_id=job_id,
                cuts=cuts,
                broll_inserts=_build_default_broll(cuts),
                summary="Fallback deterministic planner",
            )

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(mode="json"), f, indent=2)
    return plan
