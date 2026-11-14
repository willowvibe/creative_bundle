# HyperFrames Composition Architecture — Design

**Date:** 2026-10-23
**Status:** Approved for planning (umbrella; slice 1 specified in §7)
**Driver:** `~/Downloads/Cosmic Saju — First 5 Shorts.md` (five 25–45 s vertical shorts)

## 1. Problem

The series brief asks for a **reusable motion vocabulary** — Drift, Stamp, Card
flip, Draw, Kinetic caption — under one hard rule: images carry only mood, and
every word, Hanja character and chart is a HyperFrames layer.

The engine cannot render that vocabulary today. Its scene compiler supports
three visual modes: an image with one of four hardcoded Ken Burns moves, a
playing video, or one of five fixed typographic layouts
(`typographic_scenes.py:264`). There is no stamp, flip, draw, lock, counter or
per-scene layer stack.

The consequence is already visible in the repo. Each Cosmic Saju short is a
**code fork**: `videos/2026-10-23/saju-january-animal/build/` holds eight
Python scripts (~1,800 lines) copied from the previous short, including a
295-line `compose.py` that hand-writes the entire HTML composition. Its README
states the copy relationship explicitly. The motion vocabulary therefore lives
in per-video Python, and the sixth short will fork it again.

Three compilers now exist for the same job:

| Compiler | Entry point | Input |
|---|---|---|
| `hyperframes_project.py` | `compile_hyperframes_project` (`:43`) | `RenderGraph` overlays |
| `scene_plan_composition.py` | `compile_scene_plan_project` (`:68`) | scene-plan dict |
| per-project `compose.py` | bespoke, one per video | hand-written |

Adding a motion today means editing all three, or forking a fourth.

## 2. Evidence — brief vs engine

### 2.1 Motion vocabulary

| Brief primitive | Engine today | Needed |
|---|---|---|
| Drift | exists, but as 4 fixed moves in `scene_plan_composition.py:49` | parameterised primitive |
| Stamp | absent | scale + ink-bleed primitive |
| Card flip | absent | rotateY primitive |
| Draw | absent | SVG stroke primitive |
| Kinetic caption | karaoke only, in `compile_hyperframes` spans | general per-word primitive |
| Lock (two Hanja halves) | absent | opposing-slide primitive |
| Counter ("1 of 11") | absent | CSS-only digit strip |

### 2.2 Gaps found while auditing (independent of the brief)

| # | Gap | Where | Why it matters |
|---|---|---|---|
| G1 | No CJK font is bundled — Hangul/Hanja render in a **system** stack | `fonts.py:18` (`BUNDLED_FONTS` = Inter, Montserrat); `saju-january-animal/build/compose.py:43` | The brief's top risk is a wrong Hanja. On a machine without Noto CJK, glyphs silently fall back. |
| G2 | Safe areas are looser than the format requires: 8% top / 20% bottom vs the brief's 220 px / 480 px | `safe_area.py:37` | Chips at `top:300px` and pills at `560 + 92·i` are placed by hand with no cap. |
| G3 | Cue matching fails silently — a cue that does not match the spoken words emits no pill and no error | documented in `saju-january-animal/build/README.md` ("five of them missed") | A visible element can vanish without failing a gate. |
| G4 | Only `render` and `lint` of the HyperFrames CLI are wrapped | `hyperframes_cli.py:38,85,128` | `check`, `snapshot`, `inspect`, `keyframes`, `timeline`, `audio` are unused; there is no visual QA of the composition itself. |
| G5 | Every project emits one monolithic `index.html` | `scene_plan_composition.py:262`, `hyperframes_project.py:281` | HyperFrames recommends a thin orchestrator + per-scene sub-compositions once a video has hard cuts or a reusable scene. |
| G6 | The narration → captions → mix → QA flow is re-implemented per project | the eight copied scripts | `ve explain` already owns this flow for other videos. |

## 3. Goals

- One intermediate representation (`Composition`) and **one compiler**, replacing
  both existing HyperFrames compilers.
- A named, deterministic **motion primitive library** that covers the brief's
  vocabulary and the existing look.
- Series identity expressed as **design tokens + content**, not Python.
- Deterministic gates for the three failure modes the brief calls out: wrong
  Hanja, text under platform UI, cue drift.

