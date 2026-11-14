# video-editor-fablecut-bridge

Bidirectional import/export between the video editor engine's `ScenePlan` and a [FableCut](https://github.com/ronak-create/FableCut) `project.json` timeline.

The bridge lets a user review or trim the generated scene plan inside FableCut's browser-based multi-track timeline, then re-import the edited JSON for final render.

## Usage

```python
from pathlib import Path
from video_editor_fablecut_bridge.exporter import export_to_fablecut
from video_editor_engine.models.scene_plan import ScenePlan

project_dir = export_to_fablecut(
    scene_plan,
    project_root=Path("./outputs/my-job/fablecut"),
    fps=30,
    width=1920,
    height=1080,
)
```

Open `project.json` in FableCut (`node server.js` from the project root), edit the timeline, save, then re-import:

```python
from video_editor_fablecut_bridge.importer import import_from_fablecut

updated_plan = import_from_fablecut(
    project_dir / "project.json",
    original_scene_plan,
)
```

## CLI

```bash
ve export-fablecut my-job --project-dir ./outputs/my-job/fablecut
ve import-fablecut my-job --project-dir ./outputs/my-job/fablecut
```
