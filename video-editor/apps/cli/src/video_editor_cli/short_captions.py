"""Compatibility module alias for :mod:`video_editor_pipelines.short_captions`."""

from __future__ import annotations

import sys
import warnings

from video_editor_pipelines import short_captions as _impl

warnings.warn(
    "video_editor_cli.short_captions moved to video_editor_pipelines.short_captions",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