## 4. Non-goals

- No GSAP. The compiler computes absolute delays; CSS keyframes are keep-seekable
  and preserve the `data-no-timeline` speed fix (#239).
- No HyperFrames registry components. They are GSAP-timeline based
  (`window.__timelines` + `paused: true`), which reopens the runtime decision.
  Their visual language is a reference, not a dependency.
- No change to scene planning, asset providers, cost estimation or the TTS path.
- No modular sub-composition output in slice 1 (see §7, slice 4).

## 5. Architecture

### 5.1 The model — `packages/core/src/video_editor_core/composition.py`

```python
class Canvas(BaseModel):      width: int; height: int; fps: int
class LayerTime(BaseModel):   start: float; end: float | None
class MotionSpec(BaseModel):  name: str; params: dict[str, float | str]
                              delay: float = 0.0
                              duration: float | None = None
                              ease: str = "cubic-bezier(.2,.8,.2,1)"
class LayerContent(BaseModel):   # exactly one of these is meaningful per kind
    text: str = ""               # text
    spans: list[WordSpan] = []   # rich_text: {text, start, end, highlight}
    source: str = ""             # media
    gradient: str = ""           # shape / scrim
    path: str = ""               # svg
    alt: str = ""                # media accessibility text
class CompositionLayer(BaseModel):
    id: str
    kind: Literal["media", "text", "rich_text", "shape", "svg", "html"]
    preset: str = ""            # chip | pill | scrim | end_card | caption_line | spotlight
    rect: Rect                  # canvas pixels, never scene-relative
    time: LayerTime             # absolute timeline seconds
    content: LayerContent       # text, spans, source, gradient, path
    style: dict[str, Any]
    motions: list[MotionSpec] = []
class CompositionScene(BaseModel):
    id: str; start: float; end: float
    layers: list[CompositionLayer]      # bottom → top within the scene
    transition_in: MotionSpec | None = None
class Composition(BaseModel):
    job_id: str
    canvas: Canvas
    duration: float
    scenes: list[CompositionScene]
    audio: list[AudioCue]
    design: DesignTokens | None
    audio_mode: Literal["embed", "external"] = "embed"
```

Rules the model enforces:

- Every layer rect is inside the canvas; **safe-area conformance is a QA gate,
  not a constructor mutation** (`fit_box` stays opt-in).
- Every scene window is contiguous and starts at the previous scene's end.
- `audio_mode="embed"` means the compiler emits `<audio>` and HyperFrames mixes;
  `"external"` means the caller muxes (the render-graph path, for ducking and
  loudnorm). The renderer must never infer this.
- All times are quantised to the frame grid (`quantise(seconds, fps)`) so CSS
  times and HyperFrames frame quantisation agree.
- `html` carries engine-generated markup only (today: the five typographic
  layouts). It is never user text, so the compiler emits it unescaped. A layer
  of that kind may carry its own stylesheet in `style["extra_css"]`, which the
  compiler appends to the composition `<style>` verbatim.
- **Fields the emitter does not yet write fail closed (recorded 2026-10-26;**
  **`transition_in` emitted 2026-10-28).** `WordSpan.highlight` is declared so
  the model can express the intent, but the compiler does not read it: every
  word takes its colour from the layer's `highlight_color`. A non-default value
  is therefore rejected at construction (`"… not emitted until slice 2"`)
  rather than accepted and silently dropped. The `word_highlight` motion
  primitive is in the same position: it is a validation-only marker that emits
  no CSS. `CompositionScene.transition_in` is no longer in that group:
  `scene_transitions.apply_scene_transitions` emits it as per-layer `fade` and
  `fade_out` motions. It requires an explicit duration, is limited to
  `crossfade`/`fade`, and is cleared on the compiled copy so the pass stays
  idempotent.

`motions` is a **list** so `drift` + `stamp` compose on one element. Each
motion's `delay` is **clip-relative**, because HyperFrames seeks CSS animations
from the clip's own `data-start` (already relied on in
`hyperframes_project.py:180`). The compiler clamps
`delay + duration <= clip_duration`.

### 5.2 The compiler — `compile_composition.py`

