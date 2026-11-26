"""Shared clip-selection rule for stock-footage providers.

Stock search APIs return results in relevance order, which has nothing to do
with the scene's length: a 4 s scene can be filled by a 61 s clip. The
composition trims playback, so the output is correct either way, but the
download and the job's media cache are sized by the pick (#259).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def excluded_media_ids(request: Any) -> frozenset[str]:
    """Stock result ids a request must skip (``params["exclude_ids"]``).

    The footage pass fills it with clips already used elsewhere in the video or
    already rejected for this shot, so a retry moves down the result list
    instead of returning the same top hit again.
    """
    params = getattr(request, "params", None) or {}
    return frozenset(str(i) for i in params.get("exclude_ids") or ())


def requested_duration(request: Any) -> float | None:
    """Return the scene length an :class:`AssetRequest` was generated for."""
    params = getattr(request, "params", None) or {}
    value = params.get("duration_seconds")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def choose_by_duration(
    items: Sequence[dict[str, Any]],
    *,
    target: float | None,
    duration_of: Callable[[dict[str, Any]], float] | None = None,
) -> dict[str, Any] | None:
    """Pick the shortest item that still covers ``target`` seconds.

    Falls back to the longest item when none is long enough, and to the
    provider's own order when the caller has no target (or no item reports a
    duration).
    """
    if not items:
        return None
    if not target or target <= 0:
        return items[0]

    def _duration(item: dict[str, Any]) -> float:
        if duration_of is not None:
            return duration_of(item)
        value = item.get("duration")
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    covering = [item for item in items if _duration(item) >= target]
    if covering:
        return min(covering, key=_duration)
    return max(items, key=_duration)


#: Smallest short side worth downloading for a 1080p render.
DEFAULT_MIN_SHORT_SIDE = 1080


def short_side(item: dict[str, Any]) -> int:
    """The smaller of a media file's ``width`` and ``height`` (0 when unknown)."""
    return min(int(item.get("width") or 0), int(item.get("height") or 0))


def pick_sharp_enough(
    files: Sequence[dict[str, Any]], min_short_side: int = DEFAULT_MIN_SHORT_SIDE
) -> dict[str, Any] | None:
    """The smallest file whose short side reaches ``min_short_side``, else the biggest.

    Stock libraries offer one clip at several sizes; the 4K file is often
    50-200 MB where the 1080p one is a tenth of that and all a 1080p render
    uses (Pexels and Pixabay both shipped 4K first, chai-stall-pilot-1).
    """
    if not files:
        return None
    sharp_enough = [f for f in files if short_side(f) >= min_short_side]
    if sharp_enough:
        return min(sharp_enough, key=short_side)
    return max(files, key=short_side)
