# video-editor-skill-motion-graphics

Motion graphics skill for the video editor engine. Builds HyperFrames
composition plans (and falls back to ffmpeg overlays when HyperFrames is
unavailable) for three template kinds:

- `code_highlight` — animated code snippet with line highlighting
- `diagram` — node/edge diagram with title and arrows
- `callout` — lower-third style banner with name + role

## Usage

```python
from motion_graphics.motion_graphics import (
    MotionGraphicsSkill, CalloutSpec, CodeHighlightSpec, DiagramSpec
)
from video_editor_state.job import JobContext

ctx = JobContext(job_id="demo", work_root="./work", output_root="./out")
skill = MotionGraphicsSkill(ctx)

# Code highlight
code_plan = skill.build_code_highlight_plan(CodeHighlightSpec(
    title="for loop",
    code="for i in range(3):\n    print(i)",
    highlight_lines=[1],
    duration=4.0,
))
code_mp4 = skill.render(code_plan)

# Diagram
diagram_plan = skill.build_diagram_plan(DiagramSpec(
    title="Pipeline",
    nodes=[
        {"id": "src", "label": "Source", "x": 100, "y": 100},
        {"id": "out", "label": "Output", "x": 400, "y": 100},
    ],
    edges=[{"from": "src", "to": "out"}],
    duration=3.0,
))
diagram_mp4 = skill.render(diagram_plan)
```

## Motion quality (no freeze-frames, narration-synced)

Diagrams and code highlights get an ambient background (`ambient="glow"`,
`"stars"` or `"none"`) and a slow `camera_drift` pan by default, so a held
shot never reads as a freeze. Time each reveal to the narration with `at`
(seconds) and pulse a node on cue with `emphasize=[t, ...]`. Edges draw on
and stop at the node outlines; `arrow=True` adds a head. Circle nodes are
shaded as spheres and take `ring=True` for a planetary ring.

```python
from video_editor_core.cues import resolve_cues

t = resolve_cues(scene_words, {"pull": "pulls", "side": "sideways"})
DiagramSpec(
    ambient="stars",
    nodes=[{"id": "pull", "label": "Gravity pulls in", "at": t["pull"], ...},
           {"id": "move", "label": "Planet moves sideways", "at": t["side"], ...}],
    edges=[{"from": "pull", "to": "move", "arrow": True, "at": t["side"] - 0.25}],
)
```

See `docs/VIDEO_MOTION_QUALITY.md`.

## Protocol

Implements the project `SkillProtocol` with `name` property and `run()` method.
