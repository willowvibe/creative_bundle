"""Compatibility module alias for :mod:`video_editor_pipelines.explainer_script`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import explainer_script as _impl

warnings.warn(
    "video_editor_cli.explainer_script moved to video_editor_pipelines.explainer_script",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
