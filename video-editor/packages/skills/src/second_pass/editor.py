"""EDL editor for the Second Pass skill."""

from __future__ import annotations

import structlog
from video_editor_core.timeline import EDL, Segment

from second_pass.models import SegmentEdit

logger = structlog.get_logger(__name__)


class SegmentNotFoundError(Exception):
    """Raised when an edit targets a segment that does not exist in the EDL."""


class InvalidBoundsError(Exception):
    """Raised when edit bounds are invalid."""


def apply_segment_edit(edl: EDL, edit: SegmentEdit, source_duration: float) -> EDL:
    """Apply a single segment edit and recompute downstream timeline positions.

    Args:
        edl: Current locked EDL.
        edit: User refinement for one segment.
        source_duration: Total duration of the source video to clamp bounds.

    Returns:
        A new EDL with updated segment bounds and recomputed timeline positions.
    """
    seg_map = edl.segment_map()
    if edit.segment_id not in seg_map:
        raise SegmentNotFoundError(f"Segment {edit.segment_id} not found in EDL")

    new_segments: list[Segment] = []
    for seg in edl.segments:
        if seg.id == edit.segment_id:
            start = edit.new_source_start if edit.new_source_start is not None else seg.source_start
            end = edit.new_source_end if edit.new_source_end is not None else seg.source_end
            label = edit.new_label if edit.new_label is not None else seg.label

            start = max(0.0, min(start, source_duration))
            end = max(start, min(end, source_duration))
            if end - start < 0.01:
                raise InvalidBoundsError(
                    f"Segment {seg.id} would be too short after edit ({start:.3f}-{end:.3f})"
                )

            new_segments.append(
                Segment(
                    id=seg.id,
                    source_start=round(start, 4),
                    source_end=round(end, 4),
                    timeline_start=0.0,  # recomputed below
                    timeline_end=0.0,
                    label=label,
                )
            )
        else:
            new_segments.append(
                Segment(
                    id=seg.id,
                    source_start=seg.source_start,
                    source_end=seg.source_end,
                    timeline_start=0.0,
                    timeline_end=0.0,
                    label=seg.label,
                )
            )

    # Recompute timeline positions in order.
    cursor = 0.0
    for seg in new_segments:
        duration = seg.source_end - seg.source_start
        seg.timeline_start = round(cursor, 4)
        seg.timeline_end = round(cursor + duration, 4)
        cursor += duration

    logger.info(
        "segment_edit_applied",
        segment_id=edit.segment_id,
        new_start=new_segments[0].source_start if len(new_segments) == 1 else "...",
        new_duration=edl.duration(),
    )
    return EDL(job_id=edl.job_id, source_path=edl.source_path, segments=new_segments)
