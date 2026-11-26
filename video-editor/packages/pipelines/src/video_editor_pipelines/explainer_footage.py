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
    FILE_SHOT_PROVIDER,
    FOOTAGE_SHOT_PREFIX,
    FOOTAGE_VIDEO_SUFFIX,
    footage_fallback_asset_id,
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


def _is_file_shot(request: AssetRequest) -> bool:
    """A ``file:`` shot: the user's own media, never judged, retried or replaced."""
    return request.provider == FILE_SHOT_PROVIDER and request.asset_id.startswith(
        FOOTAGE_SHOT_PREFIX
    )


def _file_row(request: AssetRequest, result: AssetResult | None) -> dict[str, Any]:
    """The report row of a ``file:`` shot."""
    ai = bool(request.params.get("ai"))
    kind = "video" if request.asset_type is AssetType.VIDEO else "image"
    row: dict[str, Any] = {
        "scene_id": request.scene_id,
        "query": "",
        "asset_id": request.asset_id,
        "shot": int(request.asset_id.rsplit("_", 1)[1]),
        "kind": kind,
        "file": request.prompt,
        "source": "file",
        "provider": FILE_SHOT_PROVIDER,
        "judged": False,
        "ai": ai,
        "reason": "own file, not vision-checked",
        "rejected": [],
    }
    if ai and kind == "video":
        row["ai_label"] = "not burned into video"
    elif ai and result is not None:
        row["ai_label"] = str(result.metadata.get("ai_label") or "applied by the label pass")
    return row


def _is_shot_photo(request: AssetRequest) -> bool:
    """A footage shot's stock photo (``photo:``); judged by nothing, like map photos."""
    return (
        request.asset_type is AssetType.IMAGE
        and request.provider == "stock"
        and request.asset_id.startswith(FOOTAGE_SHOT_PREFIX)
    )


#: Picks one library may offer for one shot before the next library is tried.
MAX_TRIES_PER_LIBRARY = 3
#: Results asked for on a retry, so excluded ids still leave candidates.
RETRY_PER_PAGE = 15


#: Slack on top of the worst case: judge calls that fail and retry on the fallback model.
JUDGE_BUDGET_MARGIN = 10


def footage_judge_attempt_budget(
    requests: Sequence[AssetRequest], stock_chain: Sequence[str], configured: int
) -> int:
    """LLM attempts the footage pass may need: one judge call per pick.

    Each stock video request (a scene, or one shot of a ``shots`` scene) is judged
    at most ``MAX_TRIES_PER_LIBRARY`` times per library, so the worst case is
    ``videos x tries x libraries``. The router's per-job ``max_llm_attempts_per_job``
    (default 50) starved the tiffin explainer: 51 > 50 left 12 clips unchecked. The
    result is never below ``configured``, so a larger configured budget still wins.
    """
    videos = sum(1 for request in requests if _is_footage_video(request))
    libraries = max(1, len(stock_chain))
    needed = videos * MAX_TRIES_PER_LIBRARY * libraries + JUDGE_BUDGET_MARGIN
    return max(configured, needed)


def size_router_for_footage(
    router: Any, requests: Sequence[AssetRequest], stock_chain: Sequence[str]
) -> int:
    """Raise ``router``'s attempt budget to what the footage judge needs; return it."""
    configured = int(router.config.max_llm_attempts_per_job)
    budget = footage_judge_attempt_budget(requests, stock_chain, configured)
    if budget != configured:
        router.config = router.config.model_copy(update={"max_llm_attempts_per_job": budget})
        logger.info("footage_judge_budget", configured=configured, budget=budget)
    return budget


def _provider(result: AssetResult) -> str:
    return str(result.metadata.get("provider") or "")


def _media_id(result: AssetResult) -> str:
    attribution = result.metadata.get("attribution") or {}
    value = attribution.get("video_id") or attribution.get("photo_id")
    return "" if value is None else str(value)


def _is_repeat(result: AssetResult, used: dict[str, set[str]]) -> bool:
    media_id = _media_id(result)
    return bool(media_id) and media_id in used.get(_provider(result), set())


