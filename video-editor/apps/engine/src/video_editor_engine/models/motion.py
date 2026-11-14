"""Compatibility shim for :mod:`video_editor_core.models.motion`.

Deprecated: import from ``video_editor_core.models.motion`` instead. This
module will be removed after one release.
"""

from __future__ import annotations

import warnings

from video_editor_core.models import motion as _impl

warnings.warn(
    "video_editor_engine.models.motion moved to video_editor_core.models.motion",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({key: value for key, value in vars(_impl).items() if not key.startswith("__")})
