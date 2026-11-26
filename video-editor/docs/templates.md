# Video Templates

The engine now routes every incoming request through a **template** layer. A
_template_ is a declarative recipe that maps a set of user inputs to a supported
pipeline, preset, aspect ratio, and default providers.

Templates are **not renderers** — they are validators + configuration builders.
The actual render is still performed by the existing pipelines (v1 raw footage,
v2 skills, or the script-to-video flow).

## Quick start

```bash
# Let the router infer the template from a source file
ve run talk.mp4 --job-id talk

# Override the inferred template
ve run script.md --template social_hook --job-id reel

# List supported templates
ve list-templates
```

## Supported templates

| # | Template | Pipeline | Default preset | Default aspect | Best for |
|---|----------|----------|----------------|----------------|----------|
| 1 | `podium_cut` | v2 skills | `long_form` | 16:9 | Talking-head polish |
| 2 | `explainer_reel` | script_to_video | `long_form` | 16:9 | Markdown → full video |
| 3 | `image_story` | script_to_video | `long_form` | 16:9 | Ken Burns montage |
| 4 | `voice_canvas` | script_to_video | `long_form` | 16:9 | Voiceover + b-roll |
| 5 | `product_spot` | script_to_video | `short_explainer` | 9:16 | Product showcase |
| 6 | `social_hook` | script_to_video | `short_explainer` | 9:16 | Short vertical clip |
| 7 | `training_module` | script_to_video | `long_form` | 16:9 | Tutorials with diagrams |
| 8 | `brand_promo` | script_to_video | `long_form` | 16:9 | Locked brand style |
| 9 | `multi_language_dub` | v2 skills | `long_form` | 16:9 | Re-voice an existing video |
| 10 | `raw_batch` | v1 raw | `long_form` | 16:9 | Folder of raw clips |
| 11 | `musical_video` | music_video | `long_form` | 16:9 | Music-driven visuals |
| 12 | `storyteller_budget` | storyteller | `long_form` | 16:9 | Multi-voice story videos (budget tier) |
| 13 | `storyteller_hd` | storyteller | `long_form` | 16:9 | Multi-voice story videos (standard tier) |
| 14 | `storyteller_premium` | storyteller | `long_form` | 16:9 | Multi-voice story videos (cinematic tier) |
| 15 | `explainer_stills` | explainer_stills | `short_explainer` | 9:16 | script.md → stills + typographic scenes |

## Template details

### 1. `podium_cut`

Polish a single talking-head video with auto-cuts, captions, brand overlay, and
background music.

- **Required input:** one video file (`*.mp4`, `*.mov`, `*.mkv`, `*.avi`, `*.webm`, `*.m4v`)
- **Optional inputs:** word-level transcript JSON, brand file
- **Default providers:** none (uses config defaults)
- **Caveats:** only one source video is processed; multi-speaker / multi-angle
  switching is not supported.

### 2. `explainer_reel`

Turn a markdown script into a fully generated explainer video.

- **Required input:** markdown script (`*.md`, `*.txt`, `*.markdown`)
- **Default providers:** ElevenLabs TTS, FAL image, HyperFrames video,
  ElevenLabs music + SFX
- **Default music mood:** `upbeat`
- **Caveats:** visuals are generated from prompts; exact scene-by-scene control
  requires editing the generated `scene_plan.json`.

### 3. `image_story`

Generate a Ken Burns montage from a script that describes each scene.

- **Required input:** markdown describing each scene/image + narration
- **Optional input:** reference images (copy them into the work tree and
  reference them manually)
- **Default providers:** ElevenLabs TTS, FAL image, ElevenLabs music
- **Default music mood:** `cinematic`
- **Caveats:** there is no direct image-upload provider; images are either
  described/regenerated, or manually referenced.

### 4. `voice_canvas`

Lay generated b-roll under a pre-recorded voiceover audio file.

- **Required inputs:** pre-recorded voiceover (`*.mp3`, `*.wav`, etc.), markdown
  script matching the voiceover
