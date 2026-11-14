"""Thumbnail skill.

Extracts key frames from a final video, optionally scores them with an
LLM (with a heuristic fallback), and writes the selected thumbnail plus
a drawtext title overlay.
"""

from __future__ import annotations

from .thumbnail import (
    ThumbnailCandidate,
    ThumbnailConfig,
    ThumbnailError,
    ThumbnailScorer,
    ThumbnailSkill,
    _best_title,
    _candidate_timestamps,
    _clamp_dimensions,
    _extract_frame,
    _HeuristicScorer,
    _probe_duration,
    _render_title_overlay,
    _sanitize_title,
)

__all__ = [
    "ThumbnailCandidate",
    "ThumbnailConfig",
    "ThumbnailError",
    "ThumbnailScorer",
    "ThumbnailSkill",
    "_HeuristicScorer",
    "_best_title",
    "_candidate_timestamps",
    "_clamp_dimensions",
    "_extract_frame",
    "_probe_duration",
    "_render_title_overlay",
    "_sanitize_title",
]
