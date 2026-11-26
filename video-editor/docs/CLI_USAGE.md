# CLI Usage — `ve` Commands

This doc covers the command surface for the `ve` CLI. See
`README.md` for the high-level overview and `workflows/` for
end-to-end recipes.

## Table of contents

- [Running a command](#running-a-command)
- [Templates](#templates)
- [Utility commands](#utility-commands)
  - [Queue inspection](#queue-inspection)
  - [Media probing](#media-probing)
- [Running the worker](#running-the-worker)
- [Enqueueing jobs](#enqueueing-jobs)
  - [Common payload keys](#common-payload-keys)
  - [Per-command payload contract](#per-command-payload-contract)
- [v2 skills pipeline (recommended)](#v2-skills-pipeline-recommended)
  - [Muxing an external voiceover (`ve audio`)](#muxing-an-external-voiceover-ve-audio)
  - [Generative audio extras](#generative-audio-extras-ve-music-generate-ve-sfx-generate-ve-sfx-mix)
  - [Audio isolation and speech-to-speech](#audio-isolation-and-speech-to-speech-ve-audio-isolate-ve-speech-to-speech)
  - [Planning a script (`ve plan-script`)](#planning-a-script-ve-plan-script)
  - [Resolving generative assets](#resolving-generative-assets-tts--images--data-viz--video)
  - [Installing a project brief](#installing-a-project-brief)
  - [Forced alignment](#forced-alignment)
  - [ComfyStudio timeline bridge](#comfystudio-timeline-bridge)
  - [FableCut timeline bridge](#fablecut-timeline-bridge)
  - [QA self-evaluation](#qa-self-evaluation)
  - [Requirements verification gate](#requirements-verification-gate)
- [v1 raw-footage pipeline (legacy)](#v1-raw-footage-pipeline-legacy)

## Running a command

The `ve` CLI exposes one typer subcommand per pipeline stage plus
a handful of utility commands:

```bash
ve --help
ve run ./demo/raw.mp4 --job-id demo --work-root ./projects --output-root ./outputs
```

Every command that produces artifacts takes `--job-id`,
`--work-root`, and `--output-root`. The same three values form
the `JobContext` that flows through the pipeline.

### Default roots and config discovery

`--work-root` and `--output-root` are optional. When they are omitted the CLI,
the worker and `JobContext` itself resolve the same way
(`video_editor_state.paths`):

1. `VE_WORK_ROOT` / `VE_OUTPUT_ROOT` if set,
2. `VIDEO_EDITOR_DATA_DIR`, with `projects/` / `outputs/` beneath it,
3. `/work` / `/output` if those directories exist (the container layout),
4. `./projects` / `./outputs` in the current directory.

`ve plan-script` and the asset/LLM loaders find their project config with a
matching rule: explicit `--config PATH` → `VIDEO_EDITOR_CONFIG` → the nearest
`config.yaml` in the current directory or one of its ancestors → the repo
root. If a `config.local.yaml`
sits next to the chosen file, it is merged over it (recursively) and is
gitignored, so machine-specific experiments stay out of diffs.

## Templates

Every `ve run` request is routed through a **template** that maps
inputs to a supported pipeline, preset, aspect ratio, and default
providers. See `docs/templates.md` for the concise catalog,
`docs/Templates_capabilities.md` for detailed capabilities and examples,
and `docs/not_possible.md` for requests that will not work.

```bash
# Let the router infer the template from the source
ve run ./talk.mp4 --job-id demo --work-root ./projects --output-root ./outputs

# Explicitly select a template
ve run ./script.md --job-id demo --work-root ./projects --output-root ./outputs \
    --template social_hook

# List all supported templates
ve list-templates
```

Source → template inference:

| Source | Inferred template |
| --- | --- |
| directory | `raw_batch` |
| video file (`*.mp4`, `*.mov`, etc.) | `podium_cut` |
| audio file + `--media-dir` | `musical_video` |
| audio file (`*.mp3`, `*.wav`, etc.) | `voice_canvas` |
| markdown/text (`*.md`, `*.txt`) | `explainer_reel` |

Use `--template <name>` when the inference is wrong or the file
type is not in the table above.

### Short from a content pack (`ve short`)

```bash
# Build, price, render and QA one vertical short.
ve short shorts/saju-v1-your-animal/short.yaml

# Skip the approval prompt (the price is still written to the plan).
ve short shorts/<slug>/short.yaml --auto-approve
```

A short is one `shorts/<slug>/short.yaml` (words, on-screen text, image
prompts, voice choice) plus one series design in `designs/<name>.json`
(colours, fonts, safe area). The command runs preflight → its own
per-request cost estimate → stills → per-line TTS → alignment → one
composition → render → mux → loudness → QA, and exits non-zero when any gate
fails. It prices each voice line as its own provider call (Sarvam bills per
call), so the approved total is the number the run spends. The full report is
`projects/<job>/qa.json`; the shipped file is `outputs/<job>/final.mp4`.

`--strict-look` turns the anti-generic checks into a failing gate: one motion
on every layer, one font weight, every text centred on a full-bleed gradient,
uniform scene lengths, or a `layers: auto` pack with no depth. Without the flag
they appear in `qa.json` as an advisory `look_bans` check.

A scene can carry a chart/component over its still (#311):

```yaml
- id: pillars
  still: {reuse: hero}
  graphic:
    component: four_pillars
    over: hero          # blurs the still behind the chart
    data:
      pillars:
        - {label: Year,  glyph: 甲, sub: wood}
        - {label: Month, glyph: 乙, sub: fire}
```

Components render as one static `html` layer: `four_pillars`, `element_bars`,
`decade_ribbon`, `medallion_ring`, `strength_gauge`, `feeding_cycle`,
`sixty_cycle_rings`, `countdown`. The data is passed through verbatim; the
component never computes the chart itself. Preflight rejects an unknown
component or malformed data before any paid stage.

Captions default to burned. `captions: {mode: sidecar}` works for `ve explain`
as well as `ve short`: it skips the burn (`final.mp4` is the uncaptioned mixed
video) and writes `outputs/<job>/final.srt` from the measured word timings
(YouTube upload). The coverage gate is measured against the SRT, and the
`caption_tokens` / `caption_contrast` gates pass with "sidecar: no burned
captions" (#316).

A scene can change visual on a narration line (#314):

```yaml
- id: s12
  lines: [{say: "The pillars first."}, {say: "Then the elements."}]
  shots:
    - {still: {key: pillars, prompt: "..."}, at_line: 0}
    - {still: {key: elements, prompt: "..."}, at_line: 1}
```

`layers: generated` with `generated: {bg, mg, fg}` prices and renders three
plates; MG/FG are chroma-keyed from #00FF00 and panned at 0.15/0.45/0.9
(#317). `html: "<div>…</div>"` is a sandboxed static shot (no scripts, network,
links or event handlers) for bespoke layouts (P4.9).

A scene renders a local video instead of a generated still when it sets
`video:` (exactly one of `still`/`video` is required; video scenes skip image
generation and the file is copied into the composition):

```yaml
- id: screen_recording
  video: clips/panel.mp4
  lines: [{say: "Here is the panel."}]
```

`end_card: {enabled: false}` turns off the closing card (used by long-form
chapters). `music: {path: …, gain_db: -22}` mixes a local bed under the
narration with ducking; `music.swells` adds gain points.

`youtube_end_screen: true` on the pack adds an `end_screen` QA check that fails
when text/svg/html overlaps the right half of the last 20 seconds.

A scene can name its exit transition (#313):

```yaml
- id: pillars
  transition_out: seal_stamp
  transition_seconds: 0.5
```

Supported: `cut`, `crossfade`, `fade`, `ink_bleed`, `mist_dissolve`,
`push_through`, `hanji_turn`, `seal_stamp`. The transition becomes the next scene's entrance
(and the end card's, when set on the last scene); unknown names fail preflight.

The optional director's-brief block (`logline`, `references`, `characters`,
`beat_sheet`, `on_screen_text`, `deliverables`, `direction`) is validated but
advisory: `preflight_short` logs the missing fields and never fails the build.

Reference images (#318, P4.8) keep faces, wardrobe and style consistent; PoYo receives them as `image_urls`
through the model's reference variant (`nano-banana-2-lite-edit`, `gpt-image-2-edit`, same price):

```yaml
image:
  provider: poyo_ai              # or flow_image (free, at most 3 references)
  style_reference: refs/style.png   # sent with every still
characters:
  - name: Lakshmi
    reference_images: [refs/lakshmi_front.png, refs/lakshmi_side.png]
scenes:
  - id: corridor
    still: {key: corridor, prompt: "Lakshmi waits in the hospital corridor", characters: [Lakshmi]}
  - id: window
    still: {key: window, prompt: "The same corridor at dawn, Lakshmi at the window", edit_of: corridor}
```

References need `image.provider: poyo_ai` (paid, up to 14) or `flow_image` (Google Flow: free, up to 3 local images, needs a project open in flow-mcp's Chrome; `provider_options.download_quality: upscaled` for 2K). Without references, `auto` runs the chain poyo_ai → openrouter → fal_ai (`agy_image` and `flow_image` are no longer in it; name them with `--image-provider` if you want them, or use Flow stills as `file:` shots, see `STOCK_EXPLAINERS.md`).

Paths are relative to the pack file; URLs pass through. Local images are uploaded once to fal storage
(`FAL_KEY`) and cached by content hash. An `edit_of` still is generated after its source, with the source as
its first reference. Unknown characters, empty sheets, edit loops and non-`poyo_ai` providers fail at load.
Identity is checked at the review board's images gate, not automatically: the usual face-embedding models
(InsightFace) are licensed for non-commercial research only.

A pack can set `sfx: auto` to place the composition's cue sounds (`stamp` →
hit, `pop` → click, `flip` → card flip, `lock` → latch) plus a soft whoosh at
each scene cut, so the shipped `rise`-only packs are audible. Local files in
`projects/<job>/sfx/` win, then the free Freesound (CC0/CC BY) and Openverse
chains; each repeated cue advances one result page so eight cuts do not get
the same whoosh. `credits.txt` lands beside the final for CC BY attribution.
The default `sfx: off` renders silently. With `sfx: auto`, `qa.json` also gains
an `sfx` block: mixed vs voice LUFS (the global ceiling is +2), and per-cue
added level and nearest onset. A soft whoosh may not register an onset, and
the per-cue levels are review columns, so the block never turns a build red.

### 2.5D layers (`layers: auto`)

A scene can ask for a parallax pair instead of one still:

```yaml
scenes:
  - id: hero
    still: {key: hero, prompt: "the subject on a plain background"}
    layers: auto
    lines:
      - say: "The line."
```

The run layerizes `hero` into `hero__bg`, `hero__fg`, `hero__depth` and a
depth-derived `hero__near` mask. The adapter renders the plate as a far layer
(z=0, parallax rate 0.2), a depth-masked near copy (z=1, rate 0.6) and the
cut-out (z=5, rate 1.0); without the mask it falls back to one plate at 0.35.
Reused stills share one layerize build, cached under `projects/<job>/layerize/`
by source hash + model + resolution + cut-out + output version.

A single-still scene honours its `motion:` (`drift`, `stamp`, `pop`, `flip`,
…); the layered foreground always uses the parallax pan. A design can set
`light_leak: {placement: "first_cut"|"last_cut"|"both"}` to add a seek-safe
overlay at the cut.

The GPU work runs in its own environment, never in the engine process. Set the
command once in `config.yaml` (or the git-ignored `config.local.yaml`):

```yaml
layerize:
  command: ["/path/to/venv/bin/python", "/path/to/scripts/spikes/layerize_spike.py"]
  depth_model: depth-anything/DA3MONO-LARGE
  process_res: 1280
  cutout: birefnet        # or ben2 (lighter, softer edges)
```

With an empty `command`, a `layers: auto` pack is refused with a message
instead of silently rendering one still. The 2026-10-28 spike measured 2.34 GB
(DA3 depth), 3.06 GB (BiRefNet bf16) and 0.82 GB (LaMa) on a 4 GB RTX 3050;
run the stages with nothing else on the GPU. `qa.json` gains a `layers` check
that fails when two frames 0.4 s apart are identical (a frozen pan) and a
`flourishes` line with the light-leak count.

### Simple explainer from `script.md` (`explainer_stills`)

```bash
# Build, price, render and QA one explainer.
# Pass --music-path until poyo_music is fixed (see the provider note below).
ve explain videos/2026-10-23/wiz/script.md --job-id wiz-explainer \
    --music-path path/to/bed.m4a

# Draft the script from a topic first (one LLM call, before the gate)
ve explain --from-topic "why Google bought Wiz" --job-id wiz-explainer \
    --music-path path/to/bed.m4a

# Render every still separately instead of reusing reprises
ve explain script.md --job-id wiz --music-path path/to/bed.m4a --no-reuse

# One SFX cue per scene, placed at each cut (Freesound is free)
ve explain script.md --job-id wiz --music-path path/to/bed.m4a \
    --sfx-provider freesound_sfx
```

The command prints the plan's still-reuse table, then asks for approval with
the same UX as the `ve run` gate (skip it with `--auto-approve`). It prices its
own request list, so the line items differ from a `ve estimate`/`ve run` for
the same job; it is the same approval flow, not the same pricing path. It
exits non-zero when a QA check fails and writes the full table to
`projects/<job>/qa.json`.

With `--sfx-provider`, the SFX requests join that priced list (a free provider
prices at $0.00 but still appears), the clips are mixed after the music bed and
before the captions so `loudnorm` measures the finished mix, and each cue
starts on its scene's cut. What gets searched is a short keyword rather than
the scene's diffusion prompt: a map asks for `map whoosh`, a typographic card
for `soft impact`, and a still for the sound its description names (a delta
scene asks for `water`). Search-backed providers (`freesound_sfx`,
`openverse_sfx`, `earshot_sfx`) take those keywords because their `prompt` *is*
the search query; a generative provider such as `elevenlabs_sfx` still receives
the full visual description.


Provider flags default to `--tts-provider tts_router`, `--image-provider
auto` and `--music-provider poyo_music`. `auto` routes the stills through
`assets.image_chain` (see `docs/POYO_ROUTING.md`); name a provider to bypass
the chain. **`poyo_music` is currently stale
against the live PoYo API** — it submits a retired model id, the request
returns 404, and the fallback only fires when a request succeeds without a
`task_id`. Narration and stills are fetched concurrently, so a run without a
local bed pays for both and *then* fails with
`RuntimeError("1 asset request(s) failed: …")` and no output. Until the
provider is updated, `--music-path <file>` is required. `--caption-preset`
overrides the script's caption preset (for example `karaoke`).

`script.md` format (abridged excerpt — a real script carries enough narration
to fill `target_seconds`; this one would fail the duration check):

```markdown
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: shubh_en_narration
voice_model: bulbul:v4-flash
voice_language: en-IN
voice_pace: 1.1      # optional speaking speed, 0.5-2.0 (Sarvam)
design: cream_editorial
reuse_threshold: 0.30
target_seconds: 50
music_mood: warm minimal ambient
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That is what Google just paid.

## Scene 2 — The racks
visual: still
subject: racks
image: |
  two black server racks in a warm cream data centre, soft overhead light
narration: |
  Wiz is five years old. Its job is to watch the whole cloud.
```

### Music-driven video (`musical_video`)

```bash
ve run ./song.mp3 --job-id my-song \
    --work-root ./projects --output-root ./outputs \
    --media-dir ./visuals \
    --segment-seconds 5.0

# With lyric captions (one language per run)
ve run ./song.mp3 --job-id my-song \
    --work-root ./projects --output-root ./outputs \
    --media-dir ./visuals \
    --lyrics ./lyrics.txt \
    --language en
```

If the built-in render loses audio or clips feel too fast, the direct FFmpeg
composer in `scripts/compose_musical_video.py` bypasses Remotion and gives
deterministic control over transitions, Ken Burns strength, and clip speed:

```bash
python3 scripts/compose_musical_video.py \
    --music ./song.mp3 \
    --media-dir ./visuals \
    --output ./final.mp4 \
    --segment-seconds 8.0 \
    --transition-duration 1.5
```

The direct composer uses only the supplied `--music` file as audio (it drops
original audio from video clips), applies Ken Burns only to still images,
keeps video clips at plain scale/crop, slows clips to 50% speed, and halves
image motion amplitude.

### Musical video through the IR (`ve musical`)

`ve musical` is the engine path: it plans the xfade timeline, builds one
`Composition`, renders it with HyperFrames, muxes the music as the sole audio
track and normalises loudness. The legacy scripts above remain the FFmpeg
reference/fallback for one release.

```bash
ve musical \
    --music ./song.mp3 \
    --media-dir ./visuals \
    --lyrics ./lyrics.txt \
    --output ./final.mp4 \
    --segment-seconds 8.0 \
    --transition-duration 1.5
```

- `--media-dir` is searched for images and videos; they cycle in name order.
  Generated `fixed_audio` / `_remux` intermediates are skipped.
- `--lyrics` takes one caption per non-empty line. Devanagari is bundled, so
  the captions render the same on any machine.
- Scene boundaries are linear crossfades of `--transition-duration` seconds;
  stills get the composer's halved Ken Burns drift, videos play at 50% speed.
- `--swell at:db:duration` (repeatable) applies music gain points, e.g.
  `--swell 10:3:2` boosts 3 dB across 10–12 s (#316).
- `--quantize beat|downbeat` snaps each planned cut to the nearest measured
  beat (or bar start) within `--beat-tolerance` seconds (default 0.12) and
  writes `plan/beat_grid.md` with planned/snapped deltas. `off` is the default.
- QA gates the composition, a non-blank frame per scene, duration within
  0.1 s and loudness at -14 LUFS / -1 dBTP; `--skip-qa` skips them.
- The parity harness rebuilds the same fixture through both paths:
  `uv run python scripts/parity_rebuild.py --case musical` (slow test:
  `uv run pytest -m slow tests/test_musical_parity.py`).

### Long-form chapter packs (`ve longform`)

```yaml
# longform.yaml
job_id: cosmic-saju-long
chapters:
  - pack: chapters/00-intro/short.yaml
  - pack: chapters/01-pillars/short.yaml
transition: crossfade
transition_seconds: 0.5
output_name: final.mp4
```

```bash
ve longform longform.yaml --auto-approve
```

Each chapter renders as its own job (`<job>-ch00`, …) with the normal estimate
and QA gates, so a failed chapter can be re-run without paying for the others.
Chapters are joined with a stream copy (`transition: cut`) or an `xfade` +
`acrossfade` chain, and the joined file gets one measured QA pass. Per-chapter
paths and the joined verdict land in `work/<job>/longform.json`.

### Voice cloning (`ve voice-clone`)

```bash
# One-time: upload a 10–15 s clean reference clip and preview the clone.
ve voice-clone create --name creator --audio reference.wav --language en-IN

# Manage the workspace's voices.
ve voice-clone list
ve voice-clone delete svc-...
```

`create` stores the returned `svc-…` id under the machine cache
(`<data>/cache/voice_clones/`), synthesizes a fixed preview line (disable with
`--no-preview`), and prints the pack snippet:

```yaml
voice:
  provider: sarvam_tts
  voice_id: "sarvam_clone:svc-..."
  language: en-IN
```

The clone path calls `POST /voices/clone`; lines over 1,000 characters are
refused rather than truncated. Per-line `pause_after` (seconds) inserts a
measured silence after that line in the narration join.

### Brand capture (`ve brand-capture`)

```bash
ve brand-capture https://acme.test --out brands/acme.json --assets-dir brands/acme
```

Fetch the page, read the icon, OG image, palette and `font-family` declarations,
download the images, and write a `BrandProfile` JSON. The HTML declarations
seed the profile; computed CSS and full-page screenshots would need a browser.
Review the profile before a build uses it (missing values are empty, not guessed).

### Vision critique (`ve critique`)

```bash
ve critique my-job --max-frames 12 --cell 384
```

Sends a contact sheet of the job's QA frames to the `critique` task (a vision
model; Gemini Flash by default) and scores hierarchy, motion variety, depth,
brand fit, sound sync, legibility and pacing 1–10. It writes
`work/<job>/qa/critique.json` and prints the table. The scores are advisory;
the technical QA gates stay authoritative. `--pack short.yaml` applies the
motion patches to a copy and writes `short.patched.yaml`. `--rebuild --pack
short.yaml --yes` runs the scored/patch/rebuild loop for up to `--max-rounds`
until every axis reaches `--target` (default 8); it reuses the job's approved
estimate, so a rebuild cannot exceed the approved spend.

### Reference style extraction (`ve style-from`)

```bash
ve style-from ./reference.mp4 --out style.md --design-out design.json
```

Measures the reference's grammar in code: scene cuts (`--scene-threshold`,
default 0.3), shot lengths, cut rate, fps/aspect and a palette quantized from
`--frames` sampled frames (`--colors`, default 5). It writes a markdown guide
and an optional DesignTokens draft. `--vision` adds one Flash-class call that
writes the prose interpretation (type, transitions, camera moves, grain); it is
told to take the grammar and never the content.

### Storyteller templates (`storyteller_budget` / `storyteller_hd` / `storyteller_premium`)

The storyteller templates produce multi-voice, multi-language story videos
from a single markdown script with YAML front-matter (`cast`, `language`,
`music_mood`) and `## Speaker:` sections. They route TTS through Sarvam
Bulbul v3 for Indian languages and ElevenLabs otherwise, generate stills and
motion clips via PoYo, mix a PoYo music bed, and export to a FableCut
timeline for browser preview. See `docs/templates.md` for the catalog and
`docs/SARVAM_INTEGRATION.md` for the TTS provider details.

```bash
# Run a storyteller job (pick any of the three tiers)
ve run ./story.md --job-id story \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_hd

# Mix tiers: keep the storyteller_hd preset but upgrade motion to premium
ve run ./story.md --job-id story \
    --work-root ./projects --output-root ./outputs \
    --template storyteller_hd --video-tier budget --image-tier premium
```

`--image-tier {budget,standard,premium}` and
`--video-tier {budget,standard,premium}` independently override the
template preset's default image/video tier. Each flag is optional; when
omitted, the template's preset tier is used. Use them to mix tiers without
switching templates (e.g. `storyteller_hd` with `--video-tier budget` for a
cheaper draft, or `storyteller_premium` with `--image-tier standard` to drop
cost temporarily).

`--image-provider {auto,agy_image,poyo_ai,fal_ai,...}` chooses the still
provider. The default, `auto`, walks `assets.image_chain` (agy first, then the
paid fallbacks). Naming a provider bypasses the chain for every still. The
flag exists only on the storyteller path: other `ve run` templates build no
image requests, so passing a non-`auto` value there is rejected instead of
silently ignored.

After the run, export the scene plan to a FableCut project for browser
preview/trim, then render the final cut:

```bash
# Re-export the scene plan to a FableCut project
ve storyteller-export <job-id> \
    [--project-dir ./outputs/<job-id>-fablecut]

# Edit in the bundled FableCut editor, then render the final cut
ve render-fablecut <job-id> \
    --project-dir ./outputs/<job-id>-fablecut \
    --output-name final.mp4 \
    --work-root ./projects --output-root ./outputs
```

Audition a single TTS voice (useful for picking the right Sarvam/ElevenLabs
voice before the full run):

```bash
ve tts-test --text "नमस्ते, दोस्त" --voice sarvam:shubh --language hi-IN
```

The `storyteller_export` worker payload (for `ve enqueue storyteller_export`):

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `job_id` | string | yes | Per-job identifier; selects the workspace. |
| `project_dir` | string | yes | Target FableCut project directory. |
| `plan_name` | string | no | Scene-plan filename (default `scene_plan.json`). |
| `aspect_ratio` | string | no | Output aspect ratio (default `16:9`). |

The handler reads `scene_plan.json` from the project's parent or
`projects/<job_id>/scene_plan.json` and writes a FableCut project folder.
Work/output directories come from the job context (the common `work_root` /
`output_root` payload keys), not from this payload's schema.

Round-trip import (`fablecut_to_storyteller`) is library-only for now; no
`ve storyteller-import` CLI yet.

#### Burning lower-bottom pop-style captions

The composer deliberately does not handle captions — it is visuals + audio +
transitions only. To add Devanagari (or any) lyric captions, run the separate
caption burner as a post-process step:

```bash
# 1. Compose visuals + audio (no captions)
python3 scripts/compose_musical_video.py \
    --music ./song.mp3 --media-dir ./visuals \
    --output ./base.mp4 \
    --segment-seconds 10 --transition-duration 2.0 \
    --width 1920 --height 1080 --fps 30

# 2. Burn per-line captions onto the base video
python3 scripts/burn_musical_captions.py \
    --base-video ./base.mp4 \
    --lyrics ./lyrics.txt \
    --output ./final.mp4
```

`burn_musical_captions.py` treats every non-empty line of the lyrics file as
one caption. Time windows are divided equally across the base video's
duration (a 180s video with 24 lines gets 7.5s per line). Each caption pops
in over 0.35s (fontsize 22→40 + alpha 0→1) on a translucent black backdrop
centered at the lower bottom. The default font is
`/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf`; override with
`--font` for other Devanagari-capable faces. Section headers like
`फलशृति` are folded into the next non-empty line as a `Header: lyric`
prefix so they remain visible without consuming a full time window.

Why the two-step pattern: re-rendering captions (lyric tweak, font change,
timing adjustment) is a 3-minute pass on the existing base video, not a
3-minute + 19-xfade pass. This mirrors the established `mix_sfx_into_video`
(SFX) and `mux_voiceover` (VO) post-process steps.

### Vlogger + tech YouTuber templates (`vlog_reel`, `talking_head_polish`, `tech_tutorial`, `explainer_motion`, `podcast_video`, `shorts_factory`)

The vlogger and tech YouTuber templates turn raw clips, screen recordings, or
scripts into polished, publish-ready videos. They are wired into the same
`ve run` flow as the storyteller templates, but each template has its own
default pipeline kind and preset:

| Template | Pipeline kind | Source | Default preset |
| --- | --- | --- | --- |
| `vlog_reel` | `raw_to_video` | directory of raw clips | `vlogger_youtube` |
| `talking_head_polish` | `raw_to_video` | single talking-head video | `vlogger_short` |
| `tech_tutorial` | `screen_to_video` | screen recording (+ optional webcam/script) | `tech_tutorial` |
| `explainer_motion` | `script_to_video` | markdown script + supplied clips | `tech_tutorial` |
| `podcast_video` | `podcast_to_video` | multi-track audio + optional video | `podcast_studio` |
| `shorts_factory` | `shorts_from_long` | long-form video (via MCP tool) | `shorts_factory` |

Examples:

```bash
# Vlog reel from a directory of raw clips
ve run ./clips/ --template vlog_reel --job-id my-vlog \
    --work-root ./projects --output-root ./outputs \
    --reframe --broll --sfx --transitions

# Talking-head single take, polished + reframed
ve run ./talk.mp4 --template talking_head_polish \
    --job-id my-talk --work-root ./projects --output-root ./outputs \
    --reframe --reframe-aspect 9:16

# Tech tutorial from a screen recording (cursor zoom + click rings + captions)
ve run ./screen.mp4 --template tech_tutorial --job-id my-tutorial \
    --work-root ./projects --output-root ./outputs --skip-music
```

`shorts_factory` is not wired into `ve run` yet: it is exposed as an MCP
tool (`shorts_factory`) that slices a long-form video into N vertical
Shorts with viral-moment detection. Call it with `video_path`, an
optional `transcript`, and `count` (default 4). `thumbnail` and
`beat_sync` are MCP-only as well.

Optional flags for `raw_to_video` templates (vlog_reel, talking_head_polish):

| Flag | Default | Description |
| --- | --- | --- |
| `--reframe` | off | Reframe to a vertical (9:16) format with face-tracked smart crop |
| `--reframe-aspect` | `9:16` | Target aspect ratio for `--reframe` |
| `--broll` | off | Insert B-roll coverage over the base video using transcript cues |
| `--sfx` | off | Place context-aware sound effects based on transcript keywords |
| `--transitions` | off | Auto-select transitions between rough-cut segments |
| `--audio-cleanup` | off | Run the de-noise/vocal-isolation pre-stage on the source audio |

These templates are also exposed in the MCP server (Task 20) as individual
skill tools: `broll`, `reframe`, `screen_tutorial`, `sfx_placement`,
`chapters`, `dubbing`, `shorts_factory`, `thumbnail`, `beat_sync`. Each one
takes a single `video_path` (or `transcript` for the doc-only ones) and
returns the same artifacts the pipeline would write.

### Talking avatar over a screen recording (`ve talking-avatar`)

Overlays a lip-synced cartoon avatar (PNG sprite folder or layered SVG) in a corner of a recording, with optional
speech bubbles and avatar actions. English narration. Recipe for whole tutorials: `docs/TECH_TUTORIALS.md`.

```bash
uv run ve talking-avatar --screen screen.mp4 --audio narration.wav --avatar avatar/sprites \
  --script narration.txt --bubbles bubbles.yaml --out out/video.mp4
```

| Flag | Default | Meaning |
|---|---|---|
| `--lipsync` | `hybrid` | `hybrid` (Rhubarb shapes, lip closures on wav2vec2-measured M/B/P, dictionary vowel fixes), `rhubarb`, or `loudness` |
| `--script` | — | Dialogue text; required by `hybrid` unless `--align-hints` is given |
| `--align-hints` | — | Precomputed hints JSON (skips the aligner subprocess) |
| `--lead` | `0.02` | Seconds the mouth leads the audio |
| `--bubbles` | — | YAML of bubbles (`word`/`nth`/`time`, `text`, `emoji`, `hold`) and actions (`action: glance|brows`) |
| `--position` / `--size` / `--margin` | `br` / `0.30` / `32` | Corner, avatar height as a fraction of the frame, edge distance |
| `--no-motion`, `--bounce`, `--no-crossfade` | off / off / off | Disable breathing/tilts/slide; enable head bounce (the user prefers none); disable the 1-frame mouth crossfade |
| `--stress-open/--no-stress-open`, `--stress-percentile` | on, `80` | Wider mouth on loud syllables |

The hybrid aligner runs from an optional environment (CPU torch, ~1 GB), created once:

```bash
uv venv .venv-lipsync && uv pip install --python .venv-lipsync/bin/python torch --index-url https://download.pytorch.org/whl/cpu && uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy
```

Without it, `hybrid` warns and falls back to `rhubarb`. Hints are cached per audio + script in the render work dir.
A sidecar `<out>.json` records the engine, every frame's mouth shape and the bubbles.

## Utility commands

### Tool doctor

`ve doctor` verifies the external render stack: FFmpeg plus the filters the
engine uses (`xfade`, `loudnorm`, `subtitles`), HyperFrames, Chrome and the
optional `agy` agent. Use `--json` for a machine-readable report. Exit code 1
means a required tool (FFmpeg or HyperFrames) is missing.

```bash
ve doctor
ve doctor --json
```

### Queue inspection

Peek at the local SQLite queue without consuming anything:

```bash
ve queue-size                    # pending jobs across all tenants
ve queue-size --tenant-id acme   # scoped to a single tenant
ve dequeue                       # inspect the next pending message
```

`ve dequeue` removes the next pending job from the queue and prints
its command, job id, and payload to stdout. It is useful for
inspecting what a test or another process has enqueued; the job is
not requeued automatically.

### Media probing

```bash
ve probe ./demo/raw.mp4
# Duration: 10.000s, 1920x1080 @ 30.00fps
```

`ve probe` is a thin wrapper around `ffprobe` that prints duration,
width, height, and frame rate. It requires `ffprobe` on PATH.

## Running the worker

`ve worker` is a queue consumer — it pulls `JobMessage` entries
from the SQLite queue and dispatches each one to the matching
pipeline / skill. It is **not** needed for the one-shot `ve run`
flow; it's a separate path for fan-out or async workloads. The
queue lives at `VE_QUEUE_DB`, or `<output root>/queue.db` when that
variable is unset, so `ve enqueue` and `ve worker` can run in
separate processes.

```bash
# In one terminal — enqueue a job
ve enqueue analyze --job-id my-job --tenant-id default \
    --payload '{"work_root": "./projects", "output_root": "./outputs", "force": true}'

# In another — drain the queue
ve worker --tenant-id default --max-jobs 1
```

The worker takes a per-job `flock` on `work/<job_id>/.lock`, so two
workers cannot render into the same directory. `ve enqueue` also sets
an idempotency key to the job id: repeating the exact enqueue returns
the existing job instead of adding a second one.

### Front-end parity (#303, A11)

Capabilities should be reachable from the CLI, the worker, and (where safe)
MCP. Anything deliberately CLI-only is listed as such below.

| Capability | CLI | Worker command | MCP tool |
| --- | --- | --- | --- |
| Raw / skills pipeline | `ve run` | `run` | `run_pipeline` |
| Content-pack short | `ve short` | `short` (needs an approved `plan/cost_estimate.json`) | `run_short` |
| Musical video | `ve musical` | `musical` | — (CLI/queue only) |
| Single skill stage | `ve rough-cut` etc. | skill command | `run_skill` |
| Asset resolution | `ve assets` | registry command only (CLI) | guarded asset tools |
| Queue status | `ve queue-size` | — | `job_status` |

`ve explain` and `ve estimate` are `cli_only`: they prepare the approved
estimate the `short`/`run` handlers require, and the queue never approves
spend by itself.

## Enqueueing jobs

`ve enqueue <command> --job-id <id> --payload '<json>'` enqueues
a job for later consumption by `ve worker`. The command name must
match one of the worker handlers listed below; the payload is a
JSON object whose supported keys depend on the command.

### Common payload keys

These keys are understood by the worker for **every** command and
are passed through to `JobContext` before the handler runs:

| Key           | Type   | Default          | Description                                                                          |
| ------------- | ------ | ---------------- | ------------------------------------------------------------------------------------ |
| `work_root`   | string | resolved (below) | Filesystem path to the per-job workspace. The worker will `mkdir -p` it.             |
| `output_root` | string | resolved (below) | Filesystem path to the per-job export directory. The worker will `mkdir -p` it.      |

When either key is omitted the worker resolves it through
`video_editor_state.paths` (`VE_WORK_ROOT` / `VE_OUTPUT_ROOT` →
`VIDEO_EDITOR_DATA_DIR/projects` or `/outputs` → container mount →
`./projects` / `./outputs`) and prints the choice on stderr. Pass both
explicitly when you want the job pinned to a specific directory.

### Per-command payload contract

The table below lists every command the worker understands
(plus its handler in `packages/pipelines/src/video_editor_pipelines/handlers.py`)
and the payload keys it reads. Keys not listed here are ignored.

| Command        | Handler                  | Required keys                    | Optional keys                                              |
| -------------- | ------------------------ | -------------------------------- | ---------------------------------------------------------- |
| `ingest`       | `_handle_ingest`         | `source_dir`                     | `force`                                                    |
| `transcribe`   | `_handle_transcribe`     | (none)                           | `transcript_path`, `force`                                 |
| `analyze`      | `_handle_analyze`        | (none)                           | `force`                                                    |
| `plan`         | `_handle_plan`           | (none)                           | `config_path`, `force`                                     |
| `cut`          | `_handle_cut`            | (none)                           | `force`                                                    |
| `captions`     | `_handle_captions`       | (none)                           | `force`                                                    |
| `broll`        | `_handle_broll`          | (none)                           | `broll_provider`, `force`                                  |
| `preview`      | `_handle_preview`        | (none)                           | `force`                                                    |
| `qa`           | `_handle_qa`             | (none)                           | `force`                                                    |
| `export`       | `_handle_export`         | (none)                           | `force`                                                    |
| `run`          | `_handle_run`            | `source`                         | `template`, `transcript_path`, `config_path`, `force`, `broll_provider`, `preset`, `presets_dir`, `music_path`, `output_name`, `qa`, `loudness_target_lufs`, `expected_aspect_ratio`, `verify`, `music_prompt`, `music_provider`, `music_duration_seconds`, `sfx_provider`, `sfx_duration_seconds`, `sfx_gain_db`, `media_dir`, `segment_seconds`, `lyrics`, `language` |
| `short`        | `_handle_short`          | `pack`                           | `final`, `config_path`                                     |
| `musical`      | `_handle_musical`        | `music`, `media_dir`, `lyrics`   | `output_name`, `width`, `height`, `fps`, `segment_seconds`, `transition_seconds`, `qa`, `config_path` |
| `verify`       | `_handle_verify`         | (none)                           | `work_root`, `output_root`                                 |
| `rough_cut`    | `_handle_rough_cut`      | `source`                         | `transcript_path`                                          |
| `graphics`     | `_handle_graphics`       | (none)                           | `presets_dir`, `preset`, `qa_per_segment`                  |
| `second_pass`  | `_handle_second_pass`    | `edit`                           | `presets_dir`                                              |
| `captions_v2`  | `_handle_captions_v2`    | (none)                           | `presets_dir`, `output_name`                               |
| `music`        | `_handle_music`          | (none)                           | `music_path`, `output_name`                                |
| `brief`        | `_handle_brief`          | `from_md`                        | (none)                                                     |
| `music_generate` | `_handle_music_generate` | `prompt`                       | `music_provider`, `music_duration_seconds`                 |
| `sfx_generate`   | `_handle_sfx_generate`   | (none)                         | `provider`, `duration_seconds`, `plan_name`                |
| `align`          | `_handle_align`          | (none)                         | `audio`, `text`, `align_provider`, `output_name`          |
| `export_comfystudio` | `_handle_export_comfystudio` | `project_dir`              | `plan_name`, `fps`, `width`, `height`, `music_path`       |
| `import_comfystudio` | `_handle_import_comfystudio` | `project_dir`              | `plan_name`, `plan_out_name`                              |
| `export_fablecut` | `_handle_export_fablecut` | `project_dir`              | `plan_name`, `fps`, `width`, `height`, `music_path`       |
| `import_fablecut` | `_handle_import_fablecut` | `project_dir`              | `plan_name`, `plan_out_name`                              |
| `audio`          | `_handle_audio`          | `source`, `voiceover`          | `voiceover_gain_db`, `output_name`                        |
| `sfx_mix`        | `_handle_sfx_mix`        | (none)                         | `plan_name`, `base`, `sfx_gain_db`                         |
| `audio_isolate`  | `_handle_audio_isolate`  | `audio`                          | `provider`, `file_format`, `output_name`                                |
| `speech_to_speech` | `_handle_speech_to_speech` | `audio`                        | `voice_id`, `provider`, `model_id`, `output_name`, `remove_background_noise`, `output_format`, `output_ext`, `seed`, `stability`, `similarity_boost` |

#### Path-typed keys

Keys that are filesystem paths (`source`, `source_dir`, `pack`,
`transcript_path`, `config_path`, `presets_dir`, `music_path`, `music`,
`media_dir`, `lyrics`, `audio`, `text`, `output_name`, `from_md`,
`voiceover`, `project_dir`) accept any string the OS will accept
as a path. The worker coerces them to `pathlib.Path` before passing
them to the handler. Path-typed keys whose value comes from a queued
payload are contained to the job's `work_dir`/`output_dir` via
`_resolve_payload_path` (see #165, #169, #210).

#### The `edit` key (second_pass only)

`second_pass` requires an `edit` object — a single
`SegmentEdit` (see `packages/skills/src/second_pass/models.py`)
serialized to JSON. The required field is `segment_id`; at
least one of `new_source_start`, `new_source_end`, or
`new_label` must also be present:

```json
{
  "segment_id": "seg_0001",
  "new_source_end": 8.5,
  "reason": "tighten the intro"
}
```

#### The `force` key

Almost every command accepts `force` (boolean, default `false`).
When `true`, the corresponding stage re-runs even if its output
artifacts already exist on disk (bypasses the segment cache).

#### The `preset` / `presets_dir` keys

`preset` selects a named entry under `presets/` (default
`short_explainer`). `presets_dir` overrides the location of the
presets directory (default: the repo-relative `presets/`).

#### The `broll_provider` key

For `broll` and `run` (directory source only), selects the
b-roll generation backend. `placeholder` is the safe default
(no external API calls); `fal_ai` enables the FAL.AI
integration (requires `FAL_KEY`). For `run` on a single file
(v2 skills pipeline), the flag is ignored and a warning is
emitted — see Bug #25.

### Examples

Enqueue an `analyze` job:

```bash
ve enqueue analyze \
    --job-id demo-analyze \
    --tenant-id default \
    --payload '{
        "work_root": "./projects",
        "output_root": "./outputs",
        "force": false
    }'
```

Enqueue the full `run` pipeline against a directory of raw
footage:

```bash
ve enqueue run \
    --job-id demo-run \
    --tenant-id default \
    --payload '{
        "work_root": "./projects",
        "output_root": "./outputs",
        "source": "./raw",
        "template": "raw_batch",
        "transcript_path": "./demo/transcript.json",
        "preset": "short_explainer",
        "broll_provider": "placeholder"
    }'
```

Enqueue a `second_pass` edit:

```bash
ve enqueue second_pass \
    --job-id demo-2nd \
    --tenant-id default \
    --payload '{
        "work_root": "./projects",
        "output_root": "./outputs",
        "edit": {
            "segment_id": "seg_0001",
            "new_source_end": 8.5,
            "reason": "tighten the intro"
        }
    }'
```

## v2 skills pipeline (recommended)

For new projects, use `ve run` (or the individual v2 skill
commands below). The v2 pipeline is single-file oriented and
produces a polished final MP4 with one command.

```bash
ve run ./demo/raw.mp4 \
    --job-id my-job \
    --work-root ./projects \
    --output-root ./outputs \
    --transcript ./demo/transcript.json \
    --preset short_explainer

# With AI-generated music and SFX
ve run ./concept.md --job-id my-job \
    --work-root ./projects --output-root ./outputs \
    --template explainer_reel \
    --music-prompt "upbeat corporate" --music-provider poyo_music \
    --sfx-provider elevenlabs_sfx
```

### Muxing an external voiceover (`ve audio`)

For narration-over-existing-video workflows where the
voiceover is a separate file (e.g. TTS output, recorded
narration), `ve audio` muxes it onto the source video's
audio track before `ve run`. The engine's music-mix and
QA stages assume the speech is already in the source's
audio; this command makes that true.

```bash
ve audio ./raw.mp4 \
    --job-id vo-job \
    --work-root ./projects \
    --output-root ./outputs \
    --voiceover ./voiceover.mp3 \
    --voiceover-gain-db 3.0
```

The output is written to `<work>/<job-id>/intake/with_voiceover.mp4`
(or `<output_name>` if supplied), which can then be fed
directly into `ve run`. The visual track is copied
(`-c:v copy`); the audio is re-encoded to AAC 192k after an
optional gain and a resample to 44100 Hz. `-shortest` bounds
the output to the shorter of the source video and the
voiceover. The same command is available via the worker as
`ve enqueue audio` with payload keys `source`, `voiceover`
(required) and `voiceover_gain_db`, `output_name` (optional).

Individual v2 skill commands (each one is a wrapper around a
skill in `packages/skills/src/<name>/`):

```bash
ve rough-cut ./demo/raw.mp4 my-job
ve graphics my-job --preset short_explainer
ve second-pass my-job --segment seg_0001 --end 8.5
ve captions my-job
ve music my-job --music-path ./demo/music.m4a
ve export my-job
```

### Generative audio extras (`ve music-generate`, `ve sfx-generate`, `ve sfx-mix`)

Generate AI background music or per-scene SFX via ElevenLabs, then mix them
into the v2 pipeline:

```bash
# Generate a 30s music bed; MusicSkill auto-discovers it when --music-path is omitted
ve music-generate my-job --prompt "upbeat corporate tech" --music-provider poyo_music

# Generate one SFX clip per scene from scene_plan.json, then mix onto the base video
ve sfx-generate my-job --provider elevenlabs_sfx
ve sfx-mix my-job --base with_broll.mp4 --sfx-gain-db -6
```

`ve run` can also generate and mix music/SFX in one shot:

```bash
ve run ./concept.md --job-id my-job \
    --music-prompt "upbeat corporate" --music-provider poyo_music \
    --sfx-provider elevenlabs_sfx
```

### Audio isolation and speech-to-speech (`ve audio-isolate`, `ve speech-to-speech`)

```bash
ve audio-isolate my-job --audio ./noisy.mp3 --provider elevenlabs_isolation
ve speech-to-speech my-job --audio ./voice.mp3 \
    --voice-id Xb7hH8MSUJpSbSDYk0k2 --provider elevenlabs_speech_to_speech
```

Use the corresponding `stub_*` providers for offline tests.

`--audio` is a `pathlib.Path` and is expanded with `expanduser()`
before the existence check, so a quoted `~/audio/voice.mp3` resolves
under the invoking user's home directory (#181).

`ve audio-isolate` accepts `--file-format` (default `other`; use
`pcm_s16le_16` when the source is raw PCM and the output is `.wav`,
`.flac`, `.aiff`, or `.pcm`) which is forwarded through the worker
payload (`file_format`) to the provider. The provider reconciles the
format with the `--output-name` extension so a `.wav` output never
returns MP3 bytes (#175).

`ve speech-to-speech` accepts `--output-format` (e.g. `pcm_44100`
for `.wav`), `--output-ext` (overrides the extension derived from
`--output-name`), `--seed`, and the voice-settings pair
`--stability` / `--similarity-boost`. These survive enqueue through
`_SpeechToSpeechPayload` and are forwarded to the provider (#180).

### Planning a script (`ve plan-script`)

Decompose a markdown script into a scene plan JSON file:

```bash
ve plan-script ./script.md my-job --config ./config.yaml \
    --aspect-ratio 16:9 --music-mood "upbeat corporate"
```

The output is written to `<work>/plan/scene_plan.json` and can
be fed to `ve assets` for generative asset resolution.

### SFX mixing (`ve sfx-mix`)

After `ve sfx-generate` has produced per-scene SFX clips and a
`<work>/sfx/sfx_map.json` mapping, `ve sfx-mix` lays them onto
the audio track of the base b-roll video:

```bash
ve sfx-mix my-job --base with_broll.mp4 --sfx-gain-db -6
```

The output is `<work>/broll/with_broll_sfx.mp4`. The same
operation is available via the worker as `ve enqueue sfx_mix`
with optional payload keys `plan_name` (default
`scene_plan.json`), `base` (default `with_broll.mp4`), and
`sfx_gain_db` (default `-6.0`).

### Resolving generative assets (TTS / images / data viz / video)

`ve assets` runs the `AssetIngestionManager` over a scene plan
and writes the enriched plan to `<work-dir>/plan/scene_plan_with_assets.json`.
By default it emits TTS + IMAGE + (optionally) DATA_VIZ assets
for every populated scene field:

```bash
ve assets my-job --tts-provider elevenlabs_tts --image-provider poyo_ai
```

**Hybrid visuals: opt in to VIDEO generation only where motion is needed.**

`--video-provider` enables a motion clip, but the pipeline now
emits it only for scenes whose `props.requires_motion` is true.
Scenes without that flag fall back to a static IMAGE rendered
with a gentle Remotion Ken Burns pan/zoom (scale + translate
animated over the scene duration).

```bash
ve assets my-job --image-provider poyo_ai --video-provider poyo_ai
ve assets my-job --image-provider fal_ai --video-provider hyperframes_video
ve assets my-job --image-provider stub_image --video-provider stub_hyperframes_video
ve assets my-job --image-provider openrouter --video-provider openrouter
```

**Provider `auto` (cost-first).** Pass `--image-provider auto` (etc.) to let the
selector choose. With a named model in the request, `auto` is **cost-first**:
only providers that can serve that model are considered (exact id or via
`models.yaml:model_equivalents`) and the **cheapest** wins; the canonical model id
is translated to the chosen provider's id. Explicitly named providers are never
rerouted. For **images**, `auto` yields to a configured `assets.image_chain`:
the chain runs first, and the cost-first selector only applies when the chain
is empty. See [`MODEL_ROUTING.md`](MODEL_ROUTING.md).

**OpenRouter.** `openrouter` is an opt-in provider for image/video/music (image
`POST /v1/images`, video async `POST /v1/videos`, music via chat-completions
audio). PoYo remains the default. See
[`OPENROUTER_GENERATION.md`](OPENROUTER_GENERATION.md).

**FAL video default.** When `--video-provider fal_ai` is used, the
provider now defaults to `bytedance/seedance-2.0/mini/text-to-video`
(the legacy `fal-ai/luma-dream-machine` endpoint is deprecated and
returns 500 on poll). Seedance models default to `generate_audio: false`
to avoid content-policy failures on generic prompts; pass
`provider_options` or `model_kwargs` if you really need generated audio.

A scene plan requesting motion looks like:

```json
{
  "id": "scene_1",
  "visual_description": "A character walks through a doorway.",
  "props": {"requires_motion": true}
}
```

When `props.requires_motion` is true, the asset builder emits
both VIDEO and IMAGE; the VIDEO is appended **before** the IMAGE
in `scene.assets` so Remotion's `scene.assets.find(image|video|data_viz)`
picks the motion clip. The IMAGE stays as a low-cost fallback.
Scenes with `requires_motion: false` (or absent) emit IMAGE only
and Remotion applies an always-on Ken Burns pan/zoom (pan/zoom
parameters can be overridden via `props.motion.start` and
`props.motion.end`; `props.motion.style` is no longer used).

HyperFrames renders a prompt-driven HTML composition to MP4 via
the local CLI. Configure it under `assets.providers.hyperframes_video`:

```yaml
assets:
  providers:
    hyperframes_video:
      timeout: 600.0
      provider_options:
        cli_path: hyperframes
        width: 1920
        height: 1080
        fps: 30
  defaults:
    video: hyperframes_video
```

**Cost:** HyperFrames uses the local CLI (Chrome + FFmpeg), so the
only cost is your own compute. The IMAGE asset still costs money when a
cloud provider is used; the PoYo default (`nano-banana-2-lite`) is a flat
5 credits, ~$0.025 per image. `gpt-image-2` at 16:9 1K low-quality is
~$0.01, but its price scales with the quality and resolution tiers. See
`docs/POYO_ROUTING.md` for the full cost/model mapping.

### Image provider chain

Images try `agy_image` first, then `poyo_ai`, `openrouter`, `fal_ai`
(`assets.image_chain`). Pin one provider for a run with `--image-provider
poyo_ai`, which bypasses the chain. A run stops after
`assets.max_images_per_run` image requests that miss the cache (default 25)
with `ImageQuotaError`; copying an existing file through `local_media` does not
count. The chain ends on `fal_ai`, whose stub fallback turns a failed
generation into a placeholder asset. A pre-flight failure on the last provider
— an open circuit breaker or an exhausted `--budget` — still surfaces as an
error.

### Installing a project brief

A `project.md` brief is the per-project memory the engine reads
into `ctx.brief` (and renders into `report.md`'s "Project brief"
section). Drop one into a job's work directory with:

```bash
ve brief my-job --from-md ./my-project.md \
    --work-root ./projects --output-root ./outputs
```

The parser accepts any `*.md` (not just files literally named
`project.md`) and validates the YAML front-matter; malformed
front-matter falls back to "H1 → title, body → notes" rather
than raising. The same operation is available asynchronously:

```bash
ve enqueue brief -j my-job --payload '{"from_md": "./my-project.md", "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1
```

### QA self-evaluation

Two opt-in QA hooks block the build when a known-bad render is about
to ship:

- **`--qa`** on `ve run` — runs the full QA pass (`QASkill.run`) on
  the captioned output between the music and export stages. If any
  check fails (aspect ratio, audio loudness, black frames, etc.),
  the pipeline raises and the export stage is skipped so the
  deliverable is never written. Use this for production runs:

  ```bash
  ve run ./demo/raw.mp4 --job-id my-job \
      --work-root ./projects --output-root ./outputs \
      --transcript ./demo/transcript.json \
      --preset short_explainer --qa
  ```

  The default loudness target is -16 LUFS (YouTube/Spotify); override
  with `--loudness-target-lufs -14.0` (podcast) or any other value.
  The default expected aspect ratio is `16:9`; for short-form
  vertical video pass `--expected-aspect-ratio 9:16`.

- **`--qa-per-segment`** on `ve graphics` — runs the per-segment
  PSNR visual-jump + audio-pop check at every cut boundary. A
  per-segment artifact is written to
  `<work_root>/<job_id>/.cache/qa/{segment_id}.json`. On a failing
  check the build raises `QASelfEvalError` carrying the segment ID
  and the reason (``"visual_jump"`` or ``"audio_pop"``). Costs ~5%
  extra render time; off by default.

  ```bash
  ve graphics my-job --preset short_explainer --qa-per-segment
  ```

  Both hooks can be combined: `ve run --qa` runs the final pass
  on the captioned output, and `ve graphics --qa-per-segment`
  runs the per-segment check earlier in the pipeline. They are
  independent — opting into one does not enable the other.

### Requirements verification gate

The verification gate is the final check before a job is reported
ready. It compares the exported video against structured
requirements captured at intake. Failures raise an error and the
job stays incomplete; `NEEDS_REVIEW` results are logged but do not
block the job.

Capture requirements first:

```bash
ve requirements my-job \
    --work-root ./projects --output-root ./outputs \
    --min-duration 25.0 --max-duration 35.0 \
    --require-aspect 16:9 \
    --require-element captions \
    --require-element logo \
    --must-mention "free trial" \
    --judge-check "intro hook is compelling" \
    --judge-check "logo appears in the first 3 seconds"
```

Then run the full pipeline with `--verify`:

```bash
ve run ./demo/raw.mp4 --job-id my-job \
    --work-root ./projects --output-root ./outputs \
    --preset short_explainer --verify
```

You can also run the gate manually after export:

```bash
ve verify my-job --work-root ./projects --output-root ./outputs
```

Deterministic checks (no LLM cost):

- duration range (`--min-duration`, `--max-duration`)
- aspect ratio (`--require-aspect`)
- required / forbidden elements (`--require-element`, `--forbid-element`)
- transcript must-mention phrases (`--must-mention`)
- filler-word removal verification
- segment preservation

Subjective checks are handled by an LLM-as-judge (`--judge-check`).
Each item becomes a checklist entry; the judge returns `PASS`,
`FAIL`, or `NEEDS_REVIEW` with a brief reason. The gate uses the
`verify_judge` task type in `config.yaml`, which defaults to a
small utility model; route it to a stronger model if your checks
need visual reasoning.

Worker payloads:

- `ve requirements` writes `<work>/verify/requirements.json`.
- `ve verify` (or `ve run --verify`) writes
  `<work>/verify/verification_report.json` and appends the
  results to `<output>/report.md`.
- `ve enqueue verify --payload '{"work_root": "...", "output_root": "..."}'`
  dispatches the gate through the worker.

### Forced alignment (ElevenLabs)

`ve align` runs forced alignment on a known audio + transcript
pair via ElevenLabs' `/v1/forced-alignment` endpoint and writes
the result to `<work_root>/<job_id>/transcript/alignment.json`.
The endpoint is **free** on the ElevenLabs creator tier (2026)
and returns character-level and word-level timestamps:

```bash
ve align my-job \
    --audio ./my-narration.mp3 \
    --text "Hello, this is the exact transcript." \
    --align-provider elevenlabs_forced_alignment
```

For tests / offline use, set `--align-provider stub_alignment`
to get a synthetic alignment JSON with evenly-spaced word
timings (no API call). The output shape is identical to the
real ElevenLabs response:

```json
{
  "words": [
    {"text": "hello", "start": 0.0, "end": 0.5,
     "alignment": {"character_start": 0, "character_end": 5, "characters": [...]}},
    ...
  ],
  "characters": [],
  "transcript": "Hello, this is the exact transcript.",
  "language": "en"
}
```

The same operation is available asynchronously:

```bash
ve enqueue align -j my-job \
    --payload '{"audio": "./my-narration.mp3",
                "text": "Hello, this is the exact transcript.",
                "align_provider": "elevenlabs_forced_alignment",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1
```

The worker handler is `_handle_align` (registered in
`_HANDLERS` under "Session V: forced alignment (ElevenLabs)").

**Pipeline integration:** downstream consumers (e.g. the
`CaptionsSkill`) consume `transcript.json` with the WhisperX
shape (`{word, start, end, probability}`). Use
`scene_assets.forced_alignment_response_to_transcript()` to
convert the ElevenLabs shape to WhisperX in one line — it
renames `text`→`word`, adds `probability=1.0`, filters empty
words, and chunks into 30-word segments.

**Auto-promotion in `RoughCut.run()` (Session W):** when
`<work_root>/<job_id>/transcript/alignment.json` exists and
`transcript.json` does not, the next `ve run` (or any
`RoughCutSkill.run()` call) automatically converts the
alignment JSON into the WhisperX-shape `transcript.json` and
stamps the manifest with `stages.transcribe.outputs.source =
"alignment"`. This means a `ve align` → `ve run` flow runs
the full pipeline against the cheap, accurate alignment-driven
timestamps without ever calling WhisperX. The user-supplied
`--transcript` flag still wins over both alignment and
WhisperX.

Transcript resolution priority in `RoughCut.run()`:

1. **user-supplied** `--transcript <path>` — copied verbatim.
2. **alignment** (Session V + W) — `transcript/alignment.json`
   is converted to `transcript/transcript.json`.
3. **WhisperX fallback** — when neither is present, run the
   full WhisperX transcription pass.

### ComfyStudio timeline bridge

The engine can export a `ScenePlan` to a free ComfyStudio project
folder so a user can review/trim segments in a real multi-track
timeline GUI, then re-import the edited project for final render.

```bash
# After `ve assets` (or any workflow that produces <work>/plan/scene_plan.json)
ve export-comfystudio my-job \
    --project-dir ./outputs/my-job-comfystudio \
    --work-root ./projects --output-root ./outputs

# User opens ./outputs/my-job-comfystudio in ComfyStudio, edits, saves.

# Re-import the edited timeline back into the engine plan
ve import-comfystudio my-job \
    --project-dir ./outputs/my-job-comfystudio \
    --plan-out-name scene_plan_edited.json \
    --work-root ./projects --output-root ./outputs
```

`ve export-comfystudio` writes:

- `<project-dir>/project.comfystudio` — ComfyStudio project JSON.
- `<project-dir>/assets/{video,images,audio}/` — copied media referenced by the plan.
- `<project-dir>/engine_bridge.json` — human-readable bridge note.

It creates one video track (one clip per scene), one captions text
track (when scenes have non-empty narration), and one audio track
(when `--music-path` is provided). `ve import-comfystudio` reads the
edited `project.comfystudio`, matches clips to scenes by start order,
updates each scene's `duration_seconds` from the edited clip duration,
and writes the updated plan to `<work>/plan/<plan-out-name>`.

The same round-trip is available through the worker:

```bash
ve enqueue export_comfystudio -j my-job \
    --payload '{"project_dir": "./outputs/my-job-comfystudio",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1

# ... user edits in ComfyStudio ...

ve enqueue import_comfystudio -j my-job \
    --payload '{"project_dir": "./outputs/my-job-comfystudio",
                "plan_out_name": "scene_plan_edited.json",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1
```

Limitations of the current importer:

- Scene count mismatches are logged, not fatal.
- Clips added in ComfyStudio without a matching original scene are ignored.
- Only clip duration changes are reconciled; asset swaps and deep transform edits are not yet reflected back in the engine plan.

### FableCut timeline bridge

The engine can also export a `ScenePlan` to a FableCut project
folder. FableCut is a lightweight browser-based timeline editor
with a single `project.json` file, making it easy for a user to
review and trim segments, then re-import the edited timeline.

A bundled copy lives in `tools/FableCut`. The helper scripts
`scripts/open_in_fablecut.py` and `scripts/finalize_fablecut.py`
automate the open → edit → import handoff.

```bash
# After `ve assets` (or any workflow that produces <work>/plan/scene_plan.json)
ve export-fablecut my-job \
    --project-dir ./outputs/my-job-fablecut \
    --work-root ./projects --output-root ./outputs

# Optional: map Devanagari lyrics as bottom pop-up captions per scene
ve export-fablecut my-job \
    --project-dir ./outputs/my-job-fablecut \
    --lyrics ./videos/2026-07-12/suryastakam/comfystudio/lyrics.txt \
    --work-root ./projects --output-root ./outputs

# Open the bundled editor and sync this project into it (starts server + browser)
python scripts/open_in_fablecut.py ./outputs/my-job-fablecut

# ... edit in the browser at http://localhost:7777 and save ...

# Copy the edited project back and import it automatically
python scripts/finalize_fablecut.py
```

`ve export-fablecut` writes:

- `<project-dir>/project.json` — FableCut project document.
- `<project-dir>/media/` — copied media referenced by the plan.

It creates one video track clip per scene on `V1`, adds Ken Burns
keyframes for still images, mutes video clips so only the supplied
music is heard, places caption clips on `V2` (one per scene, either
from the scene's narration or from `--lyrics`), places scene
audio on `A1`, and adds a background-music clip on `A3` (when
`--music-path` is provided). `ve import-fablecut` reads the edited
`project.json`,
matches visual clips to scenes by start order, updates each scene's
`duration_seconds` from the edited clip duration, and writes the
updated plan to `<work>/plan/<plan-out-name>`.

The same round-trip is available through the worker:

```bash
ve enqueue export_fablecut -j my-job \
    --payload '{"project_dir": "./outputs/my-job-fablecut",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1

# ... user edits in FableCut ...

ve enqueue import_fablecut -j my-job \
    --payload '{"project_dir": "./outputs/my-job-fablecut",
                "plan_out_name": "scene_plan_edited.json",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1
```

Limitations of the current importer:

- Scene count mismatches are logged, not fatal.
- Clips added in FableCut without a matching original scene are ignored.
- Only clip duration changes are reconciled; asset swaps, keyframe changes, and deep transform edits are not yet reflected back in the engine plan.

For a foolproof render that reflects **every** FableCut edit—caption timing and position, image transforms/keyframes, clip speed, and audio volume—render directly from the edited `project.json`:

```bash
ve render-fablecut my-job \
    --project-dir ./outputs/my-job-fablecut \
    --output-name final.mp4 \
    --work-root ./projects --output-root ./outputs
```

This writes the final MP4 to `<output-root>/my-job/final.mp4` and records the `export` stage in the job manifest. The renderer reads `project.json` and `media/` from the project directory, so every browser edit survives into the deliverable.

You can also queue it through the worker:

```bash
ve enqueue render_fablecut -j my-job \
    --payload '{"project_dir": "./outputs/my-job-fablecut",
                "output_name": "final.mp4",
                "work_root": "./projects", "output_root": "./outputs"}'
ve worker --max-jobs 1
```

`scripts/render_fablecut_project.py` remains as a thin standalone wrapper around the same bridge renderer for ad-hoc use.

This bypasses the engine plan entirely and treats the browser timeline as the single source of truth. Use it for final output from FableCut; use `ve import-fablecut` only when you need to bring trimmed durations back into the engine for further automated processing.

## v1 raw-footage pipeline (removed)

The legacy directory-input pipeline (`ve ingest` / `ve transcribe` /
`ve analyze` / `ve plan` / `ve cut` / `ve preview` / `ve qa`) and its
`RawFootagePipeline` have been **removed**. Use the skills-first `ve run`:

```bash
ve run ./raw/clip.mp4 --job-id my-job
```

A directory source is accepted and runs the skills pipeline on its first
supported clip. See `ve run --help`.

## Commands & the JSON control channel

Every agent-drivable engine capability is a **typed command** (id + pydantic
params + handler). List them with:

```bash
ve commands            # id, category, summary
ve commands --json     # machine-readable catalog with param schemas
```

Run a control channel (one JSON object per line, in and out) so external tools
or agents can drive the engine:

```bash
ve control --port 9876            # binds 127.0.0.1 by default; --host to change
```

```jsonc
{"method": "engine.ping"}                                            // {"ok": true, "result": "pong"}
{"method": "engine.commands"}                                        // {"ok": true, "result": [...]}
{"method": "engine.execute", "params": {"command": "ticks.info", "params": {"seconds": 1.5, "fps": 23.976}}}
```

Built-in commands today: `probe.media`, `templates.list`, `pricing.cheapest`,
`ticks.info`, plus the pipeline commands `project.status`, `qa.run`,
`assets.resolve`, `render.run`. The same registry backs the MCP `run_command` /
`list_commands` tools. See `docs/ARCHITECTURE.md` and `docs/JEV_CLASSIFIER.md`.

## PoYo catalog check

`ve poyo-catalog` compares the active PoYo model ids (`config.yaml` defaults and
`models.yaml` price rows) with the live PoYo catalog. It is a development/ops
check, not part of a render:

```bash
ve poyo-catalog                 # readable table
ve poyo-catalog --json          # machine-readable
ve poyo-catalog --check         # exit 1 if an active config model is missing
```

It uses `poyo` when installed, otherwise `npx -y @poyoapi/cli`; the CLI is
pinned in `package.json` for local installs. `models.yaml` rows that are no
longer in the catalog are warnings, because old jobs and provider aliases may
still need them. Active `config.yaml` defaults are errors.

## External editor round-trip (OpenTimelineIO)

Exchange a job's timeline with any OTIO-capable NLE (FilmCraft, DaVinci Resolve,
Premiere via an adapter):

```bash
ve export-otio my-job --project-dir ./outputs/my-job-otio   # writes project.otio + media
ve export-otio my-job --otio-path ./handoff.otio            # (optional) extra copy

ve import-otio my-job --otio-path ./edited.otio             # durations -> updated plan
```

`ve export-otio` writes an OTIO timeline with **absolute** media references so
it opens anywhere; `ve import-otio` reconciles clip durations onto the original
scene order and writes `<work>/plan/<plan-out-name>`. See
`docs/WORKFLOWS.md` and `../tool-lab/trials/2026-10-17-filmcraft/README.md`.
