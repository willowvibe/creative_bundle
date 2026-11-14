# Template Capabilities — What Each Template Can and Cannot Do

> **Note (2026-10-16):** Remotion was retired and replaced by the HyperFrames
> HTML renderer (with a single-pass FFmpeg fallback). Where this document says
> "Remotion", read it as the composition renderer (HyperFrames/FFmpeg).

This document is the detailed companion to `docs/templates.md`. It describes, for
each of the fourteen supported templates:

1. **What it produces** — the final deliverable and intermediate artifacts.
2. **Inputs it expects** — required and optional, with accepted formats.
3. **Variations it accepts** — aspect ratios, presets, provider swaps,
   music moods, and use-case tweaks.
4. **Example commands** — ready-to-run CLI invocations.
5. **Edge cases and guardrails** — what fails, what is silently ignored, and
   what requires manual cleanup.

Templates are declarative recipes consumed by `TemplateRouter`. They do not
render video themselves; they configure the actual pipeline (v1 raw, v2 skills,
or script-to-video) that runs underneath.

---

## 1. `podium_cut`

**One source video → polished talking-head clip.**

### What it can do

- Ingest a single video file, create a proxy, and extract audio.
- Transcribe speech with WhisperX (or use a supplied transcript JSON).
- Analyze silence, filler words, pauses, and speaking pace.
- Generate an LLM edit plan that cuts dead air and repetitive segments.
- Render the locked rough cut, burn in animated word-level captions, apply a
  brand/preset graphics package, optionally mix background music, run QA,
  and export a final MP4 + `report.md` + `decision_log.jsonl`.
- Best for: webinars, interviews, single-speaker presentations, course lectures,
  podcast video, sales recordings.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.mp4`, `*.mov`, `*.mkv`, `*.avi`, `*.webm`, `*.m4v` | The talking-head video. |
| `--transcript` | no | WhisperX-shape JSON `{word, start, end, probability}` | Skip WhisperX if accurate timings already exist. |
| `--preset` | no | name under `presets/` | Graphics/captions look & feel. |
| `--config` | no | `config.yaml` | LLM/provider overrides. |
| brand file | no | `presets/brand.json` or `presets/brands/<job>.json` | Logo, colors, fonts. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--expected-aspect-ratio 16:9` (default) or `9:16`, `1:1`, `4:3` | QA gate checks the final output against this ratio. |
| **Music** | `--music-path ./bed.mp3` or auto-discovered `generated_music.mp3` | MusicSkill ducks the bed under speech. |
| **No music** | `--skip-music` | Bypasses music stage even if a file exists. |
| **QA gate** | `--qa` | Fails the build if audio loudness, black frames, aspect ratio, or caption checks fail. |
| **Loudness target** | `--loudness-target-lufs -16.0` | Default is -16 LUFS; podcast-style content often prefers -14. |
| **Preset** | `--preset long_form` or `short_explainer` | Affects lower-thirds, captions style, pacing hints. |
| **Provider overrides** | not via `--provider` on `ve run` | Use `config.yaml` or the individual v2 skill commands. |

### Example commands

```bash
# Basic — let WhisperX transcribe and edit
ve run ./talk.mp4 --job-id webinar --work-root ./projects --output-root ./outputs

# With a supplied transcript and QA gate
ve run ./talk.mp4 --job-id webinar \
    --work-root ./projects --output-root ./outputs \
    --transcript ./talk.json --qa --loudness-target-lufs -16.0

# Vertical cut for a short-form talking-head clip
ve run ./talk.mp4 --job-id webinar-short \
    --work-root ./projects --output-root ./outputs \
    --expected-aspect-ratio 9:16 --music-path ./short_bed.mp3
```

### Edge cases / guardrails

- **Multi-speaker / multi-angle:** not supported. Only the single source video
  is processed. If you pass a directory, `raw_batch` is selected instead.
- **No speech:** WhisperX returns an empty transcript and `CaptionsSkill`
  raises `CaptionsError`. Use a music-only template if there is no speech.
- **Very short source (< 2 s):** analysis stage may emit zero cuts; the video
  passes through mostly unchanged.
- **Transcript mismatch:** if the supplied JSON timings do not match the audio,
  captions will drift. Use `ve align` first to regenerate accurate timings.

---

## 2. `explainer_reel`

**Markdown script → fully generated explainer video.**

### What it can do

- Read a markdown concept file and decompose it into timed scenes
  (`plan/scene_plan.json`).
- Generate voiceover audio per scene via TTS.
- Generate still images for each scene via an image provider (PoYo, FAL, Pexels,
  stub, etc.).
- Optionally generate motion clips for scenes that need real movement via
  HyperFrames when `--video-provider` is set and `props.requires_motion=true`.
- Compose scenes with Remotion, applying an always-on Ken Burns pan/zoom to
  static images.
