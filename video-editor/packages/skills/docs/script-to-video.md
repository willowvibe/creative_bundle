---
type: code
description: Build and operate the script-to-video generation workflow.
---

# Script-to-Video Skill

Use this skill when turning a written script into a rendered video through
`apps/engine` and `apps/cli`. Composition is HyperFrames HTML; Remotion is
retired.

## Pipeline order

1. **plan-script** — LLM decomposes a markdown script into `scene_plan.json`.
2. **assets** — resolve TTS, images, video clips, music and SFX for each scene
   and write the enriched plan.
3. **render** — compile the plan to a HyperFrames project and render the MP4.
4. **captions** — optional word-level captions over the rendered video.
5. **verify** — requirements gate for an exported job.

Storyteller templates add a multi-voice planner on top:
`storyteller_planner.build_storyteller_scene_plan()` (3 image × 3 video tiers).

## Scene plan schema

Models live in `apps/engine/src/video_editor_engine/models/scene_plan.py`.

```json
{
  "job_id": "demo",
  "script": "# Demo\n...",
  "aspect_ratio": "9:16",
  "music_mood": "cinematic",
  "scenes": [
    {
      "id": "scene_001",
      "type": "narration",
      "duration_seconds": 4.6,
      "narration": "Spoken line for this scene.",
      "visual_description": "wide shot of a coastal kingdom at dawn",
      "props": {"needs_motion": false, "render_track": "image"},
      "assets": []
    }
  ]
}
```

- `type` must be one of `opening`, `narration`, `narrative`, `broll`, `closing`,
  `transition`, `music_visual`, `story`.
- `duration_seconds` is the rendered length; for spoken scenes the planner sets
  it to the TTS duration × 1.15.
- `assets` is filled by `ve assets`: `tts`, `image`, `video`, `music`, `sfx`.
  A motion scene keeps both its `video` and its `image`; every renderer prefers
  the video, and the image is the fallback.

## Aspect ratios

| Aspect | Canvas |
|--------|--------|
| 16:9 | 1920 × 1080 |
| 9:16 | 1080 × 1920 |
| 1:1 | 1080 × 1080 |

## CLI

```bash
ve plan-script fixtures/scripts/demo_explainer.md demo
ve assets demo                      # writes scene_plan_with_assets.json
ve render demo                      # HyperFrames MP4
ve requirements demo --min-duration 30 --max-duration 45 --require-aspect 9:16
ve verify demo
```

`ve assets` takes `--tts-provider`, `--image-provider`, `--video-provider`
(default `auto` = scored selection) and `--budget`. Storyteller templates use
`ve run --template storyteller_* --image-tier/--video-tier
{budget,standard,premium}` instead.

## Model routing

Providers come from `config.yaml` (see `docs/MODEL_ROUTING.md`). Scene
decomposition defaults to `minimax-m3:cloud`; visuals default to PoYo.ai with
FAL as fallback. Template and capability details are in `docs/templates.md`
and `docs/Templates_capabilities.md`.
