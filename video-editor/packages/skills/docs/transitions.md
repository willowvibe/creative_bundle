# video-editor-skill-transitions

Auto-select and apply cross-segment transitions for the video editor engine.

## Usage

```python
from video_editor_state.job import JobContext
from transitions.transitions import TransitionSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = TransitionSkill(ctx, default="auto", duration=0.3)
result = skill.run(["seg1.mp4", "seg2.mp4"])
print(result["output"])  # ./work/transitions/with_transitions.mp4
```

Heuristic selection rules:

- Speech-to-speech segments get a hard cut.
- B-roll / calm / music segments get a fade.
- Action / montage / beat segments get a slide.
- Scene-change labels get a wipe.

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