- Add background music and per-scene SFX if enabled.
- Burn captions and export the final MP4.
- Best for: product explainers, company overviews, educational content, founder
  narratives, investor updates.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt`, `*.markdown` | The script/concept. |
| `--config` | recommended | `config.yaml` | LLM, provider, and asset settings. |
| `--aspect-ratio` | no | `16:9`, `9:16`, `1:1`, `4:3` | Target composition ratio. |
| `--music-mood` | no | free text, e.g. `"upbeat corporate"` | Guides music generation prompt. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Preset** | `--preset long_form` or `short_explainer` | Long form is default. Short explainer caps scene count hints. |
| **TTS provider** | `config.yaml` → `assets.providers.tts` or `ve assets --tts-provider` | Defaults to `elevenlabs_tts`; `stub_tts` for offline tests. |
| **Image provider** | `ve assets --image-provider poyo_ai`, `fal_ai`, `pexels`, or `stub_image` | PoYo default (`gpt-image-2`) costs ~$0.01 per 16:9 1K low-quality image; Pexels is free stock search; stub generates colored placeholders. |
| **Video provider** | `ve assets --video-provider hyperframes_video`, `pexels`, or `stub_hyperframes_video` | Only emits VIDEO for `props.requires_motion=true`; Pexels returns stock clips. |
| **Music/SFX** | `--music-prompt`, `--music-provider`, `--sfx-provider` | Auto-mixed into the final render via SkillsPipeline. |
| **Forced alignment** | `ve align` before `ve run` | Replaces WhisperX with cheap, accurate timings. |
| **Scene-level control** | edit `plan/scene_plan.json` | The LLM plan is a starting point; manual edits are supported. |

### Example commands

```bash
# Plan → assets → render a long-form explainer
ve plan-script ./concept.md explainer --work-root ./projects --output-root ./outputs
ve assets explainer --image-provider poyo_ai --video-provider poyo_ai
ve render explainer

# One-shot (if your CLI/wrapper chains plan+assets+render)
ve run ./concept.md --job-id explainer \
    --work-root ./projects --output-root ./outputs \
    --template explainer_reel \
    --music-mood "upbeat corporate" \
    --config ./config.yaml
```

### Edge cases / guardrails

- **Empty or very short script:** scene planner may return a single scene.
  Minimum useful script is roughly one paragraph.
- **Visuals from prompts:** the engine does not read your mind. Descriptive,
  concrete visual paragraphs produce better results than abstract marketing copy.
- **Motion flag misuse:** if every scene sets `requires_motion=true`, render
  time and cost increase sharply. The planner is instructed to be selective.
- **No video provider:** motion scenes silently fall back to the still IMAGE
  asset with Ken Burns.

---

## 3. `image_story`

**Script describing images/scenes → Ken Burns montage.**

### What it can do

- Decompose a markdown script into scenes where each scene is intended to be a
  still image.
- Generate narration via TTS.
- Generate images from visual descriptions.
- Apply a cinematic Ken Burns pan/zoom to every static image via Remotion.
- Add cinematic background music.
- Best for: documentary-style photo montages, memorial videos, travel reels,
  non-fiction storytelling, image-heavy presentations.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt`, `*.markdown` | Scene descriptions + narration. |
| reference images | no | copied into work tree | Manual fallback when prompt regeneration is not desired. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Preset** | `--preset long_form` (default) or `short_explainer` | Long form suits storytelling; short explainer is rarely used here. |
| **Music mood** | `--music-mood cinematic` (default), `calm`, `epic`, etc. | Strongly affects the feel of the montage. |
| **Image source** | describe in `concept.md` OR reference local files | Local files must be copied to `projects/<job>/assets/` and referenced manually. |
| **Caption style** | via brand/preset | Bottom or centered captions depending on `CaptionStyle`. |
| **Real motion** | set `props.requires_motion=true` in scene plan + `--video-provider` | Breaks away from pure Ken Burns for specific scenes. |

### Example commands

```bash
# Generate a cinematic photo-story from a script
ve plan-script ./story.md story --work-root ./projects --output-root ./outputs
ve assets story --image-provider poyo_ai
ve render story

# Use reference images already in the work tree
# In concept.md reference them as: ./assets/photo_01.jpg
ve run ./story.md --job-id story \
    --work-root ./projects --output-root ./outputs \
    --template image_story --music-mood calm
```

### Edge cases / guardrails

- **No direct upload provider:** you cannot pass a folder of images and have
  the engine ingest them automatically. You must either describe them or copy
  them into the work tree and reference paths.
- **Image path typos:** if a referenced local image does not exist, the render
  stage fails with a clear file-not-found error.
- **All-motion request:** this template is optimized for still images. If every
  scene needs real motion, consider `explainer_reel` or `voice_canvas` instead.

---

## 4. `voice_canvas`

**Pre-recorded voiceover + script → generated b-roll video.**

### What it can do

- Take an existing voiceover audio file and a matching markdown script.
- Decompose the script into scenes whose durations align to the VO segments.
- Skip TTS because the audio is already supplied.
- Generate still or motion visuals that support the narration (b-roll style,
  not a talking head).
- Add captions timed to the supplied audio (via forced alignment or the script
  itself).
- Add ambient background music and SFX.
- Best for: narrated documentaries, guided tours, audio-first podcasts that need
  visuals, pre-recorded course content, audiobook trailers.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.mp3`, `*.wav`, `*.m4a`, `*.flac`, `*.aac`, `*.ogg` | The voiceover audio. |
| `concept.md` | yes | markdown | Script matching the narration. |
| `--transcript` | no | JSON | Word-level timings; if omitted the engine may use forced alignment or the script. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Preset** | `--preset long_form` (default) | Keeps visuals paced to narration. |
| **Aspect ratio** | `--aspect-ratio 16:9` (default) or `9:16` | Podcast-style often 16:9; social clips 9:16. |
| **Visual style** | `explainer`, `documentary`, `abstract` via prompts | Describe the desired b-roll look in the script. |
| **Music mood** | `--music-mood ambient` (default) | Designed to sit under speech without competing. |
| **Motion scenes** | `--video-provider hyperframes_video` / `pexels` + `props.requires_motion=true` | Use sparingly for movement that supports narration. Pexels returns real stock video clips. |
| **Captions source** | `ve align` first, or `--transcript` | Without word timings, captions may be paragraph-level only. |

### Example commands

```bash
# Use a recorded podcast episode as the backbone
ve run ./episode.mp3 --job-id docu \
    --work-root ./projects --output-root ./outputs \
    --template voice_canvas \
    --config ./config.yaml

