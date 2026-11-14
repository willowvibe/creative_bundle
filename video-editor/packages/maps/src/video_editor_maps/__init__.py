"""Map scenes for explainers: bundled Natural Earth geometry (:mod:`video_editor_maps.geo`) and the
map plate renderer (:mod:`video_editor_maps.scenes`).

Core never imports this package. Its scene-plan adapter finds :func:`~video_editor_maps.scenes.render_map`
through the ``video_editor.scene_renderers`` entry point (see ``video_editor_core.scene_renderers``).
"""
