"""Core finds out-of-package scene renderers by entry point (maps live in video-editor-maps)."""

from __future__ import annotations

import pytest
from video_editor_core.scene_renderers import SceneRendererMissingError, scene_renderer
from video_editor_maps.scenes import render_map


def test_map_scenes_resolve_to_the_maps_package():
    assert scene_renderer("map") is render_map


def test_an_unknown_scene_type_names_what_to_install():
    with pytest.raises(SceneRendererMissingError, match="no renderer for 'hologram'"):
        scene_renderer("hologram")
