"""Compatibility module alias for :mod:`video_editor_pipelines.explainer_topic`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import explainer_topic as _impl

warnings.warn(
    "video_editor_cli.explainer_topic moved to video_editor_pipelines.explainer_topic",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