# With accurate forced alignment first
ve align docu --audio ./episode.mp3 --text "$(cat script.txt)"
ve run ./episode.mp3 --job-id docu --template voice_canvas --transcript ./projects/docu/transcript/transcript.json
```

### Edge cases / guardrails

- **Script/VO mismatch:** if the script and audio do not match, scene durations
  and captions will drift. The engine does not detect semantic mismatch.
- **Long pauses in VO:** scene planner may create extra short scenes during
  silence; manual editing of `scene_plan.json` is often needed.
- **No TTS fallback:** because the audio is supplied, the engine will not
  generate a replacement. If the audio is corrupt or wrong, fix it externally.

---

## 5. `product_spot`

**Product description → vertical showcase reel.**

### What it can do

- Read a markdown product brief and script.
- Generate a short voiceover via TTS.
- Generate clean product-focused visuals from prompts.
- Optionally add motion clips for product-in-use scenes.
- Add punchy music, SFX, and bold captions.
- Render in 9:16 vertical by default for Reels/TikTok/Shorts.
- Best for: app demos, physical product teasers, feature launches, DTC ads,
  crowdfunding campaign videos.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt` | Product description + hook/problem/solution/CTA script. |
| brand file | no | `presets/brand.json` | Logo, colors, fonts for brand consistency. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--aspect-ratio 9:16` (default) or `16:9` | Social vertical is the primary use case. |
| **Preset** | `--preset short_explainer` (default) | Tuned for fast cuts and bold captions. |
| **Music mood** | `--music-mood upbeat` (default) | Energetic beds suit short spots. |
| **SFX** | `--sfx-provider elevenlabs_sfx` | Adds per-scene sound effects (whoosh, ding, etc.). |
| **Product images** | reference local files in concept.md | If you have real photos, use `image_story` instead. |
| **Voice** | set in `config.yaml` under TTS provider options | ElevenLabs voice_id, stability, similarity_boost. |

### Example commands

```bash
# 9:16 product teaser from a brief
ve run ./product_brief.md --job-id spot \
    --work-root ./projects --output-root ./outputs \
    --template product_spot \
    --music-mood upbeat --sfx-provider elevenlabs_sfx

# 16:9 landing-page version of the same brief
ve run ./product_brief.md --job-id spot-landscape \
    --work-root ./projects --output-root ./outputs \
    --template product_spot --aspect-ratio 16:9
```

### Edge cases / guardrails

- **AI-generated product shots:** the product may not look identical across
  scenes. Add a strong, repeated descriptor in every visual prompt (e.g.
  "matte-black cylindrical speaker, single product on white background").
- **Text on generated images:** image providers may hallucinate text/logos. Use
  the brand overlay layer for reliable logo/text.
- **CTA legibility:** in 9:16, keep CTA text short; the caption engine will
  wrap long lines and may overflow.

---

## 6. `social_hook`

**Short hook script → fast vertical social clip.**

### What it can do

- Read a 15–60 second markdown hook script.
- Generate a punchy voiceover.
- Generate bold, eye-catching visuals.
- Add energetic music, SFX, and animated captions.
- Render in 9:16 vertical by default.
- Best for: TikTok/Reels/Shorts hooks, viral-style explainers, quick tips,
  motivational clips, announcement videos.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt` | Hook + body + CTA script. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--aspect-ratio 9:16` (default) or `16:9` | 9:16 is native social; 16:9 for cross-posting. |
| **Preset** | `--preset short_explainer` (default) | Fast cuts, large captions. |
| **Music mood** | `--music-mood energetic` (default) | High-energy beds work best. |
| **SFX** | `--sfx-provider elevenlabs_sfx` | Strong impact sounds help stop the scroll. |
| **Caption style** | brand/preset driven | Bold, bottom, or pop-on captions depending on preset. |
| **Script length** | 15–60 seconds ideal | Longer scripts may be split into multiple social clips. |

### Example commands

```bash
# Scroll-stopping 9:16 hook
ve run ./hook.md --job-id hook1 \
    --work-root ./projects --output-root ./outputs \
    --template social_hook \
    --music-mood energetic --sfx-provider elevenlabs_sfx

# Cross-post to YouTube as 16:9
ve run ./hook.md --job-id hook1-wide \
    --work-root ./projects --output-root ./outputs \
    --template social_hook --aspect-ratio 16:9
```

### Edge cases / guardrails

- **Script too long:** the planner may compress or truncate beats. Keep the
  script under ~150 words for a 60-second output.
- **No clear CTA:** social clips without a final CTA line may feel unfinished.
  Include an explicit "follow", "click link", or "comment" line.
- **Motion generation cost:** every `requires_motion=true` scene costs a
  HyperFrames render. For social hooks, reserve motion for the opening 3–5
  seconds and use still images for the rest.

---

## 7. `training_module`

**Step-by-step markdown guide → tutorial video with diagrams.**

### What it can do

- Read a numbered markdown guide.
- Decompose each major step into a scene.
- Generate narration via TTS.
- Generate visuals for each step.
- Render **data-visualization charts** from inline `asset_query: data:...`
  syntax using Matplotlib.
- Add calm background music.
- Render in 16:9 for desktop learning.
- Best for: software tutorials (without screen capture), process explainers,
  math/science lessons, corporate SOP videos, certification courses.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt` | Numbered steps with narration + optional chart data. |
