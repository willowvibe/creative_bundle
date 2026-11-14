# video-editor-comfystudio-bridge

Bidirectional import/export between the video editor engine's `ScenePlan` and a ComfyStudio `project.comfystudio` timeline project.

The bridge lets a user review or trim the generated scene plan inside ComfyStudio's free multi-track timeline GUI, then re-import the edited timeline for final render.

## Usage

```python
from pathlib import Path
from video_editor_comfystudio_bridge.exporter import export_to_comfystudio
from video_editor_engine.models.scene_plan import ScenePlan

project_dir = export_to_comfystudio(
    scene_plan,
    project_root=Path("./outputs/my-job/comfystudio"),
    fps=30,
    width=1920,
    height=1080,
)
```

Open `project_root` in ComfyStudio, edit the timeline, save, then re-import:

```python
from video_editor_comfystudio_bridge.importer import import_from_comfystudio

updated_plan = import_from_comfystudio(
    project_dir / "project.comfystudio",
    original_scene_plan,
)
```
