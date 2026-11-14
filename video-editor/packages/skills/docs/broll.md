# video-editor-skill-broll

B-roll skill for the video editor engine. Reads a transcript + base video,
identifies moments that benefit from B-roll coverage, searches stock providers
(Pexels → Pixabay → Coverr → Mixkit) or generates a placeholder, and overlays
the clips on top of the base video.

## Usage

```python
from video_editor_state.job import JobContext
from broll.broll import BrollSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = BrollSkill(ctx, providers=["pexels", "pixabay"])
result = skill.run("locked.mp4", transcript)
print(result["output"])  # ./work/broll/with_broll.mp4
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