| `--config` | recommended | `config.yaml` | LLM and provider settings. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--aspect-ratio 16:9` (default) | Tutorial content is usually landscape. |
| **Preset** | `--preset long_form` (default) | Suits longer-form educational pacing. |
| **Music mood** | `--music-mood calm` (default) | Non-distracting bed. |
| **Data viz provider** | default `matplotlib_data_viz` | Renders bar/line/pie charts from inline syntax. |
| **Screen recordings** | not generated | Record separately and use `podium_cut`/`voice_canvas`. |
| **Quiz/checkpoint slides** | add as explicit scenes in concept.md | The engine does not auto-generate quizzes. |

### Data-viz syntax

Inside `concept.md`, embed chart requests like:

```markdown
asset_query: data:chart_type=bar;labels=Q1,Q2,Q3;values=120,150,180
```

The scene planner turns this into a `DATA_VIZ` asset; `matplotlib_data_viz`
renders the chart as a PNG, and Remotion treats it as a static image with
Ken Burns.

### Example commands

```bash
# Tutorial from a step-by-step guide
ve run ./tutorial.md --job-id course \
    --work-root ./projects --output-root ./outputs \
    --template training_module --music-mood calm

# Force data-viz scenes even without explicit queries by describing charts in the script
ve assets course --image-provider poyo_ai --data-viz-provider matplotlib_data_viz
ve render course
```

### Edge cases / guardrails

- **Screen capture not supported:** the engine cannot record your screen. For
  click-through tutorials, capture externally and use `voice_canvas` to lay
  the screen recording under a voiceover.
- **Malformed data-viz syntax:** the provider may fail or produce an empty
  chart. Follow `chart_type=...;labels=...;values=...` exactly.
- **Too many steps:** very long guides (> 30 steps) may hit render duration or
  API cost limits. Consider splitting into multiple training modules.

---

## 8. `brand_promo`

**Script + brand file → brand-locked promo video.**

### What it can do

- Read a markdown promo script.
- Generate narration via TTS.
- Generate visuals.
- Apply a brand file that locks colors, fonts, logo overlay, and lower-thirds.
- Add professional background music.
- Render in 16:9 for brand-safe distribution.
- Best for: company culture videos, event promos, partner announcements,
  branded content series, annual reports.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md`, `*.txt` | Promo script. |
| brand file | strongly recommended | `presets/brand.json` or `presets/brands/<job>.json` | Brand colors, fonts, logo path. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--aspect-ratio 16:9` (default) or `9:16` | 16:9 for corporate; 9:16 for social promos. |
| **Preset** | `--preset long_form` (default) | Matches brand templates. |
| **Music mood** | `--music-mood professional` (default) | Clean, polished beds. |
| **Brand override** | `--presets-dir ./my_brand` or `config.yaml` | Per-job brand files in `presets/brands/<job>.json`. |
| **Logo overlay** | `brand.json` → `layers[].type = "logo"` | PNG logo positioned by config. |
| **Caption style** | derived from brand via `CaptionStyle.from_brand()` | Font, color, stroke, bottom/safe alignment. |

### Example `brand.json`

```json
{
  "colors": {"primary": "#0A2540", "secondary": "#00D4AA"},
  "fonts": {"caption": "Montserrat-Bold"},
  "logo": {"path": "./assets/logo_white.png", "position": "bottom-right"}
}
```

### Example commands

```bash
# Promo using the global brand
ve run ./promo.md --job-id brand-film \
    --work-root ./projects --output-root ./outputs \
    --template brand_promo \
    --presets-dir ./presets --music-mood professional

# Promo using a per-job brand file at ./presets/brands/brand-film.json
ve run ./promo.md --job-id brand-film \
    --work-root ./projects --output-root ./outputs \
    --template brand_promo
```

### Edge cases / guardrails

- **Missing brand file:** the engine falls back to default presets. The output
  will not match your brand identity.
- **Logo path must exist:** a missing logo file raises a clear error during
  the graphics stage.
- **Font not installed:** FFmpeg `drawtext` needs the font available to the
  system or referenced as a file path. Use `fontfile` paths in brand config to
  avoid fallback fonts.

---

## 9. `multi_language_dub`

**Existing video + translated script → re-voiced video in a new language.**

### What it can do

- Take an existing video file.
- Accept a translated markdown script matching the original segments.
- Generate new voiceover audio via TTS in the target language.
- Replace the original audio track with the new TTS audio.
- Burn captions in the target language.
- Render in 16:9 (default).
- Best for: translating single-speaker content, repurposing webinars for new
  markets, multi-language social clips.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | video file | Original video to dub. |
| translated script | yes | `*.md`, `*.txt` | Target-language script aligned to segments. |
| `--transcript` | no | JSON | Forced-alignment timings in target language. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Aspect ratio** | `--expected-aspect-ratio 16:9` (default) | Preserve original framing. |
| **TTS language** | set via `config.yaml` provider options | ElevenLabs voice_id + language/model selection. |
| **Preserve music** | not supported | The original audio track is fully replaced; re-add music externally. |
| **Forced alignment** | `ve align` with target text | Improves caption/sync accuracy. |
| **Segment count** | match original script segments | More segments = finer timing control. |

### Example commands

```bash
# Dub a webinar into Hindi
ve run ./webinar_en.mp4 --job-id webinar-hi \
    --work-root ./projects --output-root ./outputs \
    --template multi_language_dub \
    --transcript ./webinar_hi_alignment.json
