# video-editor-maps

Map scenes for `ve` explainers.

- `video_editor_maps.geo`: bundled Natural Earth data (countries, regions, rivers, places, the India
  point-of-view edition), the Mercator `View`, gazetteer lookups and land snapping.
- `video_editor_maps.scenes`: `render_map`, the map plate (markers, routes, regions, camera zooms).

It depends on `video-editor-core` (designs, cues, motion, paper tokens). Core does not import it: the
scene-plan adapter loads `render_map` through the `video_editor.scene_renderers` entry point, and an
import-linter contract keeps it that way. Regenerate the bundled data with `scripts/compact_natural_earth*.py`.
