"""B-roll clip ranking: pick the best downloaded clip for a moment.

When a provider returns multiple candidate clips (e.g. stock search hits),
the ranker scores each by how well it matches the desired duration and
resolution. The top-scoring clip is chosen for the overlay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import probe_media

logger = structlog.get_logger(__name__)

PREFERRED_WIDTH = 1920
PREFERRED_HEIGHT = 1080
MAX_DURATION_DRIFT_SECONDS = 8.0


@dataclass(frozen=True)
class ClipCandidate:
    """A downloaded clip candidate for a B-roll moment."""

    path: Path
    duration: float = 0.0
    width: int = 0
    height: int = 0

    @classmethod
    def from_path(cls, path: Path | str) -> ClipCandidate:
        path = Path(path)
        try:
            info = probe_media(path)
            return ClipCandidate(
                path=path,
                duration=info.duration or 0.0,
                width=info.width or 0,
                height=info.height or 0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("broll_probe_failed", path=str(path), error=str(exc))
            return ClipCandidate(path=path)


def score_candidate(candidate: ClipCandidate, target_duration: float) -> float:
    """Return a score in [0, 1] favoring duration match and good resolution.

    Candidates shorter than the target are penalized heavily (they would
    need looping); candidates longer than ``target + MAX_DURATION_DRIFT``
    are penalized too (they waste content and get trimmed).
    """
    duration = candidate.duration or target_duration
    if duration < target_duration * 0.5:
        duration_score = 0.1
    elif duration <= target_duration:
        duration_score = duration / target_duration
    elif duration <= target_duration + MAX_DURATION_DRIFT_SECONDS:
        duration_score = 1.0 - (duration - target_duration) / MAX_DURATION_DRIFT_SECONDS
    else:
        duration_score = 0.5

    resolution = candidate.width * candidate.height
    if resolution <= 0:
        res_score = 0.5
    else:
        res_score = min(1.0, resolution / (PREFERRED_WIDTH * PREFERRED_HEIGHT))

    return round(0.7 * duration_score + 0.3 * res_score, 4)


def rank_candidates(
    candidates: Sequence[Path | str | ClipCandidate],
    target_duration: float,
) -> ClipCandidate | None:
    """Score and return the best candidate, or ``None`` for an empty list."""
    if not candidates:
        return None
    scored: list[tuple[float, ClipCandidate]] = []
    for item in candidates:
        candidate = item if isinstance(item, ClipCandidate) else ClipCandidate.from_path(item)
        scored.append((score_candidate(candidate, target_duration), candidate))
    best = max(scored, key=lambda pair: pair[0])
    return best[1]