`compile_composition(composition, project_dir) -> Path` writes a HyperFrames
project. It supersedes `compile_hyperframes_project` and
`compile_scene_plan_project`, whose public functions remain as thin adapters.

Output contract (unchanged from today, so existing tests keep their guarantees):

- one root `div[data-composition-id=main][data-no-timeline]` carrying
  canvas/duration/fps;
- **every visual layer is a root-level `class="clip"`** with `data-start`,
  `data-duration`, `data-track-index` and an explicit `z-index`. Media must be
  root children — HyperFrames does not seek nested media.
- keyframes are deduplicated by `sha1(name + params)[:8]`, so ten identical
  stagger cells emit one `@keyframes` block. Duration is not part of the key:
  it lives in the `animation` declaration, while the keyframes stay
  percentage-based.
- scenes are a compile-time grouping only. Nothing in the pipeline may depend on
  scene DOM nesting, so slice 4 can change the emitter without touching callers.

### 5.3 Adapters and migration

| Adapter | Reads | Supersedes |
|---|---|---|
| `adapters/from_render_graph.py` | `RenderGraph` | body of `hyperframes_project.py` |
| `adapters/from_scene_plan.py` | scene-plan dict | body of `scene_plan_composition.py` |
| `adapters/from_content_pack.py` (slice 2) | content pack YAML | per-project `build/` scripts |

Compatibility strategy: keep the old module paths and function signatures,
delete their HTML builders, and delegate to the Composition pipeline. `ffmpeg`
remains a fallback renderer through the unchanged `composition_router.py`.

### 5.4 Motion primitives — `motion/`

Each primitive is a pure function returning:

```python
@dataclass(frozen=True)
class MotionCSS:
    keyframes: str      # @keyframes blocks, name deduplicated
    declaration: str    # value for the CSS `animation` property
    props: str          # extra static properties (transform-origin, perspective, …)
    markup: str = ""    # extra markup the primitive needs (`count` digit strip)
```

The registry is a dict `name -> builder`; `MotionSpec.name` is resolved against
it at validation time, and an unknown name raises with the sorted registry.

| Primitive | Brief move | Deterministic implementation |
|---|---|---|
| `drift` | Drift | scale/pan keyframes; **must reproduce the four `_KB_MOVES` exactly** when called with their parameters |
| `stamp` | Stamp | `scale(1.2)→scale(1)` plus a blur that settles; `both` fill |
| `flip` | Card flip | `rotateY(90deg)→0` with `perspective` on the parent |
| `draw` | Draw | SVG `pathLength="1"` + `stroke-dasharray:1` + `dashoffset 1→0` — no path measurement |
| `lock` | Hanja pair | two elements slide from opposite sides and settle |
| `count` | counter | a masked digit column translated by keyframes (no JS); supplies `MotionCSS.markup` |
| `pulse`, `rise`, `pop`, `fade` | ambient/entry | shared |
| `pill` | cue pill | entry timed from `resolve_cues` output |
| `word_highlight` | kinetic caption | applied by the `rich_text` emitter; registered so validation accepts it |

Three primitive-contract notes:

- `scrim` is **not** a motion. A scrim is a `shape` layer with `preset="scrim"`
  and a gradient in its content.
- `draw` needs `pathLength="1"` on each `<path>`. That is an SVG attribute, not
  a CSS property, so the compiler sets it when a `draw` motion is present.
  `draw`'s `stroke-dasharray`/`stroke-dashoffset`/`fill` reach the path by
  inheritance from the layer element, so the compiler sets them there.
- A primitive that renders its own markup (`count`) or is a marker
  (`word_highlight`) returns an empty `declaration`, so there is nowhere to
  write a delay. The registry rejects a non-zero delay on those primitives
  rather than render the motion at the wrong time; callers move the clip
  instead.

`count` cannot change text content in CSS, so it renders a strip of digit glyphs
inside an `overflow:hidden` box and translates it. This keeps every primitive
seekable by `Animation.currentTime`.

### 5.5 Design tokens and series identity

`DesignTokens` (`designs.py`) gains an optional `layers` block:

