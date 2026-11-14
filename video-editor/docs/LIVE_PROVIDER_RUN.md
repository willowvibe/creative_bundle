# Live-Provider End-to-End Run

This document describes how to execute a complete script-to-video render using
real cloud providers: **ElevenLabs** (or **Sarvam**) for TTS and **PoYo.ai**
(primary) for images/video. FAL.AI and the opt-in **OpenRouter** generation
gateway remain available as alternatives. The **TypeSafe/JEV** classifier is
optional and used automatically for per-item judgments when its key is set.

## Prerequisites

Set these environment variables (copy `.env.example` to `.env` and fill in real keys):

```bash
export OPENROUTER_API_KEY="..."        # default LLM provider in config.yaml
export OLLAMA_CLOUD_API_KEY="..."      # free-mode LLM provider (config.free.yaml)
export ELEVENLABS_API_KEY="..."        # for narration TTS
export SARVAM_API="..."                # alternative TTS (Indian languages)
export POYO_API_KEY="..."              # for visual generation (primary)
export FAL_KEY="..."                   # for visual generation (fallback)
export TYPESAFE_API_KEY="..."          # optional: enables the JEV classifier
```

## One-shot command chain

```bash
JOB_ID="live_demo_$(date +%s)"

# 1. Turn a markdown script into scene_plan.json
ve plan-script fixtures/scripts/demo_explainer.md \
  --job-id "$JOB_ID" \
  --config config.yaml \
  --aspect-ratio 16:9 \
  --music-mood upbeat

# 2. Resolve real assets (TTS + images)
ve assets \
  --job-id "$JOB_ID" \
  --config config.yaml \
  --tts-provider elevenlabs_tts \
  --image-provider poyo_ai

# 2b. (Optional) Resolve real assets with VIDEO generation.
# Adds a VIDEO asset to each scene that has a visual_description
# (rendered by PoYo's default video model — see docs/POYO_ROUTING.md).
# Set ``provider_options.video_model`` in config.yaml to override
# the default ``seedance-2-fast`` model.
ve assets \
  --job-id "$JOB_ID" \
  --config config.yaml \
  --tts-provider elevenlabs_tts \
  --image-provider poyo_ai \
  --video-provider poyo_ai

# 2c. (Fallback) Use FAL.AI instead of PoYo.
ve assets \
  --job-id "$JOB_ID" \
  --config config.yaml \
  --tts-provider elevenlabs_tts \
  --image-provider fal_ai \
  --video-provider fal_ai

# 2d. (Opt-in) Use the OpenRouter generation gateway (image/video/music).
# See docs/OPENROUTER_GENERATION.md. PoYo remains the default.
ve assets \
  --job-id "$JOB_ID" \
  --config config.yaml \
  --tts-provider elevenlabs_tts \
  --image-provider openrouter \
  --video-provider openrouter

# 3. Render to MP4
ve render \
  --job-id "$JOB_ID" \
  --output-name live_demo.mp4

# 4. Verify output
ve qa --job-id "$JOB_ID"
```

## Expected result

A `1920x1080` 30 fps MP4 with AAC audio, total duration matching the summed
scene durations (~19 seconds for `fixtures/scripts/demo_explainer.md`).

## Frame-rounding safety

The script-to-video renderer is **HyperFrames** (an HTML composition); the
`engine="auto"` router falls back to a single-pass **FFmpeg** render for simpler
graphs. Each scene's float duration is rounded to the nearest frame and every
audio/visual asset is clipped to the scene's frame count. After a live render,
run `ffprobe -v error -show_entries format=duration` on the output and confirm it
is within one frame (33 ms at 30 fps) of the expected sum.

## Troubleshooting

- **PoYo.ai image/video generation fails**: ensure `POYO_API_KEY` is set and
  the default models in `config.yaml` are valid PoYo model IDs. See
  `docs/POYO_ROUTING.md` for the current model list.
- **FAL.AI image generation fails**: ensure `FAL_KEY` is set and the default
  image model in `config.yaml` is a valid endpoint.
- **OpenRouter generation fails**: ensure `OPENROUTER_API_KEY` is set and the
  `openrouter` provider's `video_model`/`music_model` are valid OpenRouter slugs.
  See `docs/OPENROUTER_GENERATION.md`.
- **TTS audio too short/long**: narration is clipped/extended to the scene frame
  count at render time. Drift beyond one frame should be reported as a bug.
- **Empty assets cache / 404s**: verify the cache path is
  `projects/{job_id}/.cache/assets` (the renderer mounts it for the composition).