- **Default providers:** FAL image, HyperFrames video, ElevenLabs music + SFX
- **Default music mood:** `ambient`
- **Caveats:** the supplied audio becomes the scene voiceover; TTS is skipped.
  Scene durations should match the voiceover segments.

### 5. `product_spot`

Generate a vertical product showcase from a product description.

- **Required input:** markdown with product, benefit, audience, and script
- **Default providers:** ElevenLabs TTS, FAL image, HyperFrames video,
  ElevenLabs music + SFX
- **Default music mood:** `upbeat`
- **Default aspect:** `9:16`
- **Caveats:** no real product footage is captured; visuals are AI-generated
  from the description. Use `image_story` if you have actual product photos.

### 6. `social_hook`

Create a fast, vertical social clip from a short hook script.

- **Required input:** 15–60 second hook script in markdown
- **Default providers:** ElevenLabs TTS, FAL image, HyperFrames video,
  ElevenLabs music + SFX
- **Default music mood:** `energetic`
- **Default aspect:** `9:16`
- **Caveats:** best for 9:16. 16:9 is possible by overriding `aspect_ratio`.

### 7. `training_module`

Turn a step-by-step markdown guide into a tutorial with diagrams.

- **Required input:** numbered steps with narration + optional chart data
- **Default providers:** ElevenLabs TTS, FAL image, Matplotlib data_viz,
  ElevenLabs music
- **Default music mood:** `calm`
- **Caveats:** diagrams use `asset_query: data:chart_type=...;...` syntax.
  Screen recordings must be captured separately and are not generated.

### 8. `brand_promo`

Render a script with locked-in brand colors, fonts, and logo overlay.

- **Required input:** markdown script
- **Optional/recommended input:** brand file (`presets/brand.json` or
  `presets/brands/<job>.json`)
- **Default providers:** ElevenLabs TTS, FAL image, ElevenLabs music
- **Default music mood:** `professional`
- **Caveats:** brand compliance depends on `presets/brand.json` being present
  and correctly formatted.

### 9. `multi_language_dub`

Re-voice an existing video in a new language with captions.

- **Required inputs:** original video file, translated markdown script
- **Default providers:** ElevenLabs TTS
- **Caveats:** the current v2 pipeline replaces the full audio track via TTS,
  not lip-sync. For precise dubbing, supply a forced-alignment transcript in
  the target language.

### 10. `raw_batch`

Run the v1 raw-footage pipeline on a folder of clips.

- **Required input:** directory containing `mp4`/`mov` clips
- **Optional input:** transcript for the first clip
- **Caveats:** v1 transcribes, analyzes, and cuts only the first discovered
  clip. Clips 2..N are discovered but ignored. Concatenate clips first if you
  need them all.

### 11. `musical_video`

Cut user images and short video clips to a music track. Still images are
animated with a gentle Ken Burns pan/zoom; video clips play in-line. Optional
lyrics can be aligned to the music and burned as captions.

- **Required input:** music/audio file (`*.mp3`, `*.wav`, etc.)
- **Required input:** `--media-dir` folder containing `jpg`/`png`/`mp4`/`mov`/...
- **Optional input:** `--lyrics` text file + `--language` code (default `en`)
- **Default provider:** `local_media` (copies user files into the asset cache)
- **Caveats:** user media is cycled in directory order; no AI generation is
  performed. Videos longer than a segment are trimmed; shorter videos freeze
  on their last frame. Beat detection is not yet implemented; use
  `--segment-seconds` to control cut timing. The composition renderer
  (HyperFrames/FFmpeg) can emit a silent AAC track in headless/music-driven
  mode; if the built-in `ve run` output has no audio, use the direct FFmpeg
  composer script `scripts/compose_musical_video.py` as a deterministic fallback.

### 12. `storyteller_budget`

Lowest-cost storyteller preset: budget image model (`flux-schnell`) and budget
video model (`wan2.2-image-to-video-fast`, gentle parallax). The LLM picks ~50% of scenes for
motion automatically. Use this tier for prototyping multi-voice stories, bulk
drafts, and internal reviews.