```json
{
  "layers": {
    "safe_area": "vertical_short_strict",
    "scrim": {"top": {...}, "bottom": {...}},
    "chip":  {"font": "Noto Serif KR", "size": 84, "accent": "#F59E0B"},
    "pill":  {"bg": "rgba(38,17,74,0.82)", "border": "#F59E0B", "size": 42},
    "end_card": {"panel_bg": "rgba(8,5,20,0.74)", "radius": 30},
    "caption_preset": "karaoke"
  }
}
```

Decision: **extend `DesignTokens`, do not add a second brand-kit concept.**
`designs/` is already the design-system directory, the loader already validates
fonts, and every new field is optional and backwards compatible. Words and
assets stay in the content pack; the design owns styling only.

### 5.6 Fonts and glyph safety

- `BUNDLED_FONTS` grows to include **Cormorant Garamond** (display) and
  **Noto Serif KR** (Hangul + Hanja), both OFL.
- Noto Serif KR is too large to vendor whole. `scripts/build_font_subsets.py`
  (fonttools is already in `uv.lock`) builds a woff2 subset from a glyph
  manifest: the Hangul syllables used by the series plus the declared Hanja
  set. Slice 1 ships the manifest at `designs/glyphs/noto_serif_kr.json`; slice 2
  lets a content pack extend it. The subset is committed; the builder is
  committed so it is reproducible.
- `font_face_css()` gains a glyph-manifest argument and fails when a design
  names an unbundled family (existing behaviour, kept).
- **Glyph gate:** every CJK character in any layer text must appear in the
  content pack's declared glyph table (character, reading, meaning). A stray or
  wrong Hanja fails the build. This is the brief's "check every Hanja layer
  against the table", automated.

### 5.7 Safe areas

`safe_area.py` gains named profiles:

| Profile | Insets (fraction of canvas) | Use |
|---|---|---|
| `vertical_short` (default, unchanged) | 8% / 20% | existing videos |
| `vertical_short_strict` | top 0.1146 (220 px @1080×1920), bottom 0.25 (480 px), sides 0.08 | Cosmic Saju series |
| `title_safe` (unchanged) | 5% all round | landscape |

`fit_box` is unchanged and stays opt-in; the gate reports violations.

### 5.8 QA gates

| Gate | Implementation | Fails when |
|---|---|---|
| canvas/duration/audio | existing `explainer_qa.evaluate_qa` (`:145`) | unchanged |
| **glyph** | layer text × glyph manifest | undeclared CJK character |
| **cue** | `resolve_cues` strict mode | a declared cue matches no spoken word |
| **safe area** | `safe_area_violations` per profile | text layer outside profile |
| **composition** | `hyperframes check` (lint + runtime validate + layout) | any non-zero exit |
| **visual** | `hyperframes snapshot` at each scene midpoint into `qa/frames/` | blank/near-uniform frame (variance floor) |
| **parity** (slice 1 only) | frame SSIM vs reference render | SSIM below threshold |

The composition and visual gates are new wrapper functions in
`hyperframes_cli.py` (`check_project`, `snapshot_project`), so tests can mock
the CLI exactly as `render_project` is mocked today.

## 6. Data flow

```
script.md ──→ scene plan ──→ stills / TTS / transcript ──→ Composition ──→ HyperFrames project ──→ render
RenderGraph (cuts + overlays + audio) ────────────────────→ Composition ──┘
content pack YAML (slice 2) ──→ plan ─────────────────────┘
```

One compile step, one render step, one QA step — regardless of which adapter
produced the Composition.

## 7. Slices

| Slice | Delivers | Proof |
|---|---|---|
| **1** | `composition.py`, `motion/`, `compile_composition.py`, both adapters; design/font/safe-area extensions; subsets; five QA gates | parity rebuilds of two existing videos, no spend |
| 2 | content-pack format + `ve short` command; Saju series brand kit; placed caption/chip/pill/scrim/end-card layers (§12) | Video 1 from the brief built from data |
| 3 | the five brief shorts | five shipped videos, zero bespoke Python |
| 4 | modular sub-composition output + scene archetypes | slice-1 parity still passes |

2.5D `layerize` is deliberately **not** on this ladder: it needs a
segmentation/relighting pipeline and paid per-layer asset sets, none of which
the brief's proof videos require. It gets its own spec once §12's pack has
proven the data shape (§11 records the reasoning).

