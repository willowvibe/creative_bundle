"""Compatibility module alias for :mod:`video_editor_pipelines.explainer_pipeline`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import explainer_pipeline as _impl

warnings.warn(
    "video_editor_cli.explainer_pipeline moved to video_editor_pipelines.explainer_pipeline",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
