# What the Engine Cannot (Yet) Do

This document lists request shapes that will **not** yield correct results today.
It is updated alongside the template system so users can self-triage before
starting a job.

## Format of each entry

- **Request** — the user ask
- **Why it fails** — the current limitation
- **Nearest supported template** — the closest template to use instead
- **Workaround / future direction** — how to get close now, or when it may land

## Unsupported request types

### 1. Lip-sync dubbing

- **Request:** "Dub this video so the speaker's mouth matches the new language."
- **Why it fails:** The engine replaces the audio track with TTS; it does not
  alter mouth shapes or perform facial animation.
- **Nearest supported template:** `multi_language_dub` (audio-only replacement)
- **Workaround:** Use `multi_language_dub` for captions + new VO. Export the
  result to a dedicated lip-sync tool if mouth-sync is required.

### 2. Speaker / face replacement

- **Request:** "Put my face on this actor in the video."
- **Why it fails:** No face-swapping, identity-transfer, or actor-replacement
  provider is wired in.
- **Nearest supported template:** none
- **Workaround:** Generate the needed scenes from scratch with
  `explainer_reel` / `product_spot`.

### 3. Precise physical camera control

- **Request:** "Dolly left, then crane up, then rack focus on the product."
- **Why it fails:** Generated visuals come from text/image prompts. Camera
  motion is limited to Ken Burns pans/zooms on static images and the motion
  style chosen by the generative video provider (HyperFrames / FAL).
- **Nearest supported template:** `image_story`, `product_spot`
- **Workaround:** Describe the desired shot in the prompt and accept the
  provider's interpretation; or pre-render the camera move externally and
  supply it as a video asset.

### 4. True multi-angle / multi-speaker switching

- **Request:** "I have three camera angles and two speakers; auto-switch between
  them."
- **Why it fails:** `podium_cut` processes exactly one source video. The v1
  `raw_batch` template only analyzes and cuts the first clip it finds.
- **Nearest supported template:** `podium_cut` for a single angle,
  `raw_batch` for the first clip only
- **Workaround:** Concatenate or pre-edit the multi-angle source into one
  input video, or run one job per angle and assemble externally.

### 5. Screen-capture generation

- **Request:** "Create a tutorial that shows clicks, typing, and UI navigation."
- **Why it fails:** The engine cannot record or generate screen captures.
- **Nearest supported template:** `training_module`
- **Workaround:** Record the screen yourself, drop the clip into a directory,
  and use the v2 skills pipeline, or use `training_module` for the
  diagram/narration segments only.

### 6. Live event / real-time editing

- **Request:** "Cut this live stream as it happens."
- **Why it fails:** All pipelines are batch/offline. There is no streaming
  ingest or real-time renderer.
- **Nearest supported template:** none
- **Workaround:** Record the stream to a file, then use `podium_cut` or
  `raw_batch`.

### 7. Long-form script with exact scene timing

- **Request:** "My 20-minute script must hit scene changes at exact timestamps."
- **Why it fails:** The scene planner estimates durations from paragraph
  breaks and provider outputs; it does not lock to wall-clock timestamps unless
  a forced-alignment transcript is supplied.
- **Nearest supported template:** `explainer_reel`, `voice_canvas`
- **Workaround:** Provide a word-level transcript JSON or forced-alignment
  file, or break the script into short per-scene markdown files and run
  `voice_canvas` with a known audio duration per scene.

### 8. Direct image upload → animated montage

- **Request:** "Here are 10 product photos; animate them into a video."
- **Why it fails:** There is no image-upload provider. Static images are
  either described and regenerated, or manually referenced from the work tree.
- **Nearest supported template:** `image_story`
- **Workaround:** Copy the images into `projects/<job>/assets/` and reference
  them in `concept.md`, or describe the shots and let the image provider
  regenerate visuals.

### 9. Full song generation with lyrics

- **Request:** "Generate a 3-minute custom song with vocals and lyrics."
- **Why it fails:** ElevenLabs music generation produces short instrumental
  beds, not sung vocals or full structured songs. TTS is speech-only and
  cannot sing on melody.
- **Nearest supported template:** `social_hook`, `product_spot`
- **Workaround:** Use ElevenLabs music for short beds, or license/compose the
  song externally and supply it as a music asset.

### 10. 3D / VFX-heavy sequences

- **Request:** "A photorealistic CGI dragon flying through a city."
- **Why it fails:** Providers are 2D generative video/image models. There is
  no 3D scene graph, physics simulation, or VFX compositor.
- **Nearest supported template:** `explainer_reel`, `social_hook`
- **Workaround:** Describe the shot and accept the provider's interpretation;
  for repeatable 3D, render in a 3D tool and import the video.

### 11. Multi-output aspect ratios in one job

- **Request:** "Give me the same video in 16:9, 9:16, and 1:1 from one command."
- **Why it fails:** A job has one `aspect_ratio`. Rerendering for other aspects
  requires re-running the pipeline with a different ratio.
- **Nearest supported template:** any template
- **Workaround:** Run the job once per aspect ratio, or post-process a
  high-resolution master with center-crop/pad externally.

### 12. Interactive / branching video

- **Request:** "A choose-your-own-path video with clickable branches."
- **Why it fails:** Output is a flat MP4. There is no interactive layer,
  hotspots, or branching logic.
- **Nearest supported template:** none
- **Workaround:** Build interactivity in a separate player/framework and use
  the engine to render the individual video branches.

### 13. Guaranteed factual accuracy in generated visuals

- **Request:** "Generate a chart showing Q3 revenue of $4.2M."
- **Why it fails:** `data_viz` renders numbers that are supplied in the script,
  but image/video generation can hallucinate text, labels, or counts.
- **Nearest supported template:** `training_module`
- **Workaround:** Use `data_viz` for charts and double-check rendered text with
  the QA skill; or export data visualizations from a spreadsheet/tool and
  import them as images.

### 14. Exact font / logo reproduction without a brand file

- **Request:** "Use my brand font and exact Pantone colors."
- **Why it fails:** Default captions/graphics use engine presets. Brand
  fonts/colors only apply when a valid `presets/brand.json` is present.
- **Nearest supported template:** `brand_promo`
- **Workaround:** Create `presets/brand.json` with the exact font path, colors,
  and logo; then run `brand_promo`.

### 15. Negative prompts / strict content blocking

- **Request:** "Make sure no hands, no text, and no faces appear anywhere."
- **Why it fails:** Providers receive positive prompts only; there is no
  negative-prompt channel or classifier-based rejection step.
- **Nearest supported template:** any template
- **Workaround:** Iterate on prompts and use the QA skill / visual spot-checks
  to catch violations; manually regenerate failed scenes.

## Requests that may partially work but need manual cleanup

| Request | What usually works | What needs manual cleanup |
|---------|-------------------|---------------------------|
| Logo overlay with transparency | PNG logo + brand file | Exact placement may need tuning |
| Music ducking under speech | Sidechain compressor in Saju path | Loudness balance per video |
| Captions on fast speech | Word-level timing | Long lines may overflow narrow aspect ratios |
| Generated b-roll for voiceover | Scene planning | Some scenes may need prompt rewrites |
| Batch render of many jobs | `raw_batch` / queue | Per-job review is still required |

## How this list is maintained

1. Every new unsupported category discovered during QA is added here.
2. When a limitation is removed, the entry is moved to a "Recently enabled"
   section or deleted.
3. New templates are added only if they remove one or more entries from this
   document.
