# Typographic charts: icons, waterfall, line, donut

Four additions to `ve explain` typographic scenes for the DeveeKitchen cost teardown
(`../video-projects/videos/2026-11-25/tiffin-centre-teardown/beats.md`), reusable by any explainer.
All render offline as HTML/CSS (seekable CSS animation, no JS), follow the existing layout pattern in
`packages/core/src/video_editor_core/typographic_scenes.py` (pure layout functions returning `_Item`s
with cues, dispatched by `_LAYOUTS`, shared `_base_css`, `render_typographic` resolving cue delays),
and raise `SceneRenderError` on bad props like the existing layouts. New layout names must be added to
`VALID_LAYOUTS` in `packages/pipelines/src/video_editor_pipelines/explainer_script.py`. Numbers display
with Indian grouping and `₹` like the existing count-up. Telugu labels must render (HTML-escaped text,
bundled fonts + browser fallback). Run tests with `.venv/bin/python -m pytest <file> -q`.

## 1 E1 Icon on big_stat
files: packages/core/src/video_editor_core/typographic_scenes.py, packages/core/src/video_editor_core/icons/__init__.py, tests/test_typographic_scenes.py, docs/templates.md
do: Optional `icon: <name>` prop on `big_stat` (and accepted on `bar_compare` bars as `{label, value, unit, icon}`). Icons are the vendored Lucide SVGs in `packages/core/src/video_editor_core/icons/lucide/*.svg` (ISC; LICENSE alongside; keep the licence comment). Add a small loader `icons/__init__.py` with `icon_svg(name) -> str` that reads the file, strips the XML comment, sets `stroke` to the design accent via CSS (`currentColor`) and raises a clear error listing available names on a miss. Render the icon above the headline (~140 px on 16:9), fading/scaling in with the headline's cue. Make sure the SVG files ship with the package (package data in the core package's pyproject if needed). Document the prop and the icon list in docs/templates.md.
done-when: `.venv/bin/python -m pytest tests/test_typographic_scenes.py -q`
difficulty: easy

## 2 E2 Waterfall layout
files: packages/core/src/video_editor_core/typographic_scenes.py, packages/pipelines/src/video_editor_pipelines/explainer_script.py, tests/test_typographic_scenes.py, docs/templates.md
do: `layout: waterfall` with `start: {label, value}`, `steps: [{label, value, cue?}]` (2–8 steps, values signed; negative steps hang down from the running level), `end: {label}` whose bar is computed as start + sum(steps). Optional `unit` (e.g. `₹`). Bars left to right; each step bar grows from the previous level when its label is spoken (default cue = its label, `cue: false` allowed), with a thin connector line between levels; negative bars in the design's negative colour, start/end bars in the accent; value labels above each bar with Indian grouping. Validate in `explainer_script.py` that an explicit `end.value`, if given, equals start + sum(steps) (to the rupee) and fail the parse otherwise, like the ledger total check. Document in docs/templates.md with an example.
done-when: `.venv/bin/python -m pytest tests/test_typographic_scenes.py tests/test_explainer_script.py -q`
needs: 1
difficulty: medium

## 3 E3 Line layout with crossing and moving dot
files: packages/core/src/video_editor_core/typographic_scenes.py, packages/pipelines/src/video_editor_pipelines/explainer_script.py, tests/test_typographic_scenes.py, docs/templates.md
do: `layout: line` drawing y = slope·x + intercept or a `points: [[x, y], ...]` polyline (3–30 points) on labelled axes (`x_label`, `y_label`, optional `x_range`, `y_range`, `unit`). A zero/baseline rule is drawn. Optional `mark: {x, label, cue?}` draws a vertical dashed marker and label (e.g. break-even at x=152). Optional `dots: [{x, label, cue?}]` (1–3): the first dot appears at its x on the line; each following dot slides along the line from the previous dot's x to its own on its cue (CSS keyframes on custom properties so it seeks deterministically, as map journeys do). The line draws on with a dash reveal at scene start. Values on the dot labels use Indian grouping. Document with the break-even example.
done-when: `.venv/bin/python -m pytest tests/test_typographic_scenes.py tests/test_explainer_script.py -q`
needs: 2
difficulty: medium

## 4 E4 Donut layout
files: packages/core/src/video_editor_core/typographic_scenes.py, packages/pipelines/src/video_editor_pipelines/explainer_script.py, tests/test_typographic_scenes.py, docs/templates.md
do: `layout: donut` with `parts: [{label, value, cue?}]` (2–6, positive) and optional `center: {value, label}` and `unit`. SVG ring; each segment sweeps in (stroke-dasharray reveal) on its cue, in order, with a legend row (label + value) appearing with it; centre value counts up like big_stat. Validate that an explicit `center.value`, if given, equals the sum of parts to the shown decimals. Document with an example.
done-when: `.venv/bin/python -m pytest tests/test_typographic_scenes.py tests/test_explainer_script.py -q`
needs: 3
difficulty: easy
