# Captions Skill

Burn word-level animated captions onto a graphics composition. The skill reads
the current EDL and transcript, remaps word timings to the cut timeline, groups
words into lines per segment, and renders each line as a timed text overlay.

## What it does

1. Loads the graphics composition plan (`graphics/plan.json`) produced by the
   Graphics skill.
2. Loads the transcript and the current EDL.
3. Remaps word timestamps from source time to cut timeline time using
   `video_editor_ffmpeg.captions.remap_transcript_to_cuts`.
4. Groups words into caption lines per segment, controlled by `max_chars` and
   `max_duration` in the caption style.
5. Replaces any existing `captions` placeholder layer in the composition with
   timed `caption_line` text layers.
6. Re-renders through the Graphics skill, which uses the segment render cache so
   unchanged segments are reused automatically.

## CLI usage

```bash
# After running graphics on a job, burn captions on top
ve captions <job-id> --work-root ./projects --output-root ./outputs

# Specify a custom output filename
ve captions <job-id> --output-name captions_final.mp4
```

## Programmatic usage

```python
from pathlib import Path
from graphics.graphics import GraphicsSkill
from captions.captions import CaptionsSkill
from video_editor_state.job import JobContext

ctx = JobContext(job_id="demo", work_root=Path("./projects"), output_root=Path("./outputs"))
graphics = GraphicsSkill(ctx, presets_dir=Path("presets"))
graphics.run(preset_name="short_explainer")

captions = CaptionsSkill(ctx, graphics_skill=graphics)
output = captions.run(output_name="with_captions.mp4")
```

## Caption style

`CaptionStyle` controls font, colors, border, animation, and line breaking.
**The default is the karaoke look** (`CaptionStyle()` equals
`CaptionStyle.preset("karaoke")`, see below). For the original Coolvetica
outlined pop-in style, use `CaptionStyle.preset("pop_in")` or set a brand's
`defaults.caption_animation: pop_in`.

```python
from captions.models import CaptionStyle

style = CaptionStyle(
    font="DejaVuSans-Bold",
    font_size=72,
    font_color="white",
    border_width=4,
    border_color="black",
    max_chars=32,
    max_duration=5.0,
    animation="pop",
)
```

## Output

- `work/<job-id>/captions/with_captions.mp4` (or the requested output name)
- `work/<job-id>/captions/captions.json` — manifest with style, segment ids, and
  the path to the output video
- Updated `work/<job-id>/graphics/plan.json` with timed `caption_line` layers

## Cache behavior

The skill reuses the same `SegmentCache` as the Graphics skill under
`work/<job-id>/.cache/graphics`. Re-running `ve captions` with the same inputs
hits the cache for every segment that has not changed, so only edits that affect
segment content trigger a re-render.

## Safe area

Caption bands are moved inside the platform safe area: the bottom 20 % of a
9:16 frame belongs to TikTok/Shorts UI (`video_editor_core.safe_area`). Set
`respect_safe_area=False` to keep the preset position, or set `bottom_padding`
to place the band yourself.

## Karaoke preset (the default)

`CaptionStyle.preset("karaoke")` gives Montserrat ExtraBold with a soft
shadow, short lines (≤34 characters, ≤2.6 s) and per-word timing. On the
HyperFrames path, words not yet spoken sit at `upcoming_opacity` (0.6), the
spoken word takes `highlight_color` with a small pop, and each line slides
in. Build render-graph layers with `caption_graph_layers(lines, rect, style)`.
Override any field, for example `CaptionStyle.preset("karaoke",
upcoming_opacity=1.0)`. See `docs/VIDEO_MOTION_QUALITY.md`.