```

### Edge cases / guardrails

- **Not lip-sync:** mouth movements are not altered. The output is a re-voice,
  not a true dub with facial animation.
- **Script timing mismatch:** if the translated script is much longer or
  shorter, audio and visuals may desync. Keep segment durations similar.
- **Original music lost:** because the entire audio track is replaced, BGM and
  SFX from the source disappear. Use `ve music-generate` to add a new bed.

---

## 10. `raw_batch`

**Directory of clips → v1 raw-footage pipeline on the first clip.**

### What it can do

- Discover all video clips in a directory.
- Create proxies and extract audio for each.
- Transcribe, analyze, plan, and cut **only the first clip**.
- Add captions, optional b-roll, preview, QA, and export.
- Best for: legacy single-clip workflows, quick experiments, and batch-folder
  discovery where only the primary clip matters.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | directory path | Folder containing `mp4`/`mov`/etc. clips. |
| `--transcript` | no | JSON | Optional transcript for the first clip. |
| `--broll-provider` | no | `placeholder`, `fal_ai`, `pexels` | B-roll generation backend for the v1 pipeline. |
| `--broll-provider` | no | `placeholder` or `fal_ai` | B-roll generation backend for the v1 pipeline. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Preset** | `--preset long_form` or `short_explainer` | Affects edit plan style. |
| **B-roll provider** | `--broll-provider placeholder` (safe default), `fal_ai`, or `pexels` | `fal_ai` requires `FAL_KEY`; `pexels` requires `PEXELS_API_KEY`. |
| **Force re-run** | `--force` | Supported in v1; re-runs stages even if cached. |
| **First-clip only** | automatic | Clips 2..N are discovered and ignored. |
| **Music** | via config or `ve music` after export | v1 pipeline does not auto-mix music. |

### Example commands

```bash
# Run the legacy pipeline on a folder of raw clips
ve run ./raw_clips/ --job-id batch \
    --work-root ./projects --output-root ./outputs \
    --broll-provider placeholder --force

# With a transcript for the first clip
ve run ./raw_clips/ --job-id batch \
    --work-root ./projects --output-root ./outputs \
    --transcript ./clip1.json
```

### Edge cases / guardrails

- **Multiple clips ignored:** this is the most important limitation. Only the
  first discovered clip is edited. If you need all clips, concatenate them with
  FFmpeg first and then use `podium_cut`.
- **No music stage:** v1 does not include `MusicSkill`; add music manually after
  export.
- **B-roll cost:** setting `--broll-provider fal_ai` makes external API calls.
  Use `placeholder` for free test runs.

---

## 11. `musical_video`

**Music track + user images/short clips → lyric-synced visual video.**

### What it can do

- Use a user-supplied music/audio file as the spine of the video.
- Discover images and short videos in a `--media-dir` and cycle them in sorted
  directory order, one segment per `--segment-seconds`.
- Animate still images with a gentle Ken Burns pan/zoom.
- Trim video clips to the segment length and freeze on the last frame if shorter.
- Render the full composition through Remotion with the music as a global audio
  track.
- Optionally align lyrics to the music via ElevenLabs forced alignment and burn
  word-level captions with FFmpeg drawtext.
- Best for: lyric videos, montages, reel cut-downs, visualizers without complex
  motion graphics.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.mp3`, `*.wav`, `*.m4a`, `*.flac`, `*.aac`, `*.ogg` | The music/audio spine. |
| `--media-dir` | yes | directory path | Folder containing `jpg`/`png`/`webp`/`gif`/`mp4`/`mov`/`mkv`/`webm`/etc. |
| `--lyrics` | no | plain text file | Lyrics for forced-alignment captions. |
| `--language` | no | language code, default `en` | Used in the output `transcript_<lang>.json` filename. |
| `--segment-seconds` | no | float, default `5.0` | Target visual segment length (clamped 1–30s). |
| `--expected-aspect-ratio` | no | `16:9` (default), `9:16`, `1:1`, `4:3` | Canvas aspect ratio for the Remotion render. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Segment length** | `--segment-seconds 3.5` | Last segment is shortened to match the music duration. |
| **Lyrics language** | `--language es` | One language per run; rerun with a different `--language` for each dub. |
| **No lyrics** | omit `--lyrics` | Skips forced alignment and emits a caption-less music video. |
| **Provider override** | `config.yaml` | Swap `local_media` or `elevenlabs_forced_alignment` providers for tests. |
| **Direct FFmpeg fallback** | `python3 scripts/compose_musical_video.py` | Use when the Remotion render drops audio or when you need deterministic transitions/clip speed. |

### Example commands

```bash
# Basic music montage
ve run ./song.mp3 --job-id montage \
    --work-root ./projects --output-root ./outputs \
    --media-dir ./visuals \
    --segment-seconds 5.0

# Lyric video with forced-alignment captions
ve run ./song.mp3 --job-id lyric-video \
    --work-root ./projects --output-root ./outputs \
    --media-dir ./visuals \
    --lyrics ./lyrics.txt \
    --language en \
    --segment-seconds 4.0

# Deterministic direct-FFmpeg fallback (no Remotion/Node required)
python3 scripts/compose_musical_video.py \
    --music ./song.mp3 \
    --media-dir ./visuals \
    --output ./final.mp4 \
    --segment-seconds 8.0 \
    --transition-duration 1.5
```

### Edge cases / guardrails

- **No supported media in `--media-dir`:** the planner raises a clear error
  listing allowed extensions.
