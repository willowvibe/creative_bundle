"""Registry for pack graphics: a name, data and the design tokens in, HTML out."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from video_editor_core.composition import Canvas
from video_editor_core.designs import DesignTokens

__all__ = ["COMPONENTS", "GraphicError", "register_graphic", "render_graphic"]

GraphicRenderer = Callable[[Mapping[str, Any], DesignTokens, Canvas], str]
COMPONENTS: dict[str, GraphicRenderer] = {}


class GraphicError(ValueError):
    """A pack named a graphic that cannot be rendered."""


def register_graphic(name: str) -> Callable[[GraphicRenderer], GraphicRenderer]:
    def _decorator(renderer: GraphicRenderer) -> GraphicRenderer:
        COMPONENTS[name] = renderer
        return renderer

    return _decorator


def render_graphic(
    component: str,
    data: Mapping[str, Any],
    tokens: DesignTokens,
    canvas: Canvas,
) -> str:
    if component not in COMPONENTS:
        raise GraphicError(
            f"unknown graphic component {component!r}; known: {', '.join(sorted(COMPONENTS))}"
        )
    return COMPONENTS[component](data, tokens, canvas)