- **Required input:** story markdown (`*.md`) with YAML front-matter
  (`cast`, `language`, `music_mood`) and `## Speaker:` sections
- **Default providers:** `tts_router` (Sarvam for Indian languages, ElevenLabs
  otherwise), PoYo image, PoYo video, `poyo_music`, ElevenLabs SFX
- **Default tier pair:** `budget` image + `budget` video
- **Caveats:** budget motion (`wan2.2-image-to-video-fast`) adds gentle parallax to a still
  image, not action shots. Override tiers per-run with
  `--image-tier` / `--video-tier` (e.g. `--video-tier premium`).

### 13. `storyteller_hd`

Standard-quality storyteller preset: `gpt-image-2` 1K low for stills and
`seedance-2-fast` 720p for motion. The user marks `> motion: true` per scene
to make a scene animate; otherwise the scene renders as a still with Ken Burns.

- **Required input:** story markdown (`*.md`) with YAML front-matter
  (`cast`, `language`, `music_mood`) and `## Speaker:` sections
- **Default providers:** `tts_router` (Sarvam for Indian languages, ElevenLabs
  otherwise), PoYo image, PoYo video, `poyo_music`, ElevenLabs SFX
- **Default tier pair:** `standard` image + `standard` video
- **Caveats:** multi-voice multi-language is supported via per-speaker
  `voice_id` + `language`; explicit prefixes (`sarvam:`/`elevenlabs:`) win
  over the language routing rule. Uses the FableCut bridge for timeline
  preview — after browser edits, run `ve render-fablecut <job>` for the final
  cut. Override tiers per-run with `--image-tier` / `--video-tier`.

### 14. `storyteller_premium`

Cinematic storyteller preset: `gpt-image-2` 2K for stills and `seedance-2`
1080p for motion. Highest cost; best for hero reels, brand films, and
final-cut output. Same script format and provider routing as `storyteller_hd`
— only the tier pair changes.

- **Required input:** story markdown (`*.md`) with YAML front-matter
  (`cast`, `language`, `music_mood`) and `## Speaker:` sections
- **Default providers:** `tts_router` (Sarvam for Indian languages, ElevenLabs
  otherwise), PoYo image, PoYo video, `poyo_music`, ElevenLabs SFX
- **Default tier pair:** `premium` image + `premium` video
- **Caveats:** every motion scene uses `seedance-2` at 1080p (~10× the cost
  of budget). Do not use for prototyping. Override tiers per-run to drop cost
  temporarily (e.g. `--image-tier standard`).

### 15. `explainer_stills`

One `script.md` becomes a finished vertical explainer: generated stills,
flat typographic scenes, geographic map scenes, a single narration track,
karaoke captions and ducked music. This is the reusable form of the
`saju-zodiac-animal` build.

- **Required input:** `script.md` with YAML front-matter (`title`,
  `aspect_ratio`, `voice`, `design`, `reuse_threshold`, `target_seconds`,
  `music_mood`, `pronunciations`) and one `## ` block per scene.
  `pronunciations:` maps a display word to how the voice should say it
  (`Kutch: Kuchh`); only the TTS request reads the spoken form, while captions
  and cues keep the display word. Whole words only, so `Kutchi` is untouched.
- **Typographic motion:** a `big_stat` headline with a number counts up to it
  (`count: false` turns it off), a `definition` gloss reveals word by word, and
  both draw a thin accent rule that grows over the whole scene.
- **End screen:** a `big_stat` takes `align: top|center` (default `center`). `top` puts the
  headline (one line, 100 px at 1080p) and sub in the top 42% of the frame, left-aligned, so the
  lower part stays clear for YouTube end-screen cards. Any other value fails the script.
- **`cue: false` means "from the first frame".** On any chart part, step, dot or bubble it shows
  the element before anything is said. Give each element a word from the narration instead
  (a waterfall's last steps and `end`, a line chart's dots).
