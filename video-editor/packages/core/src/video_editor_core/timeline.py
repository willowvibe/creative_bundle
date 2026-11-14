"""EDL (Edit Decision List) and segment diffing for partial re-renders."""

from __future__ import annotations

from pydantic import BaseModel, Field

from video_editor_core.ticks import frames_for_ticks, seconds_to_ticks


class Segment(BaseModel):
    """A single timeline segment."""

    id: str
    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    label: str = ""

    @property
    def timeline_start_ticks(self) -> int:
        """Timeline start on the integer tick base."""
        return seconds_to_ticks(self.timeline_start)

    @property
    def timeline_end_ticks(self) -> int:
        """Timeline end on the integer tick base."""
        return seconds_to_ticks(self.timeline_end)

    @property
    def duration_ticks(self) -> int:
        """Segment duration in ticks (boundary difference, so drift-free)."""
        return self.timeline_end_ticks - self.timeline_start_ticks


class EDL(BaseModel):
    """Edit Decision List representing a locked cut."""

    job_id: str
    source_path: str
    segments: list[Segment] = Field(default_factory=list)

    def duration(self) -> float:
        if not self.segments:
            return 0.0
        return self.segments[-1].timeline_end

    def duration_ticks(self) -> int:
        """Total EDL duration on the integer tick base."""
        if not self.segments:
            return 0
        return seconds_to_ticks(self.segments[-1].timeline_end)

    def frame_count(self, fps: float | int, *, mode: str = "round") -> int:
        """Total frames at ``fps``, computed drift-free on ticks."""
        return frames_for_ticks(self.duration_ticks(), fps, mode=mode)

    def segment_map(self) -> dict[str, Segment]:
        return {seg.id: seg for seg in self.segments}


def compute_timeline_segments(segments: list[tuple[float, float, str]]) -> list[Segment]:
    """Convert source-relative kept segments into timeline-aligned segments.

    Args:
        segments: list of (source_start, source_end, label)

    Returns:
        Timeline segments with computed timeline_start/end.
    """
    timeline_start = 0.0
    out: list[Segment] = []
    for idx, (src_start, src_end, label) in enumerate(segments):
        duration = src_end - src_start
        seg = Segment(
            id=f"seg_{idx:04d}",
            source_start=src_start,
            source_end=src_end,
            timeline_start=round(timeline_start, 4),
            timeline_end=round(timeline_start + duration, 4),
            label=label,
        )
        out.append(seg)
        timeline_start += duration
    return out


class SegmentDiff(BaseModel):
    """Result of comparing two EDLs."""

    unchanged: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)


def diff_edls(old: EDL, new: EDL) -> SegmentDiff:
    """Compare two EDLs and return which segments changed, were added, or removed.

    A segment is considered changed if any of its source bounds or label differ.
    """
    old_map = old.segment_map()
    new_map = new.segment_map()

    unchanged: list[str] = []
    changed: list[str] = []

    for seg_id, new_seg in new_map.items():
        old_seg = old_map.get(seg_id)
        if old_seg is None:
            continue
        if (
            old_seg.source_start == new_seg.source_start
            and old_seg.source_end == new_seg.source_end
            and old_seg.label == new_seg.label
        ):
            unchanged.append(seg_id)
        else:
            changed.append(seg_id)

    added = [seg_id for seg_id in new_map if seg_id not in old_map]
    removed = [seg_id for seg_id in old_map if seg_id not in new_map]

    return SegmentDiff(
        unchanged=unchanged,
        changed=changed,
        added=added,
        removed=removed,
    )
