"""Compatibility shim for :mod:`video_editor_core.templates`."""

from __future__ import annotations

import sys
import warnings

from video_editor_core import templates as _impl

warnings.warn(
    "video_editor_core.templates moved to video_editor_core.templates",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
