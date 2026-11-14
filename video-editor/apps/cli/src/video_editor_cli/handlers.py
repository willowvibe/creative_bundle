"""Compatibility module alias for :mod:`video_editor_pipelines.handlers`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import handlers as _impl

warnings.warn(
    "video_editor_cli.handlers moved to video_editor_pipelines.handlers",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