def _mark_used(result: AssetResult, used: dict[str, set[str]]) -> None:
    media_id = _media_id(result)
    if media_id:
        used.setdefault(_provider(result), set()).add(media_id)


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

    The report has one row per footage scene (per shot for a ``shots`` scene,
    which adds ``shot``, ``asset_id`` and ``kind``): ``source`` (``stock``,
    ``ai_fallback`` or ``file`` for a ``file:`` shot, which adds ``file`` and ``ai``), the serving ``provider``, whether it was ``judged``, the
    verdict ``reason`` and every ``rejected`` library with its reason.

    ``make_manager`` returns a fresh manager per batch, because a manager closes
    its provider sessions once a batch is done.
    """
    first = initial_requests(requests)
    batch = await make_manager().resolve_partial(first)
    footage = {r.asset_id: r for r in first if _is_footage_video(r) or _is_shot_photo(r)}
    hard = [(asset_id, error) for asset_id, error in batch.failures if asset_id not in footage]
    if hard:
        raise RuntimeError(f"{len(hard)} asset request(s) failed: {hard[0][0]}: {hard[0][1]}")

    results = [r for r in batch.results if r.asset_id not in footage]
    # A ``file:`` shot is no stock request: a missing file already failed above
    # as a hard error, and a served one skips the judge and the dedupe.
    file_results = {r.asset_id: r for r in results}
    served = {r.asset_id: r for r in batch.results if r.asset_id in footage}
    report: list[dict[str, Any]] = []
    without_clip: set[str] = set()
    shots_without_media: set[str] = set()

    for request in first:
        if _is_file_shot(request):
            report.append(_file_row(request, file_results.get(request.asset_id)))

    # Stock ids already on screen, per library, so no clip or photo is used twice.
    used: dict[str, set[str]] = {}

    async def _retry(request: AssetRequest, library: str, exclude: set[str], n: int):
        suffix = f"_{library}" if n == 1 else f"_{library}_{n}"
        params = {**request.params, "exclude_ids": sorted(exclude), "per_page": RETRY_PER_PAGE}
        retry = request.model_copy(
            update={
                "provider": library,
                "asset_id": f"{request.asset_id}{suffix}",
                "params": params,
            }
        )
        retry_batch = await make_manager().resolve_partial([retry])
        if not retry_batch.results:
            return None
        found = retry_batch.results[0]
        found.metadata.setdefault("provider", library)
        return found

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
        is_shot = asset_id.startswith(FOOTAGE_SHOT_PREFIX)
        if is_shot:
            row["asset_id"] = asset_id
            row["shot"] = int(asset_id.rsplit("_", 1)[1])
            row["kind"] = "photo" if _is_shot_photo(request) else "video"
        candidate = served.get(asset_id)
        if _is_shot_photo(request):
            # A photo has no frame to judge; a repeat gets one retry further down the list.
            if candidate is not None and _is_repeat(candidate, used):
                provider = _provider(candidate)
                row["rejected"].append(
                    {"provider": provider, "reason": "already used in this video"}
                )
                candidate = await _retry(request, request.provider, set(used.get(provider, ())), 1)
                if candidate is not None and _is_repeat(candidate, used):
                    candidate = None
            if candidate is not None:
                _mark_used(candidate, used)
                results.append(candidate)
                row.update(
                    source="stock",
                    provider=_provider(candidate),
                    reason="photo, not vision-checked",
                )
            else:
                shots_without_media.add(footage_fallback_asset_id(asset_id))
            continue
        # Each library gets up to MAX_TRIES_PER_LIBRARY picks, skipping clips that were
        # rejected for this shot or are already used elsewhere in the video.
        rejected_ids: dict[str, set[str]] = {}
        libraries = list(stock_chain)
        first_provider = _provider(candidate) if candidate is not None else ""
        if first_provider in libraries:
            libraries.remove(first_provider)
            libraries.insert(0, first_provider)
        accepted = False
        exhausted: set[str] = set()
        # Round-robin: a fresh library first, then each library's next picks.
        for tries in range(1, MAX_TRIES_PER_LIBRARY + 1):
            for library in libraries:
                if accepted or library in exhausted:
                    continue
                if not (tries == 1 and library == first_provider and candidate is not None):
                    exclude = used.get(library, set()) | rejected_ids.get(library, set())
                    candidate = await _retry(request, library, exclude, tries)
                if candidate is None:
                    exhausted.add(library)  # nothing more for this query here
                    continue
                provider = _provider(candidate) or library
                media_id = _media_id(candidate)
                if _is_repeat(candidate, used):
                    row["rejected"].append(
                        {"provider": provider, "reason": "already used in this video"}
                    )
                    rejected_ids.setdefault(library, set()).add(media_id)
                    candidate = None
                    continue
                verdict = await _verdict(judge, candidate, request.prompt)
                if verdict.ok:
                    _mark_used(candidate, used)
                    results.append(candidate)
                    row.update(
                        source="stock",
                        provider=provider,
                        judged=verdict.judged,
                        reason=verdict.reason,
                    )
                    accepted = True
                    continue
                row["rejected"].append({"provider": provider, "reason": verdict.reason})
                if media_id:
                    rejected_ids.setdefault(library, set()).add(media_id)
                else:
                    exhausted.add(library)  # an id-less result would only come back again
                candidate = None
        if row["source"] != "stock":
            if is_shot:
                shots_without_media.add(footage_fallback_asset_id(asset_id))
            else:
                without_clip.add(request.scene_id)

    fallbacks = footage_fallbacks(
        requests, scenes_without_clip=without_clip, shots_without_media=shots_without_media
    )
    if fallbacks:
        fallback_batch = await make_manager().resolve_partial(fallbacks)
        if fallback_batch.failures:
            asset_id, error = fallback_batch.failures[0]
            raise RuntimeError(f"fallback still failed: {asset_id}: {error}")
        results.extend(fallback_batch.results)

    logger.info(
        "footage_pass_done",
        stock=sum(row["source"] == "stock" for row in report),
        file=sum(row["source"] == "file" for row in report),
        ai_fallback=sum(row["source"] == "ai_fallback" for row in report),
    )
    return results, report


__all__ = [
    "FOOTAGE_VIDEO_SUFFIX",
    "FootageVerdict",
    "Judge",
    "middle_frame",
    "parse_verdict",
    "footage_judge_attempt_budget",
    "resolve_with_footage",
    "router_judge",
    "size_router_for_footage",
]
