# video-editor-skill-screen-tutorial

Screen tutorial polish skill for the video editor engine. Detects cursor
position and click events from a screen recording, then renders zoom-to-cursor,
click highlight rings, and keystroke overlays.

## Usage

```python
from video_editor_state.job import JobContext
from screen_tutorial.screen_tutorial import ScreenTutorialSkill

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = ScreenTutorialSkill(ctx, enable_zoom=True, enable_clicks=True, enable_keystrokes=True)
result = skill.run("screen.mp4")
print(result["output"])  # ./work/screen_tutorial/polished.mp4
```

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