- **Music duration shorter than one segment:** a single scene is emitted with
  the full music duration.
- **Shorter videos:** clips shorter than a segment freeze on their final frame.
- **Beat detection:** not implemented; cuts are evenly spaced by time, not
  by tempo. Use `--segment-seconds` to approximate a beat interval.
- **Multi-language:** run once per language. Each run writes its own
  `transcript_<lang>.json` and replaces `transcript.json` with the latest.
- **Silent output from Remotion:** the built-in `musical_video` pipeline can
  emit a silent AAC track in headless mode. The `scripts/compose_musical_video.py`
  fallback muxes only the supplied music file as audio and is verified to keep
  sound in the final MP4.

---

## Stock footage with Pexels

The engine can use **Pexels** (`pexels`) as an alternative image/video provider
for any template that normally generates visuals. Pexels is a curated stock
photo/video API; the scene's `visual_description` becomes the search query.

### When to use it

- **Documentary / explainer b-roll:** real-world footage under a voiceover
  (`voice_canvas`, `explainer_reel`).
- **Factual / historical topics:** stock photos of landmarks, people, or events
  where generative models may hallucinate details.
- **Cheap prototyping:** Pexels search is free, so it is useful for drafts and
  layout tests before spending credits on generative providers.

### CLI usage

```bash
# Use Pexels for still images
ve assets my-job --image-provider pexels

# Use Pexels for motion scenes (requires props.requires_motion=true)
ve assets my-job --video-provider pexels

# Use Pexels as b-roll in the raw-footage pipeline
ve run ./talk.mp4 --job-id webinar \
    --work-root ./projects --output-root ./outputs \
    --broll-provider pexels
```

### Configuration

Pexels reads `PEXELS_API_KEY` from the repo-root `.env` or environment. You can
override search defaults in `config.yaml`:

```yaml
assets:
  providers:
    pexels:
      api_key_env: PEXELS_API_KEY
      timeout: 60.0
      provider_options:
        per_page: 5
        orientation: landscape   # landscape, portrait, square
```

Per-request overrides are also supported through `params.model_kwargs` or scene
`props`, e.g. `props.orientation = "portrait"`.

### Caveats

- **Search, not generation:** you get the first matching stock result, not a
  custom-rendered image. Niche queries may return nothing.
- **Attribution:** Pexels content is free to use, but the provider records the
  photographer/videographer name and URL in `metadata.attribution`. It does not
  burn attribution onto the rendered video automatically.
- **No TTS/music/SFX:** Pexels only provides `image` and `video` assets.

---

### Storyteller cost summary

The three storyteller presets (`storyteller_budget`, `storyteller_hd`,
`storyteller_premium`) cover the diagonal of a 3-tier image × 3-tier video cost
matrix. Listed per-asset (image, motion, music, TTS, SFX):

| Asset | Budget | HD (default) | Premium |
| --- | --- | --- | --- |
| Image (per scene) | `flux-schnell`, ~$0.002 | `gpt-image-2` 1K low, ~$0.014 | `gpt-image-2` 2K, ~$0.05 |
| Motion (per scene, ~4 s) | `wan2.2-image-to-video-fast` 480p, **$0.030** | `seedance-2-fast` 720p, ~$0.28 | `seedance-2` 1080p, ~$1.24 |
| Music (one track) | `minimax-music-2.6` $0.10 | `minimax-music-2.6` $0.10 | `minimax-music-2.6` $0.10 |
| TTS (per 10K chars, English) | ElevenLabs ~$0.30 | ElevenLabs ~$0.30 | ElevenLabs ~$0.30 |
| TTS (per 10K chars, Hindi) | Sarvam Bulbul v3 ₹30 (~$0.36) | Sarvam Bulbul v3 ₹30 (~$0.36) | Sarvam Bulbul v3 ₹30 (~$0.36) |
| SFX (per call) | ElevenLabs ~$0.20 | ElevenLabs ~$0.20 | ElevenLabs ~$0.20 |

For a 10-min story (15 scenes: 9 image + 6 motion, 15K chars narration):
- Budget: 9×$0.002 + 6×$0.030 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$0.86**
- HD (default): 9×$0.014 + 6×$0.28 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$2.40**
- Premium: 9×$0.05 + 6×$1.24 + $0.10 music + $0.36 TTS + ~$0.20 SFX ≈ **$8.61** (~10x budget)

A user can mix-and-match tiers per-run:
`ve run story.md --template storyteller_hd --video-tier budget --image-tier premium` → premium images, budget motion (~$1.00 total).

---

## 12. `storyteller_budget`

**Lowest-cost storyteller preset — multi-voice, multi-language story videos on
a budget.**

### What it can do

- Parse a story markdown script with YAML front-matter (`cast`, `language`,
  `music_mood`) and `## Speaker:` sections into a scene plan.
- Route TTS per speaker: ElevenLabs v4 for every language by default
  (2026-10-30; native Telugu/Tamil narrators when no voice is named). A
  `sarvam:shubh` or `kokoro:` prefix picks that provider instead.
- Generate still images with the budget image model (`flux-schnell`).
- Generate motion clips for ~50% of scenes with the budget video model
  (`wan2.2-image-to-video-fast`, 480p) — gentle parallax, not action shots.
- Mix a PoYo music bed (`minimax-music-2.6` / `generate-music`, ~$0.10/gen)
  with speech-aware ducking.
- Export to a FableCut timeline for browser preview/trim, then render the
  final cut with `ve render-fablecut`.