- **Icons:** a `big_stat` takes an optional `icon: <name>`, drawn about 140 px
  above the headline in the design's accent and popping in with the headline's
  cue; a `bar_compare` bar takes the same key (`{label, value, unit, icon}`)
  and shows a small icon before its label. Icons are the vendored Lucide set
  (ISC, `packages/core/src/video_editor_core/icons/lucide/`, licence alongside):
  `wheat`, `flame`, `store`, `users`, `package`, `zap`, `file-badge`, `clock`.
  An unknown name fails the scene and lists the available ones. To add an icon,
  drop its Lucide SVG into that folder.

  ```yaml
  layout: big_stat
  headline: "₹24"
  icon: flame
  ```
- **`layout: waterfall`:** a running total, left to right. `start: {label, value}`,
  `steps: [{label, value, cue?}]` (2-8, signed: a negative step hangs down from
  the running level) and `end: {label, value?}`, whose bar is start + sum(steps).
  Optional `unit` (`₹`, `$`, `€`, `£` go before the number, anything else
  after). Step bars grow when their label is spoken (default cue is the label;
  `cue: false` opts out) with a thin connector between levels; start/end bars
  use the accent, positive steps the muted colour and negative steps a fixed
  red (`#C0392B`, the palettes have no negative colour). Values use Indian
  grouping (`₹1,52,000`) with `+`/`−` on steps. If `end.value` is given it must
  equal start + steps to the shown decimals or the script fails to parse.

  ```yaml
  visual: typographic
  layout: waterfall
  unit: ₹
  start: {label: అమ్మకాలు, value: 152000}
  steps:
    - {label: అద్దె, value: -40000}
    - {label: జీతాలు, value: -52000}
    - {label: టిప్స్, value: 5000}
  end: {label: లాభం, value: 65000}   # optional; checked
  ```
- **`layout: line`:** a line on labelled axes, either `slope` + `intercept`
  (needs `x_range: [lo, hi]`, or a `mark`/`dots` to derive one) or
  `points: [[x, y], ...]` (3-30, strictly increasing x). Optional `x_label`,
  `y_label`, `x_range`, `y_range` (default: the data padded, always including
  0) and `unit` (applied to y values, Indian grouping). A baseline rule is drawn
  at y = 0 when 0 is inside the range, and the line draws on at scene start with
  a CSS dash reveal. `mark: {x, label, cue?}` draws a vertical dashed marker
  with its label and x value. `dots: [{x, label, cue?}]` (1-3): the first dot
  appears at its x on the line; each following dot appears at the previous
  dot's x and slides along the line (through the polyline's vertices) to its
  own x on its cue, and the previous dot hands over and hides. Each dot
  label shows the line's value there. Cues default to the labels, as in
  `bar_compare`; `cue: false` opts out. Marks and dots must lie inside the x
  range. Everything is seekable CSS (keyframes on `left`/`top`), no JavaScript.

  ```yaml
  visual: typographic
  layout: line
  slope: 60
  intercept: -9120
  x_range: [0, 250]
  unit: ₹
  x_label: రోజుకు టిఫిన్లు
  y_label: లాభం
  mark: {x: 152, label: బ్రేక్-ఈవెన్}
  dots:
    - {x: 90, label: నేడు}
    - {x: 152, label: సమం}
    - {x: 220, label: లక్ష్యం}
  ```
- **`layout: donut`:** a ring split into `parts: [{label, value, cue?}]` (2-6,
  positive values) with optional `unit` and `center: {value, label, cue?}`.
  Each segment sweeps in clockwise from 12 o'clock on its cue, in order
  (default cue is the label, `cue: false` opts out), and its legend row (label
  and Indian-grouped value) appears with it. The centre value counts with the
  slices: at each part's reveal it steps (0.4 s) from the running sum so far to the new
  one, ending on `center.value` (₹3.00 → ₹5.40 → ₹7.02 → ₹13.02); it may also be label-only, and an explicit `center.value` must equal
  the sum of parts to the shown decimals or the script fails to parse.
  Segment colours are the design's accent, ink and muted, then a tint of each.

  ```yaml
  visual: typographic
  layout: donut
  unit: ₹
  parts:
    - {label: అద్దె, value: 40000}
    - {label: జీతాలు, value: 52000}
    - {label: గ్యాస్, value: 8000}
  center: {value: 100000, label: ఖర్చు}
  ```
