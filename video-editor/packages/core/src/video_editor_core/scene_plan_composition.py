"""Compile a script-to-video ``ScenePlan`` into a HyperFrames HTML project.

It emits an HTML composition where each scene is a timed clip (image with Ken
Burns motion, a generated video, or a text placeholder) plus its narration
audio, and the plan's music bed as a root-level ``<audio>``. The Remotion bundle
it replaced was retired; see
docs/archive/plans/2026-10-15-render-graph-rewrite.md, M6.1.

Media must be direct children of the composition root (HyperFrames only seeks
root-level media), so every scene visual/audio element is emitted at the root
with its own timing and track index.

The input is the raw scene-plan dict (as written to ``plan/scene_plan.json``),
duck-typed against the ``ScenePlan`` schema and adapted to a
:class:`~video_editor_core.composition.Composition` by
:mod:`video_editor_core.adapters.from_scene_plan`; the HTML compiler itself
lives in ``compile_composition.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from video_editor_core.adapters.from_scene_plan import from_scene_plan
from video_editor_core.compile_composition import compile_composition

#: Re-exported: callers (``explainer_qa``) import the canvas mapping from here.
#: The ``X as X`` form is an explicit re-export, which mypy strict's
#: ``no_implicit_reexport`` requires (a plain import is not an export).
from video_editor_core.composition import canvas_for_aspect as canvas_for_aspect  # noqa: F401

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"
DEFAULT_FPS = 30


def compile_scene_plan_project(
    plan: dict[str, Any],
    project_dir: Path | str,
    *,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Write a HyperFrames project for a scene plan; return the index.html path."""
    composition = from_scene_plan(plan, fps=fps)
    index_path = compile_composition(composition, project_dir)
    logger.info(
        "scene_plan_project_written",
        job_id=composition.job_id,
        project=str(project_dir),
        scenes=len(composition.scenes),
        duration=composition.duration,
    )
    return index_path


def render_scene_plan(
    plan_path: Path | str,
    output_path: Path | str,
    project_dir: Path | str,
    *,
    cli_path: str = "hyperframes",
    timeout: int = 1800,
    fps: int = DEFAULT_FPS,
) -> Path:
    """Render a scene plan to MP4 via a HyperFrames HTML composition.

    HyperFrames renders the composition's ``<audio>`` elements into the output,
    so no separate audio mux is needed. This is the renderer of record for the
    script-to-video pipeline (the Remotion bundle was retired).
    """
    import json
    import shutil

    from video_editor_core.hyperframes_cli import (
        hyperframes_available,
        render_project,
    )

    plan_path = Path(plan_path)
    output_path = Path(output_path)
    project_dir = Path(project_dir)
    if not hyperframes_available(cli_path):
        raise RuntimeError(
            "HyperFrames CLI not found; script-to-video rendering requires "
            f"HyperFrames ({cli_path!r} not on PATH)."
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # The full project is compiled either way: the QA gates (hyperframes check,
    # frame snapshots) read it.
    compile_scene_plan_project(plan, project_dir, fps=fps)
    rendered = project_dir / "rendered.mp4"
    from video_editor_core import chunked_render

    if chunked_render.chunked_render_enabled():
        chunked_render.render_composition_chunked(
            from_scene_plan(plan, fps=fps),
            rendered,
            project_dir.parent / "chunked",
            cache_dir=project_dir.parent / "render_cache",
            cli_path=cli_path,
            timeout=timeout,
        )
    else:
        render_project(project_dir, rendered, cli_path=cli_path, timeout=timeout, fps=fps)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rendered, output_path)
    logger.info(
        "scene_plan_render_complete",
        job_id=plan.get("job_id"),
        output=str(output_path),
    )
    return output_path
