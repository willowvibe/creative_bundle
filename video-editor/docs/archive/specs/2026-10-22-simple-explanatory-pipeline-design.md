# Simple Explanatory Video Pipeline — Design

**Date:** 2026-10-22
**Status:** Approved for planning
**Builds on:** `videos/2026-10-22/saju-zodiac-animal/` (11 scenes, 52.6 s, 7 stills, QA 10/10)

## 1. Problem

Every "explain a topic in under a minute" video in this repo is still a bespoke
script chain. `saju-zodiac-animal` cost eleven hand-written files (`content.py`,
`scene_plan.py`, `images.py`, `voiceover.py`, `transcribe.py`, `compose.py`,
`overlays.py`, `mix.py`, `verify_qa.py`) and produced something good. The next
topic starts from zero files again.

At the same time, the reference video we compared against — `zbacktrack |
Tech • Semiconductor • AI`, *Google Just Spent $32 Billion On ONE Company*,
99 s, 214,758 views, published 2026-10-16 — reaches a visibly richer result with
*fewer* assets: roughly half its runtime is one HTML scene whose only motion is
grid cells lighting up. Its look is flat typographic composition on a
two-colour system, not more generated imagery.

So the gap is two things at once:

1. **No reusable pipeline** for the stills + narration + karaoke caption format.
2. **No scene kind** that renders a typographic composition, which is where the
   reference video's production value actually comes from.

This spec covers both, as one pipeline.

### What already exists (and is reused, not rebuilt)

