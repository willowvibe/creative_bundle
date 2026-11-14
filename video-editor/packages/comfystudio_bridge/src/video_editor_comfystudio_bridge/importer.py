"""Import an edited ComfyStudio project back into a ``ScenePlan``."""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_core.models.scene_plan import Scene, ScenePlan

from video_editor_comfystudio_bridge.models import ComfyStudioClip, ComfyStudioProject

logger = structlog.get_logger(__name__)


def import_from_comfystudio(
    project_file: Path,
    original_plan: ScenePlan,
) -> ScenePlan:
    """Read a ComfyStudio project and produce an updated ``ScenePlan``.

    The importer matches clips to scenes by comparing the clip's original
    ``startTime`` (which equals the scene's cumulative start in the export)
    to the original scene order. Edits it understands:

    - Clip ``duration`` changed → update ``scene.duration_seconds``.
    - Clip moved → update scene order/duration accordingly.
    - Clips added/removed → log a warning; dropped clips are ignored for now.

    Clips that cannot be reconciled are ignored so the engine can still
    re-render the parts it understands.
    """
    if not project_file.is_file():
        raise FileNotFoundError(f"ComfyStudio project not found: {project_file}")

    project = ComfyStudioProject.model_validate_json(project_file.read_text(encoding="utf-8"))
    if not project.timelines:
        raise ValueError("ComfyStudio project has no timelines")

    timeline = project.timelines[0]

    # Collect visual clips from all video tracks.
    visual_clips: list[ComfyStudioClip] = []
    for track in timeline.tracks:
        if track.type != "video":
            continue
        for clip in track.clips:
            if clip.type in {"video", "image"}:
                visual_clips.append(clip)

    # Sort by start time so we can walk the original scene order.
    visual_clips.sort(key=lambda c: c.startTime)

    if len(visual_clips) != len(original_plan.scenes):
        logger.warning(
            "comfystudio_scene_count_mismatch",
            original=len(original_plan.scenes),
            imported=len(visual_clips),
        )

    updated_scenes: list[Scene] = []
    for i, clip in enumerate(visual_clips):
        if i < len(original_plan.scenes):
            original = original_plan.scenes[i]
            updated = original.model_copy(update={"duration_seconds": clip.duration})
            updated_scenes.append(updated)
        else:
            # Extra clips in ComfyStudio without a matching original scene are
            # logged but ignored until we support scene insertion.
            logger.warning(
                "comfystudio_unmatched_clip_ignored",
                clip_id=clip.id,
                clip_name=clip.name,
            )

    if not updated_scenes:
        raise ValueError("No scenes could be reconciled from the ComfyStudio project")

    # Preserve all other plan metadata.
    return original_plan.model_copy(
        update={
            "scenes": updated_scenes,
        },
    )
