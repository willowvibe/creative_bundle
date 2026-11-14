"""Compatibility module alias for :mod:`video_editor_pipelines.scene_planner`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import scene_planner as _impl

warnings.warn(
    "video_editor_engine.planner.scene_planner moved to video_editor_pipelines.scene_planner",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
