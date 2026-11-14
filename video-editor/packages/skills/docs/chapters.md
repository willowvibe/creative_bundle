# video-editor-skill-chapters

Chapter generation skill for the video editor engine. Analyzes a transcript to
detect topic boundaries and emits YouTube-formatted chapter markers.

## Usage

```python
from video_editor_state.job import JobContext
from chapters.chapters import ChapterSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = ChapterSkill(ctx)
result = skill.run(transcript)
print(result["chapters_txt"])  # YouTube chapter description
print(result["chapters_json"])  # structured JSON
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