- **Cuts:** after a short pause (under 0.5 s) the cut goes in the silence and
  leaves at least 0.15 s after the last word; the `sentence_cuts` QA check fails
  a cut that clips a word.
- **Scene kinds:** `visual: still` (needs `image:`; optional `eyebrow`,
  `headline`, `body`, `subtitle` turn it into a designed plate — see below),
  `visual: typographic`
  (needs `layout:` — `big_stat`, `bar_compare`, `definition`, `sequence`,
  `array`, `waterfall`, `line`, `donut`), `visual: map` (needs `country:`; optional `focus: "lon,lat"` and
  `label` for one highlighted point, `bbox` (the map fits it to the plate minus the title
  block, centred, with a 4% margin; nothing outside it shows, so widen it to keep a river or
  neighbour in view), `projection`, `eyebrow`,
  `eyebrow_color`, `headline`, `subtitle`, `badge`, `badge_sub`, `badge_mark`,
  `watermark`,
  `callouts` — a short string or `{text, cue}` — `markers`, `route` and
  `zooms`) and
  `visual: composite` (needs `layout:` — see below). Map geometry is bundled
  Natural Earth 110m, so a map scene costs no image generation and renders
  offline. `markers` names places on the map: 1–6 `{text, lon, lat, cue?}`
  maps, revealed in order, each defaulting to cue on its own name. Leave out
  `lon`/`lat` and the place is looked up in the bundled gazetteer (Natural Earth
  1:50m populated places, 1,251 cities): `{text: Chennai}`, or
  `{text: Calcutta, place: Kolkata}`, with `country:` to disambiguate (the scene's
  own country is tried first, so Hyderabad on an India map is the Indian one).
  A marker within 15 km offshore snaps onto the coast; `sea: true` marks a sea
  label that stays put. The `map_markers` QA gate fails any other marker still
  in the sea. `worldview: ind` draws disputed borders as India claims them
  (Natural Earth's India point-of-view edition, for India and its neighbours);
  it is on by default when `country` is India, and `worldview: none` turns it
  off. `route` is
  at least two `[lon, lat]` points drawn as a line between them, with an
  optional `route_cue`; the draw is a CSS dash reveal, so it seeks
  deterministically. Both must fall inside the map window.
  `route_curve: great_circle` bows long routes along the great circle and
  `route_dot: true` adds a dot that leads the line as it draws.
  `rivers` draws real river courses from bundled Natural Earth 1:50m data
  (449 named rivers worldwide): `{name, cue?, label?}`, where `label` is `true`
  for the data name or a string to show instead (`{name: Ganges, label: Ganga}`).
  Each course is joined from its segments and drawn source → mouth, with a light
  core over a dark casing and a flow of dashes moving toward the mouth
  afterwards (`flow: false` turns it off). `regions` fills named geography
  regions (261 worldwide: mountain ranges, deserts, plateaus, plains, deltas,
  basins): `{name, cue?, label?, style?}`, patterned by kind (soft relief hills
  for ranges, `style: range` for the older ridge marks, stipple for deserts, hatch for plateaus, tints for plains and deltas)
  after an outline draw-on. Names are case-insensitive, with aliases such as
  Amazon → Amazonas. `stutter: true` (or a number, default 12) steps the
  overlay reveals and draws at that rate, snapped to whole frames, while the
  camera stays smooth: the Vox-style hand-built cadence. Every label (markers,
  rivers, regions) is laid out together, so labels flip sides or nudge to avoid
  each other. A region label sits on the visible part of its shape and fades
  out when the camera leaves it. A region label is centred on the region when it fits
  inside; otherwise it becomes a callout, placed in open space with a thin
  leader line to a dot on the region. `callout: true` forces a callout for a
  marker or river, and `callout: false` keeps a plain side label. `zooms` pushes the
  camera in to tighter windows between regions: a list of
  `{bbox, cue?}` or `{to, cue?, pad?}` entries, applied in order. `bbox` is
  `"west,south,east,north"` (or a four-item list). `to` is a name or list of
  names resolved to a scene marker, region, river or country; their union is
  padded (`pad`, default 0.18 of each span, at least 0.6° each side) and fitted
  to the map panel's aspect ratio, so a portrait panel fills with the subject
  rather than empty margins. Exactly one of `bbox` and `to` is required.
  Without a `cue` a zoom takes the bounded stagger, so
  a scene can move on a rhythm; `cue: false` holds the opening window. The
  map artwork sits in one camera element and the pins and place names in an
  unscaled overlay beside it: a move is a uniform scale plus translation
  computed from the two windows and clipped by the fixed map window, while the
  overlay animates its own positions on the same keyframes so a place name
  keeps its reading size. That is CSS only, no JS, deterministic on seek. A
  move that cannot land and finish before the cut is dropped rather than held
  halfway.
  `eyebrow` is
  display copy; its colour is the separate `eyebrow_color` prop, defaulting to
  the design's gold (reading the text itself as a colour emitted an invalid
  declaration that dropped the rest of the stylesheet). Map plates are
  authored twice: a 9:16 frame stacks the title above the map and the callout
  cards below it; a landscape frame keeps the four-column plate. Any other
  aspect contains the matching plate with one uniform scale and letterboxes
  the remainder in paper.
