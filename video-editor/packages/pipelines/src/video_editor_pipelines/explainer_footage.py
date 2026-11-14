"""The footage pass: stock clips first, judged, then AI stills for what is left.

A ``visual: footage`` scene asks the stock chain for a clip (``provider:
stock``). Stock search matches titles and tags, which lie often enough ("eggplant
biryani" returned a field of eggplants), so each clip's middle frame goes to a
vision model with two questions: does it show the query, and is a brand or logo
readable? The second matters because the stock licences forbid commercial use
of recognisable trademarks.

A rejected clip makes the scene try the next library in the chain before it
gives up; only scenes left without a clip get their AI fallback still. Every
other request (narration, music, stills) keeps the job's all-or-nothing rule.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import structlog
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_core.models.asset import BatchResult
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import run_ffmpeg

from video_editor_pipelines.explainer_plan import (
    FOOTAGE_VIDEO_SUFFIX,
    footage_fallbacks,
    initial_requests,
)

logger = structlog.get_logger(__name__)

#: The judge sees one frame scaled to this width: enough to read a logo.
JUDGE_FRAME_WIDTH = 768
#: The router task whose model takes images (``assets`` config: ``critique``).
JUDGE_TASK = "critique"

JUDGE_SYSTEM = (
    "You check stock footage for a documentary explainer. Answer with JSON only: "
    '{"shows_query": bool, "brand_visible": bool, "reason": "<= 12 words"}. '
    "shows_query: the frame plainly shows what the search asked for, not just a "
    "related mood. brand_visible: a commercial brand name or logo is readable."
)


@dataclass(frozen=True)
class FootageVerdict:
    ok: bool
    reason: str
    #: False when the judge could not run; the clip is kept and reported.
    judged: bool = True


Judge = Callable[[Path, str], Awaitable[FootageVerdict]]


class _Manager(Protocol):
    async def resolve_partial(self, requests: list[AssetRequest]) -> BatchResult: ...


def parse_verdict(text: str) -> FootageVerdict:
    """Read the judge's JSON; anything unreadable is a rejection."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return FootageVerdict(ok=False, reason=f"unreadable verdict: {text[:60]!r}")
    shows = data.get("shows_query") is True
    brand = data.get("brand_visible") is True
    reason = str(data.get("reason") or "").strip()
    if brand:
        reason = f"brand visible: {reason}"
    return FootageVerdict(ok=shows and not brand, reason=reason)


def middle_frame(clip: Path) -> bytes:
    """A JPEG of the clip's middle frame."""
    duration = (probe_media(clip).duration_ms() or 0) / 1000.0
    completed = run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{duration / 2:.2f}",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-vf",
            f"scale={JUDGE_FRAME_WIDTH}:-2",
            "-f",
            "image2",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ],
        text=False,
        timeout=60,
    )
    return bytes(completed.stdout)


def router_judge(router: Any) -> Judge:
    """A :data:`Judge` that asks the router's vision model about one frame."""

    async def judge(clip: Path, query: str) -> FootageVerdict:
        frame = middle_frame(clip)
        text = await router.generate(
            task_type=JUDGE_TASK,
            prompt=f"Search query: {query!r}. Judge this frame.",
            system=JUDGE_SYSTEM,
            images=[frame],
        )
        return parse_verdict(text)

    return judge


def _is_footage_video(request: AssetRequest) -> bool:
    return request.asset_type is AssetType.VIDEO and request.provider == "stock"


async def _verdict(judge: Judge, result: AssetResult, query: str) -> FootageVerdict:
    try:
        return await judge(Path(result.local_path), query)
    except Exception as exc:  # noqa: BLE001 - an outage must not cost the clip
        logger.warning("footage_judge_unavailable", asset_id=result.asset_id, error=str(exc))
        return FootageVerdict(ok=True, reason=f"not judged: {exc}", judged=False)


async def resolve_with_footage(
    make_manager: Callable[[], _Manager],
    requests: Sequence[AssetRequest],
    *,
    judge: Judge,
    stock_chain: Sequence[str],
) -> tuple[list[AssetResult], list[dict[str, Any]]]:
    """Resolve ``requests`` with the footage rules; return results and a report.

    The report has one row per footage scene: ``source`` (``stock`` or
    ``ai_fallback``), the serving ``provider``, whether it was ``judged``, the
    verdict ``reason`` and every ``rejected`` library with its reason.

    ``make_manager`` returns a fresh manager per batch, because a manager closes
    its provider sessions once a batch is done.
    """
    first = initial_requests(requests)
    batch = await make_manager().resolve_partial(first)
    footage = {r.asset_id: r for r in first if _is_footage_video(r)}
    hard = [(asset_id, error) for asset_id, error in batch.failures if asset_id not in footage]
    if hard:
        raise RuntimeError(f"{len(hard)} asset request(s) failed: {hard[0][0]}: {hard[0][1]}")

    results = [r for r in batch.results if r.asset_id not in footage]
    served = {r.asset_id: r for r in batch.results if r.asset_id in footage}
    report: list[dict[str, Any]] = []
    without_clip: set[str] = set()

    for asset_id, request in footage.items():
        row: dict[str, Any] = {
            "scene_id": request.scene_id,
            "query": request.prompt,
            "source": "ai_fallback",
            "provider": "",
            "judged": False,
            "reason": "",
            "rejected": [],
        }
        report.append(row)
        candidate = served.get(asset_id)
        tried: list[str] = []
        if candidate is not None:
            tried.append(str(candidate.metadata.get("provider") or ""))
        else:
            # The whole chain came back empty; nothing left to try.
            tried.extend(stock_chain)
        while candidate is not None:
            verdict = await _verdict(judge, candidate, request.prompt)
            provider = str(candidate.metadata.get("provider") or "")
            if verdict.ok:
                results.append(candidate)
                row.update(
                    source="stock", provider=provider, judged=verdict.judged, reason=verdict.reason
                )
                break
            row["rejected"].append({"provider": provider, "reason": verdict.reason})
            candidate = None
            for library in stock_chain:
                if library in tried:
                    continue
                tried.append(library)
                retry = request.model_copy(
                    update={"provider": library, "asset_id": f"{asset_id}_{library}"}
                )
                retry_batch = await make_manager().resolve_partial([retry])
                if retry_batch.results:
                    candidate = retry_batch.results[0]
                    candidate.metadata.setdefault("provider", library)
                    break
        if row["source"] != "stock":
            without_clip.add(request.scene_id)

    fallbacks = footage_fallbacks(requests, scenes_without_clip=without_clip)
    if fallbacks:
        fallback_batch = await make_manager().resolve_partial(fallbacks)
        if fallback_batch.failures:
            asset_id, error = fallback_batch.failures[0]
            raise RuntimeError(f"fallback still failed: {asset_id}: {error}")
        results.extend(fallback_batch.results)

    logger.info(
        "footage_pass_done",
        stock=sum(row["source"] == "stock" for row in report),
        ai_fallback=sum(row["source"] == "ai_fallback" for row in report),
    )
    return results, report


__all__ = [
    "FOOTAGE_VIDEO_SUFFIX",
    "FootageVerdict",
    "Judge",
    "middle_frame",
    "parse_verdict",
    "resolve_with_footage",
    "router_judge",
]
