"""Chapter generation skill package."""

from chapters.chapters import (
    DEFAULT_MAX_CHAPTERS,
    DEFAULT_MIN_CHAPTER_DURATION,
    ChapterSkill,
    _format_timestamp,
    _infer_title_from_text,
    _segment_chapters_by_gaps,
)

__all__ = [
    "ChapterSkill",
    "DEFAULT_MAX_CHAPTERS",
    "DEFAULT_MIN_CHAPTER_DURATION",
    "_format_timestamp",
    "_infer_title_from_text",
    "_segment_chapters_by_gaps",
]