| capability | where |
|---|---|
| Plan → HyperFrames HTML, with Ken Burns keyframes, looping video, per-scene audio, root music bed | `packages/core/src/video_editor_core/scene_plan_composition.py:63` (`compile_scene_plan_project`), per-scene emitter at `:120` |
| Plan-level still reuse (`props.image_scene`), honoured by the cost gate and the renderer | `packages/asset_ingestion/src/video_editor_asset_ingestion/scene_assets.py:239` (`requests_from_plan`), `resolve_image_reuse()` (#271) |
| Word-level timings from synthetic narration | `apps/transcribe/src/video_editor_transcribe/whisper_local.py:55` (`transcribe_audio`) |
| Karaoke captions, read-ahead, safe-area layout | `skills/captions/src/captions/models.py:112`; `presets/short_explainer.json` |
| Word-cued reveals | `video_editor_core.cues.resolve_cues` |
| Template manifest + router | `templates/*.yaml`, `TemplateRouter` |
| Cost estimate gate before spend | `apps/cli/src/video_editor_cli/main.py:1240` |

## 2. Goals

- One command turns a `script.md` into a finished, captioned, loudness-normalised
  vertical explainer, with a QA report that either passes or names what failed.
- A script author writes narration and *intent* ("a big number", "two bars") — never
  durations, never reuse links, never asset IDs.
- Typographic scenes render from a design system, so the reference video's look is
  reachable without generating an image per beat.
- Stills are reused automatically where the script actually reprises a subject, and
  the reuse decision is visible *before* money is spent.
- The whole thing is testable: every boundary below has a unit test, and one real
  build is the smoke test.

## 3. Non-goals

Generated video clips inside this pipeline, multi-voice narration, dubbing,
FableCut export, thumbnails, topic research, publishing. The saju job needed none
of them.

## 4. Architecture

```
templates/explainer_stills.yaml        ← manifest, routes pipeline_kind
apps/cli/.../explainer_pipeline.py     ← new, single-purpose orchestration
apps/cli/.../explainer_script.py       ← new, script.md → ScriptSpec
apps/cli/.../still_reuse.py            ← new, subject clustering
apps/engine/.../templates/designs.py   ← new, design-system loader
packages/core/.../typographic_scenes.py ← new, layout → HTML
```

`PipelineKind` gains `"explainer_stills"` (`apps/engine/src/video_editor_engine/templates/base.py:16`).
`ve run` gains a branch in `_handle_run` (`main.py`), and a matching worker
handler so `ve enqueue` accepts it.

The orchestration lives in its own module rather than in `pipeline.py`, which is
already 1533 lines holding two unrelated pipelines (`SkillsPipeline:90`,
`MusicVideoPipeline:1016`).

### Run sequence

```
1. parse script.md ............................... free, deterministic
2. build ScenePlan + reuse report ................ free, deterministic
3. write plan/scene_plan.json + plan/reuse_report.json
4. ESTIMATE GATE ................................. prices the written plan
5. one TTS request for the whole narration
6. transcribe → word timings
7. derive scene boundaries; rewrite plan durations
8. stills (one per reuse cluster) ............... paid
9. compile ScenePlan → HyperFrames project; render
10. mux narration, duck music under it
11. burn karaoke captions
12. loudnorm −14 LUFS
13. QA gate → build/qa.json, non-zero exit on failure
```

Steps 1–3 run **before** the estimate gate, and that ordering is deliberate: the
plan is the thing being priced, and the reuse table is the most important thing a
human can look at before approving a purchase. Today the gate only fires when a
plan already exists (i.e. on resume); for this pipeline it fires on every run.

## 5. Script format

`script.md` is the only required input. It replaces `content.py`.

```markdown
---
title: Why Google Paid $32 Billion For Wiz
aspect_ratio: 9:16
voice: sarang_en_narration
caption_preset: karaoke
design: cream_editorial
reuse_threshold: 0.30
target_seconds: 50
---

## Scene 1 — The number
visual: typographic
layout: big_stat
headline: $32B
sub: Google's largest acquisition ever
narration: |
  Thirty-two billion dollars. That's what Google just paid for a company
  most people have never heard of.

## Scene 2 — The racks
visual: still
image: |
  two black server racks in a warm cream data centre, a red rental sticker
  taped to one, soft overhead light
narration: |
  Wiz is five years old. Its job is to watch the whole cloud.

## Scene 3 — The comparison
visual: typographic
layout: bar_compare
bars:
  - {label: Wiz, value: 32, unit: B}
  - {label: Nest, value: 3.2, unit: B}
  - {label: Fitbit, value: 2.1, unit: B}
narration: |
  It is Google's biggest acquisition ever. Bigger than YouTube, bigger
  than Nest, bigger than Fitbit.
```

### Rules

- `visual` is exactly `typographic` or `still`. `typographic` requires `layout`.
  `still` requires `image`. Anything else is a validation error naming the scene.
- Scene **duration is never authored**. It is derived from the narration (section 9).
- A still scene that turns out to reuse another scene's image still writes its own
  `image` prompt. The prompt is not wasted: it is half the word set that lets
  clustering recognise that the subject is the same (section 6).
- `subject:` is optional on any scene and splits or joins reuse clusters (section 6).
- Front-matter keys: `title`, `aspect_ratio` (default from template), `voice`,
  `caption_preset` (default `karaoke`), `design`, `reuse_threshold` (default `0.30`),
  `target_seconds` (optional QA bound), `music_mood`.
- Narration must be non-empty on every scene: a silent scene in a 50-second explainer
  is a bug, not a feature.

### `--from-topic`

`ve run --template explainer_stills --from-topic "why Google bought Wiz"` generates
`script.md` via the existing `build_scene_plan` LLM path, writes it to
`build/script.generated.md`, and stops **before** the estimate gate unless
`--yes` was passed. The generated script is then edited or accepted as-is; the
build path from that point is identical, deterministic, and has no LLM in it.

## 6. Automatic still reuse

The engine already honours reuse: `resolve_image_reuse()` reads `props.image_scene`,
`requests_from_plan()` skips the IMAGE request for member scenes, and the cost gate
and renderer therefore agree (#271). This design adds the **decision**, not the
mechanism.

### Algorithm

Runs on still scenes only, before any asset request is built.

1. For each still scene, build a word set from its `image` prompt **plus** its
   narration: lowercase; strip punctuation; drop stopwords; fold a trailing
   `s`/`es`; keep numerals and capitalised tokens.
2. Walk still scenes in script order. A scene joins an existing cluster when the
   Jaccard overlap between its word set and the cluster's accumulated word set is
   `>= reuse_threshold`.
3. A cluster's owner is its first scene. Every later member gets
   `props.image_scene = owner.id`.
4. A scene carrying `subject: X` never merges with a scene carrying a different
   `subject`. Scenes sharing a `subject` always merge.
5. Write `plan/reuse_report.json` and print it at the estimate gate.

Both prompt and narration are used because a reprise restates its subject in
words ("the rest is what makes it yours" returning to the 八字 gate) rather than
in the prompt. Prompt-only clustering misses it; narration-only over-merges on
generic vocabulary.

### Report

```
still reuse: 7 still scenes → 4 renders (3 saved, 15 credits / $0.075)
  cluster "zodiac tiles"   scenes 3, 5, 10   overlap 0.41   → render of scene_3
  cluster "gem"            scenes 6, 7       overlap 0.36   → render of scene_6
  unclustered              scenes 2, 8, 9                    own render
```

### Failure mode and escape hatches

Two still scenes can share vocabulary while needing different pictures. The
mitigations are visible, not silent:

- `subject: metals` on either scene splits the cluster. This is the intended fix,
  and it is a statement of intent, not a reuse link.
- `--no-reuse` disables clustering for a run.
- The report prints before the purchase, so a bad merge is caught at the gate.

Clustering is deterministic: same script in, same plan out, same cost.

## 7. Scene vocabulary and rendering

### New scene type

`"typographic"` is added to `_ALLOWED_SCENE_TYPES`
(`apps/engine/src/video_editor_engine/models/scene_plan.py:13`), so the plan states
what the scene is instead of hiding it in `props`. The safety property is free:
`requests_from_scene()` only emits an IMAGE request when
`visual_description` is non-empty, so a typographic scene with no
`visual_description` emits no paid request in *any* pipeline.

### Data carried in `props`

```json
{"layout": "big_stat", "headline": "$32B", "sub": "...", "unit": ""}
```

Layout-specific keys are validated by the layout renderer, not by `Scene`.

### Layouts in v1

Five layouts, each a pure function `(props, design tokens, duration) -> (html, keyframes)`:

| layout | renders | reference |
|---|---|---|
| `big_stat` | one number or word, sub-line, optional unit | the `$32B` frame |
| `bar_compare` | 2–4 labelled bars with values | `$32B` vs `$12.5B` |
| `definition` | term, gloss, small mark | "A digital X-ray" |
| `sequence` | 3–5 steps on a path with a draw-on connector | the red-dot L-path |
| `array` | grid of cells lighting in order | the rack-grid X-ray |

Every typographic scene gets three motions, so a held shot never reads frozen
(the rule already established for ambience and camera drift in #240–#247):

1. an entry (0.35 s rise/fade) scaled by the design's `motion.tempo`;
2. per-item reveals cued to narration words through `resolve_cues`;
3. a slow camera drift at the design's `motion.drift` scale.

### Compiler change

`_scene_elements()` (`scene_plan_composition.py:120`) gains one branch, before the
existing placeholder fallback:

```python
if scene.get("type") == "typographic":
    elements.append(_typographic_element(scene, index, start, duration, design))
elif visual is not None:
    ...
```

The emitted element is a root-level `<div class="clip" data-start data-duration>`
containing the layout's markup. HyperFrames only seeks root-level media, so the
div stays at root and the layout nests inside it.

`compile_scene_plan_project()` keeps its signature. The design name travels on the
plan (`plan["design"]`), written by the pipeline and defaulted to `cream_editorial`
when absent, so the compiler resolves tokens without a new argument and every
existing caller stays valid.

## 8. Design systems

`design: cream_editorial` resolves to `designs/cream_editorial.json`. The data files
sit at repo root next to `presets/`; the loader lives in the engine and finds them
the way presets already are found (`ProjectConfig.presets_dir(repo_root=...)` gains a
sibling `designs_dir`).

```json
{
  "name": "cream_editorial",
  "palette": {"bg": "#EFEAE3", "ink": "#141210", "accent": "#C8102E", "muted": "#8A8378"},
  "type": {"display": "Playfair Display", "body": "Inter", "display_weight": 700},
  "theme": "light",
  "motion": {"tempo": 1.0, "entry": "rise", "drift": 1.06}
}
```

v1 ships `cream_editorial` (the reference look) and `ink_noir` (the same grammar,
dark). A missing `design` falls back to `cream_editorial`. Fonts must already be
bundled by `fonts.py`; an unbundled family is a validation error rather than a
silent system-font substitution.

## 9. Timing derived from narration

- One TTS request for the entire narration, `asset_id="job_narration"`, provider
  from the template (Sarvam default), carrying the front-matter voice as request
  params (`model`, `language`, `voice_id`) exactly as a scene-level request would.
  Scene-scoped narration is the same string, so one request replaces N.
- `requests_from_scene`/`requests_from_plan` gain `emit_tts: bool = True`,
  symmetric with #271's `emit_image`, so the pipeline can suppress per-scene TTS and
  add the job-level request itself.
- Timings from `transcribe_audio` with `initial_prompt` set to the narration's opening
  and **`vad_filter=False`** — the #268 lesson: VAD trimming a synthetic tail silently
  loses the last sentence.
- Boundary for scene *k*: starts at its first word's start minus a 0.12 s lead-in
  (clamped to the previous scene's end, never overlapping), ends at its last word's
  end plus 0.25 s interior tail, or **1.2 s** on the final scene.
- `plan/scene_plan.json` is rewritten with the measured durations before rendering, so
  the plan on disk matches what shipped.

## 10. Audio, captions, loudness

- Music: `poyo_music` by default, sidechain-ducked under narration.
- Captions: the existing `karaoke` preset, `max_chars 34`, read-ahead window, burned
  after mux from the same transcript used for timing.
- Loudness: one pass on the final mix, −14 LUFS integrated, true peak ≤ −1.0 dBTP.

## 11. QA gate

The run fails unless every line passes.

```
canvas 1080×1920 @30fps ✓   loudness −14.0 LUFS ✓   true peak −1.3 dBTP ✓
duration 51.8 s (target 50, +3.6%) ✓
scene starts within 0.25 s of a word ✓ (11/11)
caption coverage 99.4% of transcript words ✓
stills: 7 scenes → 4 renders (reuse report matches) ✓
silent gaps > 1 s inside narration: none ✓   tail after last word: 1.2 s ✓
cost $0.19 of $0.25 estimate ✓
```

Written to `build/qa.json`, printed as a table, exit code 1 on any failure. When
`target_seconds` is absent, the duration check is the format's own bound: 30–90 s.

## 12. CLI surface

```bash
ve run --template explainer_stills videos/2026-10-22/wiz/script.md
ve run --template explainer_stills --from-topic "why Google bought Wiz"
ve run --template explainer_stills script.md --no-reuse
ve estimate jobs/<job_id>          # prices the exact plan, reuse included
```

## 13. Testing

Test-first, one module per boundary.

| test module | covers |
|---|---|
| `tests/test_explainer_script.py` | front-matter parsing, scene blocks, unknown layout rejected, missing narration rejected, `subject:` recorded |
| `tests/test_still_reuse.py` | cluster detection on a synthetic six-scene script, threshold, `subject:` split, `--no-reuse`, determinism |
| `tests/test_explainer_timing.py` | word→scene mapping, lead-in clamp, final tail, single-word scene |
| `tests/test_typographic_scenes.py` | each layout emits its elements, text escaped, tokens applied, keyframes finite |
| `tests/test_scene_plan_composition.py` | typographic scene compiles with no `<img>` and correct `data-start`/`data-duration` |
| `tests/test_explainer_pipeline.py` | stub providers; one TTS request for N scenes; a two-scene cluster yields one image request; estimate reflects the saving |

One real build to `videos/2026-10-22/<project>/` is the smoke test, in line with
the repo's existing split (build scripts under `videos/` are not unit-tested).

## 14. Risks

| risk | mitigation |
|---|---|
| Clustering merges two scenes that deserve different pictures | reuse table at the estimate gate; `subject:` split; `--no-reuse` |
| Whisper mistimes synthetic narration | `initial_prompt` + `vad_filter=False`; boundary clamping; QA asserts scene starts land on words |
| Typographic scene renders differently from its preview | layouts are pure functions with string-assertion tests; one real build reviewed frame by frame |
| One TTS call makes a single mispronunciation expensive to fix | the request is cached by asset id; a fix re-bills one request, not eleven |
| A 30-second layout scene exposes the "frozen frame" problem | entry + word-cued reveals + drift are mandatory, not optional |

## 15. Rollout

1. Script parser and reuse clustering (free, testable without network).
2. Typographic layouts and the compiler branch (still no spend).
3. Pipeline wiring, estimate gate, TTS/transcribe integration.
4. One real build against a topic we already know cold, reviewed frame by frame.
5. Docs: `docs/templates.md`, `docs/CLI_USAGE.md`, `docs/POYO_ROUTING.md` if defaults move.

## 16. Side fix folded in

`templates/explainer_motion.yaml` lists `motion_graphics`, `code_highlight`, `diagram`,
`callout`, and `full_bleed` as `scene_type_hints`, but `Scene` accepts none of them
(`scene_plan.py:13`). Those hints should describe `motion_graphics` props rather than
scene types, so a user who follows the template does not hit a validation error.