- **Stock footage, tallies, calculations (2026-11-12):** `visual: footage`
  (an English `query` for a vision-checked stock clip plus an AI `image`
  fallback), a running `tally` chip, checked `calc` cards, `layout: ledger`,
  `narration_mode: per_scene` and `ai_label`. See
  [`STOCK_EXPLAINERS.md`](STOCK_EXPLAINERS.md).
- **Map stories (2026-11-12):** map scenes add `frame: full`, the `night_map`
  design, `journey` (multi-stop legs, km counter, travelling icon, `follow`,
  `along: <river>`, `shape: heart`), `state:`/`district:` regions (`fill`,
  `photo` with `image_prompt`/`image_query`), `bubbles`, `stickers` and
  `min_span` zooms. See [`MAP_STORIES.md`](MAP_STORIES.md).
- **Designed stills:** a `visual: still` scene with any of `eyebrow`,
  `headline`, `body` or `subtitle` is compiled as the `over_media` composite:
  the full-bleed still under a scrim with a title block. Without display copy
  it stays a bare Ken Burns still, which is right for an atmosphere shot but
  leaves an idea unread. The headline defaults to a cue on its own words, so
  opt out with `headline_cue: false` when the narration never says it. The
  same rules apply to a `narration` scene drafted by `--from-topic`: title
  what matters, leave the rest bare.
- **Composite layouts:** the designed plate an essay explainer uses most.
  Every composite scene may also carry `eyebrow` (display copy), its colour
  `eyebrow_color`, `watermark`, `highlight` (a colour override) and cue words.
  - `layout: split` — a mounted still beside a text column. Needs `image:`.
    Text props: `headline`, `body`, `stat: {value, label}`, `facts: [text |
    {text, cue}]`, `credit`; `side: right` puts the still on the right.
    `stat` also takes `cue`.
  - `layout: over_media` — a full-bleed still under a scrim with a title
    block. Needs `image:`. Text props: `headline`, `body`, `subtitle`, and an
    optional `badge` / `badge_sub` / `badge_mark`.
  - `layout: compare` — two stills side by side. Needs both `image:` and
    `image_b:` (a second paid IMAGE request) and `labels: [left, right]`;
    optional `headline` and `caption`.
  - `layout: quote` — a pull quote on the paper plate. Needs `quote:`;
    optional `attribution`, `attribution_cue`, `role`. No still.
  - `layout: section` — a chapter divider with a large `number:` (default
    `01`). Needs `headline:`; optional `subtitle`. No still.
  Composite plates are authored twice: a 16:9 plate and a 9:16 plate. A canvas
  at least as tall as it is wide takes the portrait plate, so `split` stacks
  the still above the text column (`side: right` puts it below), `compare`
  stacks its two stills with a label under each, and `over_media` fills the
  frame. Any other aspect (4:5, 1:1) contains the matching plate with one
  uniform scale and letterboxes the remainder in paper, never stretched. A
  `compare` scene counts as two renders in the reuse report, and its two
  stills attach to the scene in request order (own prompt first, `image_b`
  second).
