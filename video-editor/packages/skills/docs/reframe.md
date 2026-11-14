# video-editor-skill-reframe

Smart reframe skill for the video editor engine. Converts horizontal video
to vertical (e.g. 16:9 → 9:16) with face-aware cropping and optional punch-in
zoom.

## Usage

```python
from video_editor_state.job import JobContext
from reframe.reframe import ReframeSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = ReframeSkill(ctx, target_aspect="9:16", enable_zoom=True)
result = skill.run("source.mp4", output_name="reframed.mp4")
print(result)  # ./work/reframe/reframed.mp4
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
