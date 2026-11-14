"""Compatibility shim for :mod:`video_editor_core.models.edit_plan`.

Deprecated: import from ``video_editor_core.models.edit_plan`` instead. This
module will be removed after one release.
"""

from __future__ import annotations

import warnings

from video_editor_core.models import edit_plan as _impl

warnings.warn(
    "video_editor_engine.models.edit_plan moved to video_editor_core.models.edit_plan",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({key: value for key, value in vars(_impl).items() if not key.startswith("__")})
