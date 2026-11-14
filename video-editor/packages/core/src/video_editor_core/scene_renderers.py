"""Scene renderers that live outside core, found by entry point.

Some scene types are drawn by their own workspace package (maps: ``video-editor-maps``). Core must not
import those packages (they import core), so the scene-plan adapter asks this module for the renderer
of a scene type instead. A package offers one by declaring, in its ``pyproject.toml``::

    [project.entry-points."video_editor.scene_renderers"]
    map = "video_editor_maps.scenes:render_map"
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "video_editor.scene_renderers"

#: The distribution that provides each known external scene type, for the error message.
PROVIDERS = {"map": "video-editor-maps"}


class SceneRendererMissingError(ValueError):
    """No installed package renders this scene type."""


@cache
def scene_renderer(kind: str) -> Callable[..., Any]:
    """The renderer registered for scene type ``kind``.

    Raises :class:`SceneRendererMissingError` when no installed distribution declares one.
    """
    found = entry_points(group=ENTRY_POINT_GROUP, name=kind)
    for entry in found:
        renderer = entry.load()
        if callable(renderer):
            return renderer  # type: ignore[no-any-return]
    package = PROVIDERS.get(kind, "a package that renders it")
    raise SceneRendererMissingError(
        f"no renderer for {kind!r} scenes is installed; install {package}"
    )