- **Cues:** an item's default cue is its own display text (a headline, a stat
  value, a fact, a label, a callout, a map marker, a definition term, a
  sequence step or a bar label), so the plate reveals it when the voice says
  those words. When the text is display copy the narration never speaks, opt
  out with `cue: false` on that item, or `headline_cue: false` /
  `term_cue: false` for the scene-level headline or definition term; a route
  waits for its `route_cue`. A cue left on that the scene never says fails the
  cue gate at QA.
- **Default providers:** `tts_router`, PoYo image (`nano-banana-2-lite`),
  `poyo_music`
- **Caveats:** **`poyo_music` is currently stale against the live PoYo API**
  (it submits a retired model id, which now returns 404). Until the provider
  is updated, pass `--music-path <file>` with a local music bed: a run without
  it pays for narration and stills, then fails before rendering. For a free
  offline dry run, `--tts-provider kitten_tts --image-provider stub_image`
  spends nothing (install the local voice with `uv pip install kittentts`);
  the stills are test gradients, so use it to exercise plates and timing, not
  to judge the look. Scene
  durations are derived from the narration, never authored. Still scenes that
  reprise a subject render once; the reuse table is printed before the
  estimate gate and written to `projects/<job>/plan/reuse_report.json`.
  Typographic, map and composite scenes use bundled fonts only (`Inter`,
  `Montserrat`, `Cormorant Garamond`).
- **Command:** `ve explain script.md --job-id <job> --music-path <file>` (or
  `ve explain --from-topic "..." --job-id <job> --music-path <file>` to draft
  the script first; `--from-topic` asks the planner for the full grammar, so
  the draft can contain still, typographic, map and composite scenes, and an
  unrenderable plate fails before the estimate gate). `--job-id` is required.

## How inference works

The `TemplateRouter` picks a template from the source path unless `--template`
is given:

| Source type | Picked template |
|-------------|-----------------|
| Directory | `raw_batch` |
| Video file | `podium_cut` |
| Audio file + `--media-dir` | `musical_video` |
| Audio file (alone) | `voice_canvas` |
| Markdown/text file | `explainer_reel` |

If the file extension is unknown, the router errors and asks for `--template`.

## Overrides

You can tweak a template without abandoning it:

```bash
ve run script.md --template social_hook \
       --job-id reel \
       --work-root ./projects --output-root ./outputs \
       --preset long_form \
       --music-mood calm
```

Supported override fields:

- `preset`
- `aspect_ratio` (selected by the template; pick a different template to change it)
- `providers` per asset type (`tts`, `image`, `video`, `music`, `sfx`, `data_viz`, `alignment`, `isolation`, `speech_to_speech`) via `config.yaml` or `ve assets --*-provider`. Valid visual providers include `poyo_ai`, `fal_ai`, `hyperframes_video`, `pexels`, and the `stub_*` variants.
- `music_mood`

## Adding a new template

1. Add a `Template(...)` instance to
   `apps/engine/src/video_editor_engine/templates/eleven_templates.py`.
2. Provide required inputs, defaults, providers, and caveats.
3. Optionally add a `build_concept` helper that returns a starter markdown
   scaffold.
4. Add router tests in `tests/test_templates.py`.
5. Update this document.

## Relationship to other docs

- For **detailed capabilities, variations, and examples** per template see
  `docs/Templates_capabilities.md`.
- For supported CLI commands see `docs/CLI_USAGE.md`.
- For pipeline internals see `docs/ARCHITECTURE.md` (the detailed historical
  walkthrough is archived at `docs/archive/architecture-explained.md`).
- For requests the engine cannot handle see `docs/not_possible.md`.