### 7.1 Slice 1 acceptance

**Machinery**

1. `Composition` model validates canvas, contiguity, `audio_mode`, motion names.
2. Motion registry implements every primitive in §5.4; `drift` reproduces
   `_KB_MOVES` bit-for-bit.
3. `compile_composition` emits the contract in §5.2; both adapters delegate.
4. `DesignTokens.layers` optional block; Cormorant Garamond + Noto Serif KR
   subsets bundled and embedded.
5. `safe_area.py` profiles, including `vertical_short_strict`.
6. `hyperframes_cli.check_project` / `snapshot_project` wrappers.
7. Glyph, cue, safe-area, composition and visual gates wired into the explain
   pipeline's QA report. The cue gate applies wherever `resolve_cues` is used
   today — typographic scenes in `typographic_scenes.py` and the per-project
   transcribe step — and the explain pipeline is the first consumer.

**Parity (fully offline)**

| Case | Reference | Rebuild | Assertion |
|---|---|---|---|
| Scene-plan adapter | `projects/wiz-explainer/composition/rendered.mp4` | `render_scene_plan` on the saved `plan/scene_plan.json` | whole-video SSIM ≥ 0.98, per-scene min ≥ 0.95, duration within 1 frame, every composition gate (`glyphs`, `cues`, `safe_area`, `composition`, `visual`) passes |
| Render-graph adapter | `projects/saju-january-animal/hyperframes/captioned.mp4` | the existing `overlays.py` flow over the saved `rendered.mp4` + `timeline.json` | whole-video SSIM ≥ 0.98, per-scene min ≥ 0.95, duration within 1 frame, caption span count and animation delays unchanged (within 1 frame) |

> **Parity scope (recorded 2026-10-26).** The harness asserts the five composition gates on the scene-plan rebuild and caption span count and delays on the render-graph rebuild. The measured-file checks (`explainer_qa`) never touch the compiler — they measure the finished MP4 — so they stay covered by that module's own tests rather than re-running here.

> **Cue content fix (recorded 2026-10-26).** Once the cue gate read the renderer's cue map it flagged two display-vs-narration drifts in the shipped wiz plan: `scene_3`'s first bar is labelled "Wiz", which the narration never speaks, and `scene_6`'s third step reads "Breaches start with a wrong key." where the voice says "breach starts with one wrong key". A `bar_compare` bar and a `sequence` step may now carry an explicit `cue` naming the spoken phrase, and the plan uses it for the first bar while the step's wording was corrected. The reference render was **not** replaced: the corrected plan rebuilds against the pre-migration artifact at whole-video SSIM 0.9960 (worst scene 0.9886), so the case still compares the unified compiler against the video that shipped, not against its own output.

Both cases run through `scripts/parity_rebuild.py`, which copies the reference
renders to `qa/parity/reference/` before asserting. Neither case makes a
network call or calls a paid provider — the harness has no cost path at all.

> **Running on a clean checkout (recorded 2026-10-26).** `projects/` and
> `videos/` are git-ignored build output and the two reference renders are
> 152 MB and 14 MB, so the harness cannot be self-contained in git. Instead of
> failing deep inside a case with `FileNotFoundError`, `main()` preflights the
> per-case `PREREQUISITES` table, prints each absent path with its remedy, and
> exits **2** — distinct from the assertion failure's exit 1, so CI can tell
> "the build output is missing here" from "parity regressed". Selection is per
> case: `--case scene-plan` does not require the saju build.

If SSIM misses, the fix is the primitive defaults or the adapter mapping — never
a relaxed threshold.

## 8. Testing

- **Unit:** each primitive's keyframes/declaration for fixed params; keyframe
  dedupe; clamping of delay+duration; motion-registry validation errors.
- **Model:** Composition contiguity, `audio_mode`, rect bounds, unknown preset.
- **Adapter:** `from_scene_plan` reproduces the existing Ken Burns index
  mapping; `from_render_graph` preserves layer count, rects and timings.
- **Golden:** emitted `index.html` for a fixed Composition, compared as a whole
  (string snapshot), so an accidental compiler change is visible.
