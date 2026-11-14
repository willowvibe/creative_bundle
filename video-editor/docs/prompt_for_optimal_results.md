# Prompt Patterns for Optimal Results — Video Editor Engine

**A comprehensive guide to prompt patterns, output types, template selection,
and best practices for the video-editor engine.**

---

## Table of Contents

1. [Quick Reference: Which Template for What?](#quick-reference)
2. [The Three Pipeline Families](#pipeline-families)
3. [Template-by-Template Prompt Guide](#template-guide)
4. [Output Artifacts Produced](#outputs)
5. [Prompt Pattern Cheat Sheets](#cheat-sheets)
6. [Quality Tiers & Cost Control](#tiers)
7. [Common Prompt Mistakes](#mistakes)

---

## <a name="quick-reference"></a>1. Quick Reference: Which Template for What?

| You have... | Use template | Output |
|-------------|-------------|--------|
| A talking-head video to polish | `podium_cut` | Captioned, branded, music-backed MP4 |
| A markdown script/idea | `explainer_reel` | Fully generated explainer video |
| Photos + story to tell | `image_story` | Ken Burns photo montage |
| Pre-recorded voiceover | `voice_canvas` | B-roll video synced to your audio |
| A product to showcase | `product_spot` | 9:16 vertical product reel |
| A short social hook | `social_hook` | 15–60s vertical social clip |
| Step-by-step tutorial | `training_module` | Tutorial with diagrams |
| Brand-locked promo script | `brand_promo` | On-brand promo video |
| Video to re-voice in new language | `multi_language_dub` | Dubbed video with captions |
| Folder of raw clips | `raw_batch` | Auto-cut compilation |
| Music track + images/clips | `musical_video` | Music-driven visual montage |
| Multi-voice story (budget) | `storyteller_budget` | Story video, lowest cost |
| Multi-voice story (standard) | `storyteller_hd` | Story video, balanced quality |
| Multi-voice story (cinematic) | `storyteller_premium` | Story video, highest quality |

---

## <a name="pipeline-families"></a>2. The Three Pipeline Families

The engine has three fundamentally different pipelines. Understanding which
one your template uses is key to writing effective prompts.

### Pipeline A: v2 Skills (`podium_cut`, `multi_language_dub`)

**What it does:** Takes an existing video file and applies post-production:
transcribe → analyze → cut → graphics → captions → music → export.

**Prompt pattern:** You don't write a "prompt" — you provide a **video file**.
The engine auto-edits it. Your control comes from CLI flags, not text prompts.

**Best prompt = best source video:**
- Clear, well-lit speaker facing the camera
- Clean audio with minimal background noise
- Single speaker (multi-speaker not supported)
- 30 seconds to 60 minutes

### Pipeline B: Script-to-Video (`explainer_reel`, `image_story`, `voice_canvas`,
`product_spot`, `social_hook`, `training_module`, `brand_promo`)

**What it does:** Reads a markdown script, plans scenes, generates visuals
and voiceover, composes everything into a video.

**Prompt pattern:** You write a **markdown script** with scene descriptions
and narration. The LLM decomposes it into a scene plan, then the engine
generates assets and renders.

**Best prompt = descriptive, concrete, scene-by-scene:**
- Each `## Scene N` becomes one video segment
- `Narration:` text becomes TTS voiceover
- `Visual:` descriptions become AI-generated images
- Be specific about what should be on screen

### Pipeline C: Storyteller (`storyteller_budget`, `storyteller_hd`,
`storyteller_premium`)

**What it does:** Reads a story markdown with YAML front-matter, generates
multi-voice narration, creates visuals per scene, exports to FableCut for
timeline editing, then renders the final cut.

**Prompt pattern:** You write a **story script** with YAML front-matter
defining the cast, language, and music mood. Each `## Speaker: name` section
becomes a scene with that character's voice.

**Best prompt = narrative with character voices:**
- YAML front-matter defines `cast`, `language`, `music_mood`
- `## Speaker: character_name` sections for each scene
- `> visual:` blockquotes describe what the scene looks like
- `> motion: true` to animate a scene (HD/Premium tiers)

### Pipeline D: Music Video (`musical_video`)

**What it does:** Takes a music track + folder of images/clips, cycles
through them in order, applies Ken Burns to stills, trims videos to fit.

**Prompt pattern:** You provide **files, not text**. The music file drives
the timeline. Images and clips in the media directory are the visuals.

---

## <a name="template-guide"></a>3. Template-by-Template Prompt Guide

### 3.1 `podium_cut` — Talking-Head Polish

**Pipeline:** v2 Skills | **Input:** Video file | **Aspect:** 16:9

**What makes a great input:**
- A single speaker, well-framed, good lighting
- Clean audio (the engine removes silence/filler but can't fix severe noise)
- Content that benefits from captions (educational, informational)

**Optimal CLI pattern:**
```bash
ve run ./talk.mp4 \
  --job-id my-talk \
  --template podium_cut \
  --preset long_form \
  --music-path ./background.mp3 \
  --qa \
  --work-root ./projects --output-root ./outputs
```

**When to add flags:**
- `--transcript ./talk.json` — if you already have word-level timings
- `--qa` — to fail on bad audio levels, black frames, missing captions
- `--skip-music` — for pure speech with no background
- `--expected-aspect-ratio 9:16` — for vertical short-form cuts

**What NOT to do:**
- Don't use for multi-speaker panels or interviews
- Don't use for music videos or visual-heavy content
- Don't use if the source has no speech (captions will fail)

---

### 3.2 `explainer_reel` — Markdown → Full Video

**Pipeline:** Script-to-Video | **Input:** Markdown script | **Aspect:** 16:9

**Optimal prompt structure:**
```markdown
# Your Topic Title

A one-sentence summary of what this video covers.

## Scene 1

Narration: Start with a hook that grabs attention. State the problem clearly.

- Visual: A bold title card with the problem statement
- Visual: An illustration of someone facing this problem

## Scene 2

Narration: Introduce the solution. Explain how it works in simple terms.

- Visual: A diagram or illustration of the solution
- Visual: Before/after comparison

## Scene 3

Narration: Show the key benefit or result. Use a concrete example.

- Visual: A happy user or successful outcome
- Visual: Key stat or metric as a text overlay

## Scene 4

Narration: Wrap up with a clear call to action.

- Visual: Your logo/brand with the CTA text
```

**Key principles for explainer scripts:**
1. **Hook first** — Scene 1 must grab attention
2. **One idea per scene** — Don't cram multiple concepts into one scene
3. **Concrete visuals** — "A person looking confused at a spreadsheet" beats "confusion"
4. **5–8 scenes** is the sweet spot for 60–90 second explainers
5. **Narration text IS the voiceover** — write it to be spoken, not read

**Optimal CLI pattern:**
```bash
ve run ./concept.md \
  --job-id explainer \
  --template explainer_reel \
  --music-mood "upbeat corporate" \
  --config ./config.yaml \
  --work-root ./projects --output-root ./outputs
```

---

### 3.3 `image_story` — Ken Burns Photo Montage

**Pipeline:** Script-to-Video | **Input:** Markdown | **Aspect:** 16:9

**Optimal prompt structure:**
```markdown
# My Photo Story

## Scene 1

- Visual: A wide landscape shot of [specific location] at golden hour
- Narration: It began here, in the summer of...

## Scene 2

- Visual: A close-up portrait of [describe person] with a warm expression
- Narration: She was the one who...

## Scene 3

- Visual: An intimate detail shot — [specific object] on a wooden table
- Narration: The small things mattered most...
```

**Key principles:**
1. **Visual-first** — describe the image BEFORE the narration
2. **Cinematic language** — "golden hour", "shallow depth of field", "wide shot"
3. **Emotional arc** — images should tell a story even without narration
4. **8–15 scenes** for a 60–90 second montage
5. **Use `--music-mood cinematic`** for emotional weight

---

### 3.4 `voice_canvas` — Voiceover + Generated B-Roll

**Pipeline:** Script-to-Video | **Input:** Audio file + Markdown | **Aspect:** 16:9

**Optimal prompt structure:**
```markdown
# My Documentary Segment

## Scene 1

Narration: [Must match your voiceover audio exactly]

- Visual: Establishing shot of [location]
- Visual: [Subject] in their natural environment

## Scene 2

Narration: [Must match your voiceover audio exactly]

- Visual: Close-up of [detail]
- Visual: [Action] happening in real time
```

**Key principles:**
1. **Script MUST match the audio** — the engine doesn't verify this
2. **B-roll style visuals** — show what the narrator is talking about, not the narrator
3. **Use `ve align` first** for accurate caption timings
4. **Ambient music** works best (doesn't compete with speech)

---

### 3.5 `product_spot` — Vertical Product Showcase

**Pipeline:** Script-to-Video | **Input:** Markdown | **Aspect:** 9:16

**Optimal prompt structure:**
```markdown
# Product Spot

## Product
Name: [Product Name]
Benefit: [One-line key benefit]
Audience: [Target user]

## Script

1. Hook: [Attention-grabbing opener — problem or desire]
2. Problem: [The pain point your product solves]
3. Solution: [How your product fixes it, with key feature]
4. CTA: [Clear call to action — where to get it]
```

**Key principles:**
1. **Hook in first 3 seconds** — vertical scrollers decide instantly
2. **One product, one benefit** — don't list features
3. **Bold captions** — many viewers watch without sound
4. **15–30 seconds** is optimal for social
5. **Use `--music-mood upbeat`** for energy

---

### 3.6 `social_hook` — Fast Vertical Social Clip

**Pipeline:** Script-to-Video | **Input:** Markdown | **Aspect:** 9:16

**Optimal prompt structure:**
```markdown
# Social Hook

## Hook
[One punchy sentence — the thing that makes people stop scrolling]

## Body
[2–3 sentences expanding the hook with value or intrigue]

## CTA
[One clear action — follow, comment, click link]
```

**Key principles:**
1. **15–60 seconds total** — shorter is better
2. **Punchy sentences** — captions pop one word at a time
3. **High contrast visuals** — bold colors, clear subjects
4. **Energetic music** — matches the fast pace
5. **One message per clip** — don't try to say everything

---

### 3.7 `training_module` — Tutorial with Diagrams

**Pipeline:** Script-to-Video | **Input:** Markdown | **Aspect:** 16:9

**Optimal prompt structure:**
```markdown
# Training Module

## Step 1: [Topic]

- Narration: Explain what this step accomplishes.
- Visual: A clean diagram or illustration of the concept.
- Data: asset_query: data:chart_type=bar;labels=Q1,Q2,Q3,Q4;values=10,25,15,30

## Step 2: [Topic]

- Narration: Walk through the process.
- Visual: A screenshot-style illustration of the interface or workflow.

## Step 3: [Topic]

- Narration: Show the result and explain why it matters.
- Visual: Before/after comparison or success metric.
```

**Key principles:**
1. **Numbered, sequential steps** — each `## Step N` is one scene
2. **Data viz syntax:** `asset_query: data:chart_type=bar;labels=A,B;values=1,2`
3. **Calm music** — doesn't distract from learning
4. **Screen recordings must be captured separately** — the engine doesn't record screens

---

### 3.8 `brand_promo` — Brand-Locked Promo

**Pipeline:** Script-to-Video | **Input:** Markdown + brand.json | **Aspect:** 16:9

Same structure as `explainer_reel`, but requires a `presets/brand.json` file
with logo, colors, and fonts. The engine enforces brand compliance on every
visual element.

**Key addition:** Create `presets/brand.json`:
```json
{
  "name": "My Brand",
  "colors": { "primary": "#1a73e8", "secondary": "#ea4335" },
  "fonts": { "heading": "Montserrat", "body": "Inter" },
  "logo_path": "presets/brands/logo.png"
}
```

---

### 3.9 `multi_language_dub` — Re-voice Existing Video

**Pipeline:** v2 Skills | **Input:** Video + Translated Script | **Aspect:** 16:9

**Optimal approach:**
1. Transcribe the original video first
2. Translate the transcript
3. Run `ve run ./video.mp4 --template multi_language_dub --language hi`
4. The engine replaces the audio track with TTS in the target language

**Key principles:**
- Supply a translated script that matches the original timing
- Use `ve align` for accurate caption timings in the target language
- This is voice replacement, not lip-sync — mouths won't match

---

### 3.10 `raw_batch` — Folder of Raw Clips

**Pipeline:** v1 Raw | **Input:** Directory of clips | **Aspect:** 16:9

**Optimal CLI pattern:**
```bash
ve run ./raw_clips/ \
  --job-id compilation \
  --template raw_batch \
  --work-root ./projects --output-root ./outputs
```

**Key principles:**
- Only the first clip is transcribed/analyzed
- Concatenate clips first if you need them all processed
- Best for: quick compilations, event highlights, B-roll reels

---

### 3.11 `musical_video` — Music-Driven Visuals

**Pipeline:** Music Video | **Input:** Audio file + media directory | **Aspect:** 16:9

**Optimal CLI pattern:**
```bash
ve run ./song.mp3 \
  --job-id music-vid \
  --template musical_video \
  --media-dir ./visuals/ \
  --segment-seconds 5 \
  --lyrics ./lyrics.txt \
  --language en \
  --work-root ./projects --output-root ./outputs
```

**Key principles:**
1. **Music file is the spine** — everything is timed to it
2. **Media directory** should contain JPG/PNG/MP4/MOV files
3. **Files cycle in directory order** — name them `01_`, `02_`, etc. for control
4. **Stills get Ken Burns** — no need to pre-animate
5. **Videos are trimmed** to segment length, not stretched
6. **`--segment-seconds`** controls cut timing (no beat detection yet)
7. **Lyrics** are optional — if provided, they're burned as captions

---

### 3.12–3.14 `storyteller_*` — Multi-Voice Story Videos

**Pipeline:** Storyteller | **Input:** Story markdown | **Aspect:** 16:9

**Optimal prompt structure:**
```markdown
---
title: The Lost Kingdom
language: en
music_mood: cinematic
cast:
  narrator:
    voice_id: elevenlabs:Bella
    language: en
  hero:
    voice_id: elevenlabs:Adam
    language: en
  villain:
    voice_id: sarvam:RJ-sports-news
    language: hi
---

# The Lost Kingdom

## Speaker: narrator

In the ancient kingdom of Valdoria, a secret lay buried beneath the
crystal mountains...

> visual: sweeping aerial view of misty mountain peaks at dawn
> motion: true

## Speaker: hero

I've searched for this my entire life. The map was real.

> visual: close-up of weathered hands unfolding an ancient parchment
> motion: false

## Speaker: villain

You think you can keep it from me? I've come too far.

> visual: shadowy figure silhouetted against torchlight in a stone chamber
> motion: true
```

**Key principles for storyteller scripts:**

1. **YAML front-matter is required** — defines the cast, language, and mood
2. **`cast` defines voices** — each character gets a `voice_id` and `language`
3. **Voice ID format:** `elevenlabs:VoiceName` or `sarvam:VoiceName`
4. **`## Speaker: name`** — each section becomes a scene with that voice
5. **`> visual:` blockquotes** — describe what the camera sees
6. **`> motion: true/false`** — control per-scene animation (HD/Premium)
7. **`music_mood`** — `cinematic`, `mysterious`, `uplifting`, `melancholic`, `epic`

**Tier selection guide:**

| Tier | Image Model | Video Model | Cost/Scene | Best For |
|------|------------|-------------|-----------|----------|
| `storyteller_budget` | flux-schnell | wan2.2-image-to-video-fast | ~$0.002; +$0.03 motion (480p) | Prototyping, bulk drafts |
| `storyteller_hd` | gpt-image-2 1K | seedance-2-fast 720p | ~$0.010; +$0.56 motion (720p ~4s) | Standard production |
| `storyteller_premium` | gpt-image-2 2K | seedance-2 1080p | ~$0.045; +$1.80 motion (1080p ~4s) | Hero reels, final cut |

**Override tiers per-run:**
```bash
ve run ./story.md --template storyteller_budget \
  --image-tier premium --video-tier standard
```

---

## <a name="outputs"></a>4. Output Artifacts Produced

Every pipeline produces a standard set of outputs in the job directory:

```
projects/<job-id>/
├── rough_cut/
│   └── locked.mp4              # Auto-edited rough cut
├── graphics/
│   └── with_graphics.mp4       # After brand/preset applied
├── captions/
│   └── with_captions.mp4       # After captions burned in
├── music/
│   └── with_music.mp4           # After background music mixed
├── plan/
│   ├── scene_plan.json          # The LLM-generated scene plan
│   └── scene_plan_with_assets.json  # After asset generation
├── transcript/
│   └── transcript.json          # Word-level timings
├── verify/
│   ├── requirements.json        # QA requirements spec
│   └── report.json              # QA check results
├── review/
│   └── review_report.json       # Post-render quality review
└── project.md                   # Project brief (if provided)

outputs/<job-id>/
└── output/
    └── final.mp4                # THE FINAL DELIVERABLE
```

**Additional storyteller outputs:**
```
projects/<job-id>/
└── fablecut/
    └── project.json             # FableCut timeline (editable in browser)
```

**Additional ledger outputs (if enabled):**
```
outputs/
└── projects.db                  # SQLite ledger with cost tracking
```

---

## <a name="cheat-sheets"></a>5. Prompt Pattern Cheat Sheets

### Pattern 1: The "Explainer" Script (for `explainer_reel`, `brand_promo`)

```
HOOK → PROBLEM → SOLUTION → BENEFIT → CTA
```

Each step = one scene. 5 scenes, 60–90 seconds.

### Pattern 2: The "Storyteller" Script (for `storyteller_*`)

```
SETTING → CONFLICT → JOURNEY → CLIMAX → RESOLUTION
```

Each step = 1–3 scenes with different speakers. 8–15 scenes, 2–5 minutes.

### Pattern 3: The "Social Hook" Script (for `social_hook`)

```
PATTERN INTERRUPT → VALUE PROMISE → PROOF/EXAMPLE → CTA
```

4 scenes, 15–30 seconds. Punchy. Bold captions.

### Pattern 4: The "Product Spot" Script (for `product_spot`)

```
HOOK → PROBLEM → SOLUTION → FEATURE → CTA
```

5 scenes, 15–30 seconds. Vertical. Show the product.

### Pattern 5: The "Tutorial" Script (for `training_module`)

```
CONTEXT → STEP 1 → STEP 2 → ... → STEP N → RECAP
```

N+2 scenes. Each step has narration + visual + optional data viz.

### Pattern 6: The "Image Story" Script (for `image_story`)

```
OPENING IMAGE → STORY BEAT 1 → STORY BEAT 2 → ... → CLOSING IMAGE
```

8–15 scenes. Visual-first. Cinematic music. Emotional arc.

---

## <a name="tiers"></a>6. Quality Tiers & Cost Control

### Image Generation Tiers

| Tier | Model | Resolution | Cost | Best For |
|------|-------|-----------|------|----------|
| `budget` | flux-schnell | 512–768px | ~$0.0024 (per MP) | Prototyping, drafts |
| `standard` | gpt-image-2 1K low | 1024px | ~$0.010 | Production |
| `premium` | gpt-image-2 2K | 2048px | ~$0.045 | Hero/print-quality |

### Video Generation Tiers

| Tier | Model | Resolution | Cost | Best For |
|------|-------|-----------|------|----------|
| `budget` | wan2.2-image-to-video-fast | 480p | ~$0.03/gen | Gentle parallax, drafts |
| `standard` | seedance-2-fast | 720p | ~$0.14/sec | Production motion |
| `premium` | seedance-2 | 1080p | ~$0.45/sec | Cinematic motion |

### TTS Provider Costs

| Provider | Cost/1K chars | Best For |
|----------|--------------|----------|
| `kokoro_tts` | Free (local) | English, CPU-friendly |
| `kitten_tts` | Free (local) | Ultra-lightweight |
| `moss_tts` | Free (local) | 20 languages, voice clone |
| `sarvam_tts` | ~$0.023 (v4 Flash) | Indian languages |
| `elevenlabs_tts` | ~$0.04 (Flash) / ~$0.08 (v3) | Highest quality |
| `openai_tts` | ~$0.015 | Good quality, fast |

### Music Provider Costs

| Provider | Cost/Track | Best For |
|----------|-----------|----------|
| `ace_step` | Free (local/API) | Quick generation, 50+ languages |
| `poyo_music` | ~$0.10/generation | **Default.** Good quality, fast |
| `elevenlabs_music` | ~$0.15/**minute** | ⚠️ **Avoid — do not use for music** (Bug #229): many times the PoYo cost |

### OpenRouter Gateway (opt-in)

OpenRouter also fronts **image, video, and music** generation (asset provider
`openrouter`; PoYo remains the default). Live per-unit pricing:

| Modality | Example model | Cost |
|----------|---------------|------|
| Image | `bytedance-seed/seedream-5-0-pro` | ~$0.045/image |
| Image | `openai/gpt-image-2` | ~$0.13/image |
| Video | `kwaivgi/kling-v3.0-std` | ~$0.084/sec |
| Video | `google/veo-3.1-fast` | ~$0.10–0.12/sec |
| Music | `google/lyria-3-pro-preview` | preview (pricing unpublished) |

When a provider is `auto` and a model is named, the **cheapest** configured
provider serving that model wins (`models.yaml:model_equivalents`). See
`docs/OPENROUTER_GENERATION.md` and `docs/MODEL_ROUTING.md`.

### Classifier (JEV / TypeSafe)

Per-item judgments (scoring, judging, detecting, classifying) run on the
TypeSafe/JEV classifier in one batched call across 11 engine stages — far cheaper
than a per-item LLM round trip and optional (no `TYPESAFE_API_KEY` → unchanged
behavior). See `docs/JEV_CLASSIFIER.md`.

### Using `free_mode` for Zero-Cost Operation

```bash
ve run ./script.md --config config.free.yaml --template explainer_reel
```

The `config.free.yaml` profile routes everything through free providers:
Kokoro TTS, Pexels stock images, ACE-Step music, stub SFX.

---

## <a name="mistakes"></a>7. Common Prompt Mistakes

### ❌ Mistake 1: Abstract visual descriptions
```
- Visual: Innovation and synergy
```
**✅ Fix:** Be concrete and visual.
```
- Visual: Two engineers looking at a holographic blueprint on a glass table
```

### ❌ Mistake 2: Too many ideas per scene
```
## Scene 1
Narration: Our product helps you save time, reduce costs, improve quality,
and delight customers. It's used by Fortune 500 companies and startups alike.
```
**✅ Fix:** One idea per scene. Split into multiple scenes.

### ❌ Mistake 3: Narration that reads like documentation
```
Narration: The system utilizes a microservices architecture with event-driven
communication patterns to ensure horizontal scalability.
```
**✅ Fix:** Write for the ear, not the eye.
```
Narration: Think of it like a restaurant kitchen. Each chef handles one task,
and they all talk to each other. That's how our system stays fast.
```

### ❌ Mistake 4: Missing YAML front-matter in storyteller scripts
```
# My Story
## Speaker: narrator
Once upon a time...
```
**✅ Fix:** Always include the YAML front-matter block.
```markdown
---
title: My Story
language: en
music_mood: cinematic
cast:
  narrator:
    voice_id: elevenlabs:Bella
    language: en
---
# My Story
## Speaker: narrator
Once upon a time...
```

### ❌ Mistake 5: Using `podium_cut` for multi-speaker content
```
ve run ./panel_discussion.mp4 --template podium_cut
```
**✅ Fix:** `podium_cut` only handles single speakers. For multi-speaker,
use `raw_batch` or split the video first.

### ❌ Mistake 6: Expecting the engine to read your mind on visuals
```
- Visual: Something cool
```
**✅ Fix:** The AI generates exactly what you describe. Be specific about
composition, lighting, subject, mood, and style.

### ❌ Mistake 7: Not using `--qa` for production renders
```
ve run ./script.md --job-id final
```
**✅ Fix:** Always add `--qa` for deliverables. It catches audio level issues,
black frames, missing captions, and aspect ratio mismatches.
```
ve run ./script.md --job-id final --qa
```

### ❌ Mistake 8: Using premium tier for prototyping
```
ve run ./draft.md --template storyteller_premium
```
**✅ Fix:** Start with `storyteller_budget` for drafts. Upgrade to HD or
Premium only for the final render.

---

## Appendix: Complete CLI Quick Reference

```bash
# List all templates
ve list-templates

# Check your config (API keys, provider availability)
ve check-config

# Generate a starter concept file for any template
ve plan-script --template explainer_reel --output ./concept.md

# Run a full pipeline
ve run <source> \
  --job-id <id> \
  --template <name> \
  --preset <long_form|short_explainer> \
  --music-mood <mood> \
  --music-path <file.mp3> \
  --config <config.yaml> \
  --qa \
  --budget <credit_limit> \
  --work-root ./projects \
  --output-root ./outputs

# Run individual skills on an existing job
ve rough-cut <job-id> --source <video>
ve graphics <job-id> --preset long_form
ve captions <job-id>
ve music <job-id> --music-path <file.mp3>
ve export <job-id>

# Generate assets for a planned job
ve assets <job-id> --image-provider poyo_ai --tts-provider elevenlabs_tts

# Generate AI music
ve music-generate <job-id> --prompt "upbeat corporate electronic"

# Check project status and costs
ve project-status <project-id>
ve cost-report

# Prune the asset cache
ve cache-prune --max-age-days 30 --max-size-gb 10

# Run the worker to process queued jobs
ve enqueue run --job-id <id> --payload '{"source":"..."}'
ve worker --max-jobs 5
```

---

## Summary: The Golden Rules

1. **Match template to content** — don't force a talking-head video through `explainer_reel`
2. **Be concrete in visual descriptions** — "golden hour mountain landscape" beats "nice view"
3. **One idea per scene** — split complex topics across multiple scenes
4. **Write narration for the ear** — read it aloud; if it sounds unnatural, rewrite it
5. **Start with budget tier, upgrade for final** — save costs during iteration
6. **Always use `--qa` for deliverables** — catch issues before they reach your audience
7. **Use `ve check-config` first** — verify API keys before starting a render
8. **The `scene_plan.json` is editable** — the LLM plan is a starting point, not the final word