- Best for: prototyping multilingual stories, internal drafts, bulk
  generation where cost matters more than fidelity.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md` | Story script with YAML front-matter + `## Speaker:` sections. |
| `--config` | recommended | `config.yaml` | LLM, provider, and tier overrides. |
| `--image-tier` | no | `budget` (default), `standard`, `premium` | Override image tier. |
| `--video-tier` | no | `budget` (default), `standard`, `premium` | Override video tier. |
| `--aspect-ratio` | no | `16:9` (default), `9:16`, `1:1`, `4:3` | Canvas ratio. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Per-scene motion** | `> motion: true` in the scene header | The LLM auto-selects ~50% of scenes; user directive wins. |
| **Voice routing** | `voice_id: sarvam:shubh` or `elevenlabs:<id>` per speaker | Unprefixed voices use ElevenLabs v4 (default voice-over); a `sarvam:`/`kokoro:` prefix picks that provider. |
| **Tier override** | `--image-tier standard --video-tier premium` | Mix tiers without changing template. |
| **Music mood** | `--music-mood cinematic` (default) | Passed to the PoYo music prompt. |
| **Timeline preview** | `ve storyteller-export <job>` then `ve render-fablecut <job>` | Round-trip via FableCut. |

### Example commands

```bash
# Budget storyteller run
ve run ./story.md --job-id story-budget \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_budget

# Mix tiers: keep budget video but upgrade images
ve run ./story.md --job-id story-mixed \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_budget --image-tier standard
```

### Edge cases / guardrails

- **Budget motion fidelity:** `wan2.2-image-to-video-fast` adds gentle parallax to a still
  image; do not expect action shots. Use `--video-tier premium` for hero
  scenes.
- **LLM motion selection:** the LLM's motion-scene choice is deterministic per
  (text, speaker) but not user-tunable. Use `> motion: true|false` to override.
- **Sarvam fallback:** if Bulbul v3 returns `model_unavailable`, the TTS
  router falls back to Bulbul v2 — see `docs/SARVAM_INTEGRATION.md`.

---

## 13. `storyteller_hd`

**Standard-quality storyteller preset — the default multi-voice story template.**

### What it can do

- Same script format and pipeline as `storyteller_budget`, but with the
  standard image model (`gpt-image-2` 1K low) and standard video model
  (`seedance-2-fast` 720p).
- The user marks `> motion: true` per scene to make a scene animate; otherwise
  the scene renders as a still with Ken Burns.
- Full multi-voice multi-language support via per-speaker `voice_id` +
  `language`; ElevenLabs v4 by default, or a `sarvam:`/`kokoro:` prefix
  for that provider.
- Same FableCut timeline preview + `ve render-fablecut` final cut.
- Best for: most multi-voice story videos — the recommended starting point.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md` | Story script with YAML front-matter + `## Speaker:` sections. |
| `--config` | recommended | `config.yaml` | LLM, provider, and tier overrides. |
| `--image-tier` | no | `budget`, `standard` (default), `premium` | Override image tier. |
| `--video-tier` | no | `budget`, `standard` (default), `premium` | Override video tier. |
| `--aspect-ratio` | no | `16:9` (default), `9:16`, `1:1`, `4:3` | Canvas ratio. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Per-scene motion** | `> motion: true` in the scene header | Default is still image + Ken Burns. |
| **Voice routing** | `voice_id: sarvam:shubh` or `elevenlabs:<id>` per speaker | Unprefixed voices use ElevenLabs v4 (default voice-over); a `sarvam:`/`kokoro:` prefix picks that provider. |
| **Tier override** | `--video-tier premium` | Upgrade motion without changing template. |
| **Music mood** | `--music-mood cinematic` (default) | Passed to the PoYo music prompt. |
| **Timeline preview** | `ve storyteller-export <job>` then `ve render-fablecut <job>` | Round-trip via FableCut. |

### Example commands

```bash
# Standard storyteller run
ve run ./story.md --job-id story-hd \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_hd

# Upgrade only the hero motion scene to premium
ve run ./story.md --job-id story-hd-hero \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_hd --video-tier premium
```

### Edge cases / guardrails

- **No motion directive:** without `> motion: true`, every scene is a still
  image with Ken Burns. Motion is opt-in, not automatic.
- **Sarvam fallback:** same as `storyteller_budget` — see
  `docs/SARVAM_INTEGRATION.md`.
- **Cost of full motion:** marking every scene `> motion: true` at 720p
  multiplies video cost. Reserve motion for scenes that need it.

---

## 14. `storyteller_premium`

**Cinematic storyteller preset — hero reels and brand films.**

### What it can do

- Same script format, pipeline, and provider routing as `storyteller_hd`,
  but every still uses `gpt-image-2` 2K and every motion scene uses
  `seedance-2` at 1080p.
- Highest visual fidelity in the storyteller family; ~10× the cost of
  `storyteller_budget` for motion scenes.
- Same FableCut timeline preview + `ve render-fablecut` final cut.
- Best for: hero reels, brand films, final-cut output, story-finale
  sequences where visual fidelity is the primary goal.

### Expected inputs

| Input | Required | Format | Purpose |
|-------|----------|--------|---------|
| `source` | yes | `*.md` | Story script with YAML front-matter + `## Speaker:` sections. |
| `--config` | recommended | `config.yaml` | LLM, provider, and tier overrides. |
| `--image-tier` | no | `budget`, `standard`, `premium` (default) | Override image tier. |
| `--video-tier` | no | `budget`, `standard`, `premium` (default) | Override video tier. |
| `--aspect-ratio` | no | `16:9` (default), `9:16`, `1:1`, `4:3` | Canvas ratio. |

