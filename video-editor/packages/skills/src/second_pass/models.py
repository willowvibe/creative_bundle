"""Data models for the Second Pass skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class SegmentEdit(BaseModel):
    """A user-driven refinement of a single EDL segment."""

    segment_id: str
    new_source_start: float | None = None
    new_source_end: float | None = None
    new_label: str | None = None
    regenerate_graphics: bool = True
    reason: str = ""

    @model_validator(mode="after")
    def _at_least_one_change(self) -> SegmentEdit:
        if self.new_source_start is None and self.new_source_end is None and self.new_label is None:
            raise ValueError(
                "SegmentEdit must change at least one of source_start, source_end, or label"
            )
        return self

    def should_regenerate_graphics(self) -> bool:
        """Return True when this edit requires the segment's graphics to be
        re-rendered.

        Pure label changes can reuse the existing rendered segment file, while
        source-boundary changes (or an explicit ``regenerate_graphics=False``)
        control whether the renderer recuts and re-composites the segment.
        """
        if not self.regenerate_graphics:
            return False
        return self.new_source_start is not None or self.new_source_end is not None


class SecondPassManifest(BaseModel):
    """Record of edits applied during the second pass."""

    job_id: str
    edits: list[SegmentEdit] = Field(default_factory=list)
    output_video: str = ""
    # Advisory, non-blocking critique of each applied edit, produced by the
    # optional Jev classifier. Empty when no classifier is available.
    critique: list[dict[str, Any]] = Field(default_factory=list)
