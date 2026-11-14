"""Compile a :class:`RenderGraph` into a HyperFrames **HTML composition project**.

The installed HyperFrames CLI (v0.7.x) renders HTML compositions
(``hyperframes render [DIR]``), not JSON plans. This module writes a project
directory containing ``index.html`` plus an ``assets/`` folder, following the
HyperFrames composition contract:

* one root ``<div id="root" data-composition-id="main" ...>`` with
  ``data-width``/``data-height``/``data-duration``/``data-fps``, plus
  ``data-no-timeline`` because animation is plain CSS (no GSAP timeline is
  registered on ``window.__timelines``; without the flag HyperFrames polls
  for one for 45s on every render — #239);
* the base ``<video>`` as a direct child of the root (muted; the framework owns
  playback);
* one timed ``class="clip"`` element per overlay, each on its own track so
  overlapping windows never collide.

The work itself lives in :mod:`video_editor_core.adapters.from_render_graph`
(which adapts the graph) and :mod:`video_editor_core.compile_composition`
(which emits the HTML); this module is the historical entry point and
delegates to both.

Audio is intentionally **not** emitted here: an ``<audio>`` source must itself
be an audio file (HyperFrames rejects a video file used as audio), and the
project's audio buses need ducking/loudnorm. The renderer muxes the mixed audio
with FFmpeg (``compile_ffmpeg_mux_audio``) after the composition renders. See
docs/archive/audits/AUDIT_2026-10-15.md §4.5.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from video_editor_core.adapters.from_render_graph import from_render_graph
from video_editor_core.compile_composition import compile_composition
from video_editor_core.project_assets import AssetCopier
from video_editor_core.render_graph import RenderGraph

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"

#: Kept for callers that imported the private name.
_AssetCopier = AssetCopier


def compile_hyperframes_project(
    graph: RenderGraph,
    project_dir: Path | str,
    base_video: str | Path | None = None,
) -> Path:
    """Write a HyperFrames project for ``graph`` and return the index.html path.

    ``base_video`` overrides the base video source (e.g. a pre-cut
    intermediate); when omitted, ``graph.source`` is used directly.
    """
    composition = from_render_graph(graph, base_video=base_video)
    index_path = compile_composition(composition, project_dir)
    logger.info(
        "hyperframes_project_written",
        job_id=graph.job_id,
        project=str(project_dir),
        clips=len(composition.scenes[0].layers),
    )
    return index_path
