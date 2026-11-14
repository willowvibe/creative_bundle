"""Partial re-render renderer for the Second Pass skill.

The renderer diffs the old and new EDLs, reuses cached segment files for
unchanged segments, and re-renders only changed or added segments through the
graphics skill.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from video_editor_core.render_cache import SegmentCache
from video_editor_core.timeline import EDL, diff_edls
from video_editor_ffmpeg.cuts import apply_cuts, concat_segments
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import run_ffmpeg

if TYPE_CHECKING:
    from graphics.graphics import GraphicsSkill
    from graphics.models import CompositionPlan

logger = structlog.get_logger(__name__)


def _copy_or_use(path: Path, fallback: Path) -> Path:
    """Return the requested path if it exists, otherwise copy the fallback."""
    if path.exists():
        return path
    if fallback.exists():
        shutil.copyfile(fallback, path)
        return path
    raise FileNotFoundError(f"Neither {path} nor {fallback} exists")


def rebuild_after_edit(
    old_edl: EDL,
    new_edl: EDL,
    plan: CompositionPlan,
    graphics_skill: GraphicsSkill,
    cache: SegmentCache,
    edits: list[Any] | None = None,
) -> Path:
    """Rebuild the final graphics video after a Second Pass segment edit.

    Args:
        old_edl: EDL before the edit.
        new_edl: EDL after the edit.
        plan: CompositionPlan built from the new EDL.
        graphics_skill: The GraphicsSkill instance used to render compositions.
        cache: SegmentCache for reusing unchanged rendered segments.
        edits: Optional list of ``SegmentEdit`` objects. When provided, an edit
            with ``regenerate_graphics=False`` prevents a pure label change
            from forcing a full segment re-render.

    Returns:
        Path to the rebuilt final video.
    """

    diff = diff_edls(old_edl, new_edl)
    changed_ids = set(diff.changed + diff.added)

    # Honour the per-edit regenerate_graphics flag: a pure label change may
    # reuse the cached render even though the EDL technically changed.
    if edits:
        for edit in edits:
            if not edit.should_regenerate_graphics() and edit.segment_id in changed_ids:
                changed_ids.discard(edit.segment_id)
                logger.info(
                    "second_pass_skipping_render_for_label_change",
                    segment_id=edit.segment_id,
                )

    segment_dir = graphics_skill.ctx.work_dir / "graphics" / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)

    source_video = Path(plan.source_video)
    info = probe_media(source_video)
    if info.duration is None:
        raise RuntimeError(f"Could not determine duration for {source_video}")

    options = getattr(graphics_skill, "render_options", None) or RenderOptions()

    final_segments: list[Path] = []
    for comp in plan.segments:
        cached_output = segment_dir / f"{comp.segment_id}.mp4"

        if comp.segment_id not in changed_ids and cached_output.exists():
            # Unchanged segment: reuse previously rendered file if present.
            logger.info("reusing_unchanged_segment", segment_id=comp.segment_id)
            final_segments.append(cached_output)
            continue

        # Changed or missing segment: cut input and re-render.
        end = min(comp.source_end, info.duration)
        start = min(comp.source_start, end)
        seg_input = segment_dir / f"{comp.segment_id}_input.mp4"
        if end - start > 0.01:
            apply_cuts(source_video, [(start, end)], seg_input, render_options=options)
        else:
            # Degenerate placeholder.
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s={comp.width}x{comp.height}:d=0.5",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "0.5",
                "-pix_fmt",
                "yuv420p",
            ]
            _video_encode(cmd, options)
            _audio_encode(cmd, options)
            cmd.append(str(seg_input))
            run_ffmpeg(cmd)
        rendered = graphics_skill.render_composition(comp, seg_input, segment_dir, plan=plan)
        final_segments.append(rendered)

    if graphics_skill.run_qa_per_segment:
        # Bug #88: the second-pass renderer must run the same per-segment
        # boundary check that the first-pass graphics skill runs. Otherwise
        # an edit that introduces a jarring cut or audio pop would pass
        # silently.
        from qa.segment_check import check_segment_boundary

        prev_output: Path | None = None
        prev_segment_id: str | None = None
        for comp, seg_path in zip(plan.segments, final_segments, strict=False):
            check_segment_boundary(
                prev_path=prev_output,
                curr_path=seg_path,
                prev_segment_id=prev_segment_id,
                curr_segment_id=comp.segment_id,
                work_dir=graphics_skill.ctx.work_dir,
            )
            prev_output = seg_path
            prev_segment_id = comp.segment_id

    output = graphics_skill.ctx.work_dir / "graphics" / "with_graphics.mp4"
    concat_segments(final_segments, output)
    logger.info("second_pass_render_complete", changed=len(changed_ids), output=str(output))
    return output
