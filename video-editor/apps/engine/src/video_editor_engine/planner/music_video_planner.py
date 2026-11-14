"""Compatibility module alias for :mod:`video_editor_pipelines.music_video_planner`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import music_video_planner as _impl

warnings.warn(
    "video_editor_engine.planner.music_video_planner moved to video_editor_pipelines.music_video_planner",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
