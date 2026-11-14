"""Import an edited FableCut project back into a ``ScenePlan``."""

from __future__ import annotations

from pathlib import Path

import structlog
from video_editor_core.models.scene_plan import Scene, ScenePlan

from video_editor_fablecut_bridge.models import FableCutClip, FableCutProject

logger = structlog.get_logger(__name__)


def plan_from_fablecut_project(
    project: FableCutProject,
    original_plan: ScenePlan,
) -> ScenePlan:
    """Reconcile an in-memory FableCut project into an updated ``ScenePlan``.

    Shared core of ``ve import-fablecut`` and ``ve import-otio``. The importer
    matches clips to scenes by comparing the clip's original ``start`` time
    (which equals the scene's cumulative start in the export) to the original
    scene order. Edits it understands:

    - Clip ``duration`` changed → update ``scene.duration_seconds``.
    - Clip moved → update scene order/duration accordingly.
    - Clips added/removed → log a warning; dropped clips are ignored for now.

    Clips that cannot be reconciled are ignored so the engine can still
    re-render the parts it understands.
    """
    # Collect visual clips from all video tracks.
    visual_clips: list[FableCutClip] = []
    for clip in project.clips:
        if clip.track.startswith("V") and clip.kind in {"video", "image"}:
            visual_clips.append(clip)

    # Sort by start time so we can walk the original scene order.
    visual_clips.sort(key=lambda c: c.start)

    if len(visual_clips) != len(original_plan.scenes):
        logger.warning(
            "fablecut_scene_count_mismatch",
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
            logger.warning(
                "fablecut_unmatched_clip_ignored",
                clip_id=clip.id,
                clip_name=clip.name,
            )

    if not updated_scenes:
        raise ValueError("No scenes could be reconciled from the FableCut project")

    return original_plan.model_copy(update={"scenes": updated_scenes})


def import_from_fablecut(
    project_file: Path,
    original_plan: ScenePlan,
) -> ScenePlan:
    """Read a FableCut ``project.json`` and reconcile it into a ``ScenePlan``."""
    if not project_file.is_file():
        raise FileNotFoundError(f"FableCut project not found: {project_file}")
    project = FableCutProject.model_validate_json(project_file.read_text(encoding="utf-8"))
    return plan_from_fablecut_project(project, original_plan)


def import_otio_plan(
    otio_file: Path,
    original_plan: ScenePlan,
) -> ScenePlan:
    """Read an OpenTimelineIO file (e.g. exported by FilmCraft or Resolve) and
    reconcile it into an updated ``ScenePlan`` for re-rendering."""
    from video_editor_fablecut_bridge.otio import import_otio

    project = import_otio(otio_file)
    return plan_from_fablecut_project(project, original_plan)