### Accepted variations

| Variation | How to request | Notes |
|-----------|----------------|-------|
| **Per-scene motion** | `> motion: true` in the scene header | Default is still image + Ken Burns. |
| **Voice routing** | `voice_id: sarvam:shubh` or `elevenlabs:<id>` per speaker | Unprefixed voices use ElevenLabs v4 (default voice-over); a `sarvam:`/`kokoro:` prefix picks that provider. |
| **Tier override** | `--image-tier standard` | Drop cost temporarily without changing template. |
| **Music mood** | `--music-mood cinematic` (default) | Passed to the PoYo music prompt. |
| **Timeline preview** | `ve storyteller-export <job>` then `ve render-fablecut <job>` | Round-trip via FableCut. |

### Example commands

```bash
# Cinematic storyteller run
ve run ./story.md --job-id story-premium \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_premium

# Drop cost temporarily: keep cinematic motion but use standard images
ve run ./story.md --job-id story-premium-mixed \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_premium --image-tier standard
```

### Edge cases / guardrails

- **Cost:** every motion scene at 1080p is ~10× the budget tier. Do not use
  for prototyping; use `storyteller_hd` or `storyteller_budget` first.
- **Sarvam fallback:** same as the other storyteller tiers — see
  `docs/SARVAM_INTEGRATION.md`.

---

## Cross-template comparison

| Capability | podium_cut | explainer_reel | image_story | voice_canvas | product_spot | social_hook | training_module | brand_promo | multi_language_dub | raw_batch | musical_video |
|------------|:----------:|:--------------:|:-----------:|:------------:|:--------------:|:-----------:|:-----------------:|:-----------:|:------------------:|:---------:|:-------------:|
| Uses existing video | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Uses markdown script | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (translated) | ❌ | ❌ |
| Generates voiceover | ❌ | ✅ TTS | ✅ TTS | ❌ (supplied) | ✅ TTS | ✅ TTS | ✅ TTS | ✅ TTS | ✅ TTS | ❌ | ❌ |
| Generates images | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | optional b-roll | ❌ |
| Generates motion video | ❌ | ✅ opt-in | ✅ opt-in | ✅ opt-in | ✅ opt-in | ✅ opt-in | ✅ opt-in | ✅ opt-in | ❌ | ❌ | ❌ |
| Ken Burns static motion | ❌ | ✅ | ✅ always | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Captions | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ opt-in |
| Background music | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | replace only | manual | ✅ (supplied) |
| SFX | ✅ opt-in | ✅ opt-in | ❌ | ✅ opt-in | ✅ opt-in | ✅ opt-in | ❌ | ❌ | ❌ | ❌ | ❌ |
| Data viz / charts | ❌ | ✅ via data_viz | ❌ | ✅ via data_viz | ❌ | ❌ | ✅ | ✅ via data_viz | ❌ | ❌ | ❌ |
| Brand overlay | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ strongly | ❌ | ✅ | ❌ |
| Default aspect | 16:9 | 16:9 | 16:9 | 16:9 | 9:16 | 9:16 | 16:9 | 16:9 | 16:9 | 16:9 | 16:9 |
| Pipeline | v2 skills | script_to_video | script_to_video | script_to_video | script_to_video | script_to_video | script_to_video | script_to_video | v2 skills | v1 raw | music_video |

## Selecting the right template

**Flowchart:**

1. Is the source a directory? → `raw_batch`.
2. Is the source an audio file?
   - Do you have a `--media-dir` of images/clips to cut to the music? → `musical_video`.
   - Otherwise → `voice_canvas`.
3. Is the source a video file and you want to keep the speaker/visuals? → `podium_cut`.
4. Is the source a video file and you want to replace the language audio? → `multi_language_dub`.
5. Is the source a markdown script?
   - Product demo / ad? → `product_spot` (9:16) or `explainer_reel` (16:9).
   - Short viral clip? → `social_hook`.
   - Step-by-step guide with charts? → `training_module`.
   - Brand-locked corporate video? → `brand_promo`.
   - Photo/image montage? → `image_story`.
   - General topic explainer? → `explainer_reel`.

## Overrides reference

Every template can be overridden without abandoning it:

| Override | CLI flag | Accepted values |
|----------|----------|-----------------|
| `preset` | `--preset` | any name under `presets/` |
| `aspect_ratio` | template-driven; `--expected-aspect-ratio` for QA | `16:9`, `9:16`, `1:1`, `4:3` |
| `providers` | `ve assets --*-provider` or `config.yaml` | `tts`, `image`, `video`, `music`, `sfx`, `data_viz`, `alignment`, `isolation`, `speech_to_speech` |
| `music_mood` | `--music-mood` | free text passed to the music prompt generator |
| `music_path` | `--music-path` | local audio file |
| `skip_music` | `--skip-music` | boolean |
| `qa` | `--qa` | boolean |
| `verify` | `--verify` | boolean |
| `loudness_target_lufs` | `--loudness-target-lufs` | float |
| `template` | `--template` | one of the 14 names |

## Adding a new variation

If a request does not fit any template exactly, prefer to:

1. Pick the closest template.
2. Use overrides for preset, music mood, providers, and QA/verification flags.
3. Edit the generated `scene_plan.json` for scene-level control.
4. If it still cannot work, document the gap in `docs/not_possible.md` and
   schedule a new template only when it removes a recurring unsupported category.

---

*For the concise catalog, see `docs/templates.md`. For unsupported requests,
see `docs/not_possible.md`.*
