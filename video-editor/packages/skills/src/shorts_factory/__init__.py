"""Shorts factory skill.

Long-form video → 3-5 vertical Shorts/Reels with viral-moment detection,
9:16 reframe, and a per-short plan manifest.
"""

from __future__ import annotations

from .shorts_factory import (
    MomentScorer,
    ShortCandidate,
    ShortsFactoryConfig,
    ShortsFactoryError,
    ShortsFactorySkill,
    _candidate_windows,
    _first_sentence,
    _HeuristicScorer,
    _parse_aspect,
    _probe_duration,
    _validate_transcript,
)

__all__ = [
    "MomentScorer",
    "ShortCandidate",
    "ShortsFactoryConfig",
    "ShortsFactoryError",
    "ShortsFactorySkill",
    "_HeuristicScorer",
    "_candidate_windows",
    "_first_sentence",
    "_parse_aspect",
    "_probe_duration",
    "_validate_transcript",
]
