"""Static HTML/SVG graphics for content-pack scenes (#311)."""

from video_editor_core.graphics import saju as _saju  # noqa: F401 — registers components
from video_editor_core.graphics.registry import (
    COMPONENTS,
    GraphicError,
    register_graphic,
    render_graphic,
)

__all__ = ["COMPONENTS", "GraphicError", "register_graphic", "render_graphic"]
