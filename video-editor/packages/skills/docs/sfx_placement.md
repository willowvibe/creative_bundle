# video-editor-skill-sfx-placement

Context-aware sound-effect placement skill for the video editor engine.
Detects SFX-worthy moments from a transcript, maps them to SFX prompts,
downloads/generates the sounds, and mixes them into the video's audio track.

## Usage

```python
from video_editor_state.job import JobContext
from sfx_placement.sfx_placement import SfxPlacementSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = SfxPlacementSkill(ctx, provider="elevenlabs_sfx", gain_db=-8.0)
result = skill.run("video.mp4", transcript, output_name="with_sfx.mp4")
print(result["output"])  # ./work/sfx_placement/with_sfx.mp4
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
