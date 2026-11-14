# Video motion quality

How the engine keeps generated videos from reading as animated slides, and
how to use those features when you build a video. Added 2026-10-22 (#240–#246).

## The four rules

1. **Never freeze.** A frame that holds for ≥1.5 s reads as a stall. Every
   motion-graphics scene has ambient motion: a drifting glow or a starfield,
   a slow camera pan, and breathing glows on circles. Stills get varied
   Ken Burns moves.
2. **The picture follows the voice.** Reveal or highlight each element when
   the narration *names* it, not on a fixed stagger from scene start.
3. **Captions lead the eye.** Use short 2–5 word lines. Dim the words not
   yet spoken, and pop and highlight the word being spoken.
4. **Fonts and captions come from the engine.** Hand-written HTML must embed
   fonts with `video_editor_core.fonts` and get captions from the captions
   skill. Anything else drifts, as when human-body fell back to Times (#243).

Music is deliberately out of scope here.

## Narration cues — `video_editor_core.cues`

```python
from video_editor_core.cues import resolve_cues, spotlight_layer

# words: [{"word"|"text", "start", "end"}, ...] from faster-whisper,
# ElevenLabs alignment or WhisperX, in the timebase you want back.
times = resolve_cues(words, {"pull": "pulls", "side": "sideways"},
                     after=scene_start, before=scene_end)
# -> {"pull": 1.03, "side": 3.93}   (80 ms lead; phrases that are never
#    spoken are omitted, so use times.get(key, fallback))
```

Matching ignores case and punctuation and is prefix-based per token, so
`"orbit"` also matches "orbits". Multi-word phrases (`"sixty percent"`)
match consecutive words.

**Get word timings before the visuals render.** Transcribe each narration
clip, or the whole track, first, then build the scenes. See
`videos/2026-10-22/solar-system-explainer/build/build.py`.

## Motion graphics (`skills/motion_graphics`)

| Feature | Where | Notes |
|---|---|---|
| `ambient="glow" \| "stars" \| "none"` | `DiagramSpec`, `CodeHighlightSpec` | Default `glow`. `stars` is a three-depth parallax starfield with twinkle. Deterministic per plan. |
| `camera_drift=True` | same | A slow ~26 px pan across every content layer (CSS `translate`). |
| `at` | node / edge / orbit dict | Reveal time in seconds from the clip start; overrides the default stagger. |
| `emphasize=[t, ...]` | node dict | A scale + brightness pulse at each time; the node's label pulses with it. |
| `arrow=True` | edge dict | The edge draws on from source to target, and an arrow head fades in. Edges stop at the node outline. |
| `ring=True \| "<css colour>"` | circle node | Planetary ring (Saturn). |
| `shading=True` (default) | circle node | Sphere highlight + terminator shadow. |

Entrance animations use `transform`, cue pulses use `scale`, and the camera
pan uses `translate`. These are independent CSS properties, so all three run
at once without overriding each other.

## Spotlights on stills and charts

When a scene is a static image (a matplotlib chart, an AI still), ring the
part being talked about with a render-graph `shape` layer:

```python
spot = spotlight_layer("calcium", Rect(x=76, y=174, width=408, height=175),
                       start=times["calcium"], end=next_cue, color="#FF6B6B")
graph = RenderGraph(..., overlays=[spot, *caption_layers])  # captions last = on top
```

`shape` layers route the graph to HyperFrames automatically. Keep Ken Burns on
the still subtle (≤1.5 % scale) so the spotlight stays aligned with the
chart. Example: `videos/2026-10-22/human-body/build/overlays.py`.

## Captions

Karaoke is the default `CaptionStyle()`, the same as `CaptionStyle.preset("karaoke")`. It gives Montserrat ExtraBold with a soft
shadow, ≤34 characters per line and ≤2.6 s per line. Upcoming words sit at 0.6
opacity (`upcoming_opacity`), the spoken word turns `highlight_color` with a
small pop, and each line slides in. Set `upcoming_opacity=1.0` to turn the
dimming off.

Karaoke layers set `requires_motion`, so captioned render graphs go through
HyperFrames. `CaptionStyle.preset("pop_in")` restores the old Coolvetica style,
which can render on the FFmpeg path.

## Stills (`scene_plan_composition`)

Ken Burns moves cycle zoom-in → pan-right → zoom-out → pan-left, eased
in-out, so consecutive scenes never repeat the same push.

## Safe areas (`video_editor_core.safe_area`)

On TikTok, Shorts and Reels the app draws its username, description and action
buttons over the bottom of a vertical video, so text placed there is hidden.

| Canvas | Keep clear |
|---|---|
| Vertical (height ≥ 1.2 × width: 9:16, 4:5) | top 8 %, sides 8 %, **bottom 20 %** |
| Landscape / square | 5 % title-safe on every side |

- `safe_box(w, h)` returns the safe area. `fit_box(box, w, h)` moves a box
  inside it and shrinks the box only when it is too big to fit.
- The graphics planner fits every text layer (hook, captions band, lower
  third, chapter title), and `CaptionsSkill` fits its caption band
  (`CaptionStyle.respect_safe_area`, default on). An explicit
  `bottom_padding` overrides it.
- All shipped presets keep their text bands inside the safe area, and a test
  enforces this for new presets.
- The pipeline logs `text_outside_safe_area` for render-graph text that
  breaks the rule. Hand-built graphs should run
  `safe_area_violations(graph.overlays, w, h)` themselves.

## QA gate

`QASkill` adds `no_frozen_frames` (FFmpeg `freezedetect`, n=0.003, d=1.5 s).
It is advisory by default because screen recordings legitimately hold. For
explainers and motion graphics, make it a hard gate:

```python
QASkill(ctx, freeze_max_seconds=1.5)
```

QA also adds `text_in_safe_area` when the job has a
`plan/render_graph.json`. It is advisory by default, and
`QASkill(ctx, safe_area_strict=True)` makes it fail.

Or check freezes by hand: `ffmpeg -i out.mp4 -vf freezedetect=n=0.003:d=1.5 -an -f null -`.
