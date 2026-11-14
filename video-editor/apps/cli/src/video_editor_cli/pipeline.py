"""Compatibility module alias for :mod:`video_editor_pipelines.pipeline`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import pipeline as _impl

warnings.warn(
    "video_editor_cli.pipeline moved to video_editor_pipelines.pipeline",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
