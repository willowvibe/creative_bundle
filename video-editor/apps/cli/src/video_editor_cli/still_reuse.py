"""Compatibility module alias for :mod:`video_editor_pipelines.still_reuse`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import still_reuse as _impl

warnings.warn(
    "video_editor_cli.still_reuse moved to video_editor_pipelines.still_reuse",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
