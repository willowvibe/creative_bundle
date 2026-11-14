"""Compatibility shim for :mod:`video_editor_core.models.scene_plan`.

Deprecated: import from ``video_editor_core.models.scene_plan`` instead. This
module will be removed after one release.
"""

from __future__ import annotations

import warnings

from video_editor_core.models import scene_plan as _impl

warnings.warn(
    "video_editor_engine.models.scene_plan moved to video_editor_core.models.scene_plan",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({key: value for key, value in vars(_impl).items() if not key.startswith("__")})