- **Gate:** glyph gate fails on an undeclared Hanja; cue gate fails on a missing
  cue; safe-area gate fails on a pill below the strict profile.
- **Integration (`slow`):** the two parity cases.
- Existing suite must stay green. The migration is accepted only with the full
  suite passing.

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A primitive cannot reproduce the old render, so SSIM fails | `drift` is specified against `_KB_MOVES` values, not "similar"; per-scene SSIM localises the miss |
| Unifying breaks a caller of the old compilers | function names and modules preserved; adapters are the only change |
| Noto Serif KR subset misses a Hanja a later script needs | glyph gate fails loudly at build time; rebuild the subset with the new character |
| `hyperframes check`/`snapshot` are version-sensitive | thin wrappers with a mocked-CLI unit test; a CLI upgrade touches one module |
| CSS-only `count` looks worse than a JS counter | it is a slice-3 concern; the primitive is isolated and can be swapped |
| Scope creep into content production | slice 1 is explicitly machinery + parity; the five shorts are slice 3 |

## 10. Appendix — brief motion vocabulary → engine

| Brief line | Engine primitive | Slice |
|---|---|---|
| "Drift: slow zoom (100%→108%) and pan" | `drift` | 1 |
| "Stamp: drops in with a quick scale (120%→100%) and ink-bleed blur" | `stamp` | 1 |
| "Card flip: a tile turns over to reveal a new character" | `flip` | 1 |
| "Draw: lines, rings or bars draw themselves in 0.6–1 s" | `draw` | 1 |
| "Kinetic caption: each phrase pops on beat, keyword in gold" | `word_highlight` | 1 |
| "Two Hanja slide in from left and right and lock together" | `lock` | 1 |
| "Counter ticks 1 → 11" | `count` | 1 |
| "Standard end card" | `end_card` preset | 1 |
| "Five labels stamp around the circle" | `stamp` × 5 with staggered delays | 1 (data in slice 3) |

## 11. Deferred past slice 2

**2.5D photo layering (`layerize`).** Splitting a still into depth layers
(subject / midground / background) and parallaxing them is a content-production
capability, not a compiler one. It needs three things this spec does not carry:
a segmentation/relighting pipeline, per-layer asset sets in the content pack,
and a `Composition` with several `media` layers per scene, each with its own
`drift` params. The engine needs **no change** to support it — several media
layers per scene, root-level with independent `z`, already compile — so it gets
its own spec and plan once the content pack (§12) has proven the data shape on
real content, rather than being bundled into that pack's first slice.

**Safe-area measurement of typographic scenes.** The safe-area gate measures
the rects of explicitly placed `text` / `rich_text` layers. On the scene-plan
path there are none: a typographic scene compiles to a single full-canvas
`html` layer and the scene renderer lays its text out with CSS flex and a
container padding, so no per-text pixel box exists in the `Composition` to
measure against. Slice 2 (§12) puts captions, chips, pills and end cards into
the composition as placed `rich_text` / `text` layers, which is what makes the
gate measure; the **explainer** path keeps its full-canvas `html` layer and
stays covered by the `composition` gate's post-layout pass. Rendered overflow
was always measured honestly there, so nothing is left unmeasured — only
unmeasured *by this gate*.

## 12. Slice 2 — content pack and `ve short`

Scope: a per-video content pack, a `ve short` command that runs it end to end,
a Saju series brand kit, and placed caption/chip/pill/scrim/end-card layers.
Proof: Video 1 of the brief built from data.

### 12.1 The pack

A new tracked source root, `shorts/<slug>/short.yaml`. `videos/` and
`projects/` are both git-ignored build output (today's `script.md` and the
1,819 lines of per-project build Python are untracked), so a pack — which is
*source* — cannot live there. `designs/` keeps series identity; the pack
carries only words and asset prompts:

