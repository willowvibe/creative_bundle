"""Compatibility shim for :mod:`video_editor_core.models.job`.

Deprecated: import from ``video_editor_core.models.job`` instead. This
module will be removed after one release.
"""

from __future__ import annotations

import warnings

from video_editor_core.models import job as _impl

warnings.warn(
    "video_editor_engine.models.job moved to video_editor_core.models.job",
    DeprecationWarning,
    stacklevel=2,
)

globals().update({key: value for key, value in vars(_impl).items() if not key.startswith("__")})
