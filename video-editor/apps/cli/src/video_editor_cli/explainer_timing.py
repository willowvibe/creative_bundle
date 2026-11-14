"""Compatibility module alias for :mod:`video_editor_pipelines.explainer_timing`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import explainer_timing as _impl

warnings.warn(
    "video_editor_cli.explainer_timing moved to video_editor_pipelines.explainer_timing",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