```yaml
job_id: saju-v1-your-animal
design: saju_series                 # designs/saju_series.json
aspect: "9:16"
voice:  {provider: sarvam_tts, model: bulbul:v4-flash, speaker: sarang_en_narration,
         language: en-IN}
image:  {provider: poyo_ai, model: gpt-image-2.5-fixed, resolution: 2K,
         style_suffix: "<the brief's shared image style, verbatim>"}
end_card: {text: "Free chart snapshot · cosmicsaju.com", seconds: 3}
scenes:
  - id: hook
    still: {prompt: "A single small rat sitting on an ink-wash plum branch…", key: rat}
    chip: "八字"
    lines:
      - say: "You think you're a Rat? That's one-eighth of you."
        show: "You're not just a Rat."
        cue: "Rat"
    motion: drift
```

- `say` is the narration *and* the TTS text. A line may add `speak_as` for the
  phonetic spellings the previous shorts' pronunciation passes needed
  ("Sah-joo", "Ip-choon"); it is identical to `say` everywhere else.
- `show` is what the caption or pill displays, defaulting to `say`.
- `cue` names the spoken phrase a motion fires on, using slice 1's `cue`
  semantics (`find_cue` with per-token prefix matching), so the cue gate
  polices pack-authored cues the same way it polices typographic ones.
- No styling keys. Colours, fonts, safe area, pill/chip/end-card shape and the
  caption preset come from `design`.

### 12.2 The brand kit

`designs/saju_series.json`: the existing brand preset's palette, Cormorant
Garamond + Inter + Noto Serif KR, and the `layers` block slice 1 built —
`safe_area: vertical_short_strict` (220 / 86 / 480 px at 1080×1920),
`scrim` / `chip` / `pill` / `end_card` styles, `caption_preset: karaoke`.
`presets/brands/saju-2026-07-03.json` keeps supplying provider and prompt
defaults; it gains no design data.

**One visible consequence.** `vertical_short_strict`'s 480 px bottom inset puts
the safe bottom edge at y = 1440. The shipped saju shorts place the caption box
at y 1380–1560, which crosses that line — they passed only because the gate was
inert on that path. Under `ve short` the captions move up into the safe band
(top edge ≤ 1260 for a 180 px box), so slice 2's Video 1 will not be
pixel-identical to the earlier shorts. The brief's own rule ("keep text out of
the … bottom 480 px") is the reason, and the design carries the box so every
later short is compliant without a per-video decision.

### 12.3 One render pass

Today the saju short is two: `compose.py` hand-writes HyperFrames HTML for the
scrims, Hanja chip, cue pills and end card, then `overlays.py` runs a second
render-graph pass to burn karaoke captions on top. Slice 2 compiles both into
one `Composition`:

| Today | Slice 2 layer |
| --- | --- |
| background still | `media`, full canvas, `drift` from design |
| top/bottom gradients | `shape` scrim layers, below all text |
| Hanja chip | `text`, `preset="chip"` |
| cue pills | `text`, `preset="pill"` |
| karaoke captions (second pass) | `rich_text`, `preset="caption_line"`, `WordSpan`s from the alignment |
| end card | `text`, `preset="end_card"`, last 3 s |

Every one of these carries a real rect, so the safe-area gate measures the
strict profile instead of staying inert. One compile, one render, one QA pass.

### 12.4 `ve short`

`ve short <shorts/<slug>/short.yaml>`: validate the pack → quote the cost and
gate on it → stills (existing provider chain, cached) → TTS → alignment →
`Composition` via `adapters/from_content_pack.py` → `compile_composition` →
render → the five gates plus the measured checks → export. `ve explain` is
untouched; typographic explainers keep their own path.

### 12.5 Acceptance

1. A pack round-trips: unknown keys, an empty `lines`, a missing `still.prompt`
   and a non-bundled font are validation errors, not silent defaults.
2. `adapters/from_content_pack` compiles a pack to a `Composition` with the
   layer mapping in §12.3 and the design's motions.
3. The safe-area gate reports a violation when a chip or caption rect is pushed
   outside `vertical_short_strict`, and is quiet when it is not — measured on
   the pack path, not inert.
4. The karaoke caption layer reproduces today's word-level styling (dimmed
   upcoming, popped spoken word, read-ahead) from alignment spans.
5. An **offline** fixture build of the Video 1 pack (placeholder stills, canned
   alignment) passes all five gates with no provider call and no spend.
6. Video 1 of the brief renders from its pack — five stills and one Sarvam
   take, quoted before the spend — and passes QA.


