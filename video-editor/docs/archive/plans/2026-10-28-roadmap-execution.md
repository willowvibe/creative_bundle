# Roadmap 2026-10-28 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute `docs/archive/plans/ROADMAP_2026-10-28.md` in dependency order: emit scene transitions in the
Composition IR (unblocking the A3 musical port), add sound design, add 2.5D layer separation,
add film-look grading, and close the remaining MCP spend and documentation gaps.

**Architecture:** Every visual feature lands in the Composition IR or in the one compiler
(`compile_composition`), so `ve short`, `ve explain` and the ported musical path get it at once.
Transitions are a pure pre-pass over the IR (`apply_scene_transitions`) that adds motions and
extends layer ends; the compiler itself stays a dumb emitter. Audio features extend
`AudioCue`/`Composition.audio` and the single FFmpeg mix pass, never a second renderer.
Film-look is native HyperFrames grading (`data-color-grading`), not a layer stack of our own.
2.5D is an opt-in stage that produces three cached images per scene plus a `parallax` primitive.

**Tech Stack:** Python 3.12, pydantic v2, Typer, HyperFrames CLI 0.8.208 (pinned in
`package.json`), FFmpeg, pytest, structlog.

---

## Scope

This plan covers all six roadmap priorities and the two open items in
`docs/ISSUES_BUGS.md` that are actionable offline. It is split into seven slices. Slice 1 is the
only blocker for another slice; the rest are independent and can be parallelised.

| Slice | Roadmap item | Delivers |
| --- | --- | --- |
| 1 | #1 transitions | `CompositionScene.transition_in` emitted; `fade_out` primitive; crossfade |
| 2 | #1 (A3) | Musical composer + pop captions ported to the IR, with a parity case |
| 3 | #5 bugs | `sfx_placement` spend capped; `dubbing` MCP tool retired (or wired) |
| 4 | #4 film look | `DesignTokens.grade` → `data-color-grading`; LUT, grain, vignette, light leak |
| 5 | #2 sound design | Cue table from the Composition, SFX providers, ducked mix, sync QA |
| 6 | #3 2.5D | Spike, `layers: auto`, `layerize` stage, `parallax` primitive |
| 7 | #6 tooling | Project skill; stale task/bug docs corrected; plugin pruning (user-local) |

### Progress

- **Slice 1:** complete (`eb2dfb8`). `scene-plan` parity stays at SSIM 0.9960 with all gates
  green; `render-graph` rebuilds the committed HTML and video byte-identically (SSIM 1.0000).
- **Slice 2:** complete except 2.4.7 (the two scripts stay as the FFmpeg reference for one
  release). `ve musical` and `--case musical` pass: whole SSIM 0.9760, scenes 0.9706 / 0.9814,
  boundary 0.9620, all four composition gates green.
- **Slice 3:** complete (SFX placement under `job_budget`; the `dubbing` MCP tool retired).
- **Slice 4:** the grade/LUT half is complete (`DesignTokens.grade` → sparse
  `data-color-grading`, LUT copied); the light-leak overlay is deferred until it has a
  placement policy.
- **Slice 5:** the cue table, the local-first bank with an ordered resolver chain, the
  Freesound/Openverse providers, the voice + ducked music + SFX mix, the `ve short`
  `sfx: auto` wiring and the advisory `hyperframes audio` report are complete. The Freesound
  key was verified live on 2026-10-28: short CC0/CC BY matches for hit/click/flip/latch/whoosh,
  readable licence labels and transport retries. The whole chain was then run on
  `projects/saju-january-animal/voiced.mp4`: the `rise`-only pack needed scene-cut cues, so
  `cues_from_composition(scene_cuts=True)` adds one whoosh per cut; eight distinct Freesound
  sounds resolved, credits deduped, and the 52.833 s duration was preserved. The QA report now
  compares mixed vs voice LUFS (global ceiling +2) and lists per-cue added level/onset; the live
  run is 8/8 sync with +0.0 LUFS. Earshot v0.4.0 is installed at `~/tools/earshot`, configured
  in `config.local.yaml`, and is first in the live chain: eight scene-cut whooshes rendered
  (8/8 sync, +0.0 LUFS, no credits). The external listening trial remains.
- **Slice 6:** not started (needs the RTX 3050 spike).
- **Slice 7:** repo-side work is complete (project skill, `TASKS.md`, docs index, plan
  indexing); the plugin list is machine-local. Removing the two legacy musical scripts remains
  the release follow-up.

### Non-goals

- No second renderer. Do not adopt DepthFlow (AGPL) or vendor a WebGL transition renderer.
- No shader-transition parity in slice 1. The registry blocks are standalone demo
  compositions (see Findings), so slice 1 ships a crossfade and slice 1b may add CSS
  approximations later.
- No self-hosted image generation, no rented GPU, no lip-sync pipeline (roadmap decided
  against/deferred).
- No changes to provider pricing or model routing.

---

## Global Constraints

Every task implicitly includes this section.

- Times are absolute timeline seconds, frame-quantised with `quantise(seconds, fps)`.
- Scene windows stay contiguous and cover `composition.duration` exactly
  (`Composition._scenes_are_contiguous`).
- Layers are emitted as root-level clips; never nest media inside a scene wrapper.
- No inline style may contain a double quote; `_style_attr` escapes them.
- No JS-dependent motion. Every effect must survive HyperFrames' deterministic frame seek.
- No test may make a network call or spend money. Provider calls in tests are faked.
- `uv run mypy apps packages` must stay clean; `ruff format` and `ruff check` are CI gates.
- `import-linter` contracts stay intact: core imports neither apps, MCP nor skills.
- HyperFrames is pinned to 0.8.208. Verify behaviour against that version, not a newer one.
- Commits end with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

---

## Findings That Changed the Plan

These were verified against the installed HyperFrames 0.8.208 and the current code. They are
the reason several roadmap items are scoped differently.

1. **Registry shader transitions are not a transition API.**
   `hyperframes add light-leak` writes a standalone composition whose snippet is
   `<div data-composition-src="compositions/light-leak.html" …>`. The file hardcodes its own
   `#s1`/`#s2` scenes, captures them to WebGL textures, and blends those. It cannot take two of
   our layers as inputs. The shader registry lives in
   `node_modules/hyperframes/dist/chunk-3GXQPCJC.js` (`TRANSITIONS`, 15 entries: `crossfade`
   plus 14 shader effects). A true shader transition would require pre-rendering the two
   adjacent scenes to frames, which breaks on video and costs a second pass. Slice 1 therefore
   emits a crossfade; CSS approximations are a follow-up.

2. **`data-playback-rate` is supported.** The runtime reads it (`Ot` in
   `hyperframe.runtime.iife.js`, clamped 0.1–10). The musical port's 50% slow-motion video
   needs no FFmpeg pre-pass. `data-media-start` is the trim offset.

3. **Media grading is native.** The runtime reads `data-color-grading` as JSON and merges
   sparse `adjust` / `details` / `effects` sections with defaults (`ml` in the runtime).
   `hyperframes media-treatment` normalises and writes the same attribute. The `lut` key is
   `{"src": "...", "intensity": 0..1}`; `.cube`, max size 64.
   So film-look is a compiler attribute, not a layer stack.

4. **MCP `dubbing` cannot run at all.** `_handle_dubbing`
   (`packages/mcp_server/src/video_editor_mcp/tools/vlog_skills.py:477-495`) constructs
   `DubbingSkill(ctx, config=…)` with no `translator` and no `tts_provider`.
   `DubbingSkill.run` raises `DubbingError("dubbing requires a translator")`
   (`packages/skills/src/dubbing/dubbing.py:326-331`). There is no translator implementation
   anywhere in `packages/`. The budget item is therefore "retire or wire", not "add a cap".

5. **`sfx_placement` is the uncapped spender.** `_handle_sfx_placement`
   (`vlog_skills.py:436-451`) builds `SfxPlacementSkill(ctx)` with no manager or budget, and the
   skill calls ElevenLabs SFX directly (`sfx_placement.py:139-162`). It is the live paid path the
   roadmap believed `dubbing` was.

6. **No parity case exists for the musical port.** `scripts/parity_rebuild.py` only has
   `scene-plan` and `render-graph` (`REFERENCE_PAIRS`, lines 166-170). Slice 2 has to add the
   case it is verified against.

7. **No Devanagari font is bundled.** `BUNDLED_FONTS` in
   `packages/core/src/video_editor_core/fonts.py` has Inter, Montserrat, Cormorant Garamond and
   Noto Serif KR only. The musical captions use a system Noto Sans Devanagari path.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `packages/core/src/video_editor_core/scene_transitions.py` | **New.** Pure IR pre-pass: `apply_scene_transitions`, supported names, validation. |
| `packages/core/src/video_editor_core/composition.py` | Remove the transition rejection; keep `transition_in: MotionSpec`. |
| `packages/core/src/video_editor_core/motion/primitives.py` | **New primitive.** `fade_out` (+ `parallax` in slice 6). |
| `packages/core/src/video_editor_core/compile_composition.py` | Call the pre-pass; emit `data-media-start`, `data-playback-rate`, `data-color-grading`. |
| `packages/core/src/video_editor_core/musical.py` | **New.** Segment/window math ported from `compose_musical_video.py`. |
| `packages/core/src/video_editor_core/adapters/from_musical.py` | **New.** Segment plan + lyrics → `Composition`. |
| `packages/core/src/video_editor_core/lyrics.py` | **New.** `parse_lyrics` extracted from the two existing copies. |
| `packages/core/src/video_editor_core/designs.py` | Add `Grade` and `DesignTokens.grade`. |
| `packages/core/src/video_editor_core/sfx_cues.py` | **New.** Composition → `SfxCue` list; motion→sound table. |
| `packages/core/src/video_editor_core/fonts.py` | Add Noto Sans Devanagari (subset) to `BUNDLED_FONTS`. |
| `packages/core/src/video_editor_core/content_pack.py` | Add `PackScene.layers` for 2.5D. |
| `packages/core/src/video_editor_core/layerize.py` | **New.** Depth/cut-out/inpaint stage and cache keys. |
| `packages/pipelines/src/video_editor_pipelines/musical_pipeline.py` | **New.** `ve musical` stage driver. |
| `packages/pipelines/src/video_editor_pipelines/short_pipeline.py` | Optional `layerize` and SFX stages. |
| `packages/skills/src/sfx_placement/sfx_placement.py` | Accept an `AssetIngestionManager`; route generation through it. |
| `packages/skills/src/sfx_placement/bank.py` | **New.** earshot / ElevenLabs / Openverse resolver + licence manifest. |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/freesound_sfx.py` | **New.** CC0/CC-BY search + preview download with provenance (primary free tier). |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/openverse_sfx.py` | **New.** Fallback index with licence filters (secondary free tier). |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/earshot_sfx.py` | **New.** Bridge to an earshot MCP server. |
| `packages/skills/src/sfx_placement/bank.py` | Local hand-picked files (incl. Pixabay) win; then earshot/ElevenLabs, then the free indexes. |
| `packages/ffmpeg/src/video_editor_ffmpeg/mix.py` | **New.** One pass: voice + ducked music + SFX timeline. |
| `packages/mcp_server/src/video_editor_mcp/tools/vlog_skills.py` | Budget for `sfx_placement`; remove `dubbing`. |
| `scripts/compose_musical_video.py` | Thin wrapper over the IR adapter after parity. |
| `scripts/burn_musical_captions.py` | Thin wrapper; `parse_lyrics` imported from core. |
| `scripts/parity_rebuild.py` | New `musical` case. |
| `tests/test_scene_transitions.py` | **New.** Pre-pass rules. |
| `tests/test_musical.py` | **New.** Window math. |
| `tests/test_from_musical.py` | **New.** Adapter layers/captions. |
| `tests/test_sfx_cues.py` | **New.** Cue table, dedupe, caps. |
| `tests/test_grade.py` | **New.** Sparse grading + LUT copy. |
| `tests/test_layerize.py` | **New.** Cache keys and opt-in gating. |
| `.claude/skills/video-editor/SKILL.md` | **New.** The repo's build/QA/cost loop. |

---

## Slice 1 — Scene transitions in the IR

**Depends on:** nothing. **Unblocks:** slice 2.

### Task 1.1: Accept `transition_in` and validate it

**Files:**
- Modify: `packages/core/src/video_editor_core/composition.py`
- Create: `packages/core/src/video_editor_core/scene_transitions.py`
- Test: `tests/test_composition.py`, `tests/test_scene_transitions.py`

**Interfaces:**
- `SUPPORTED_TRANSITIONS: frozenset[str] = frozenset({"crossfade", "fade"})`
- `class SceneTransitionError(ValueError)`
- `def apply_scene_transitions(composition: Composition) -> Composition`
- `CompositionScene.transition_in` stays a `MotionSpec`; the pre-pass requires an explicit
  `duration` when the field is set. The model keeps it optional so an adapter can build the
  IR incrementally.

- [x] **Step 1: Write the failing tests**

`tests/test_composition.py` — replace
`test_a_scene_may_not_declare_a_transition_the_emitter_ignores` with:

```python
def test_a_scene_may_declare_a_transition_with_a_duration():
    scene = {"id": "s1", "start": 0.0, "end": 4.0, "layers": [text_layer()]}
    built = CompositionScene(
        **scene,
        transition_in=MotionSpec(name="crossfade", duration=0.5),
    )
    assert built.transition_in is not None
    assert built.transition_in.duration == pytest.approx(0.5)
```

`tests/test_scene_transitions.py` — cover the rules listed in Step 2:

```python
def test_crossfade_fades_in_the_incoming_scene(tmp_path):
    comp = _two_scenes(tmp_path)
    out = apply_scene_transitions(comp)
    incoming = out.scenes[1].layers[0]
    assert incoming.motions[-1].name == "fade"
    assert incoming.motions[-1].duration == pytest.approx(0.5)

def test_crossfade_extends_and_fades_out_full_window_layers(tmp_path):
    comp = _two_scenes(tmp_path)
    out = apply_scene_transitions(comp)
    outgoing = out.scenes[0].layers[0]
    assert outgoing.time.end == pytest.approx(4.0 + 0.5)
    assert [m.name for m in outgoing.motions] == ["drift", "fade_out"]
```

- [x] **Step 2: Write the pre-pass**

`scene_transitions.py`:

```python
SUPPORTED_TRANSITIONS = frozenset({"crossfade", "fade"})

def apply_scene_transitions(composition: Composition) -> Composition:
    """Return a new Composition with every scene entrance emitted."""
```

Rules:

1. Iterate scenes in order. A scene without `transition_in` is copied unchanged.
2. Reject unknown `name` with the supported list; reject `duration is None` or `<= 0`; reject
   `duration > min(previous scene length, this scene length)`.
3. `crossfade`:
   - Add `MotionSpec(name="fade", duration=d, ease=...)` to **every** layer of the incoming
     scene. **Append** it to `motions`: the compiler emits `animation: a, b` and the later
     animation wins on a shared property, so prepending would let an authored `rise`/`pop`
     keep its own opacity ramp and cut the crossfade short. Appended, the fade owns opacity
     and the authored move keeps its transform.
   - For every layer of the **previous** scene whose `time.end is None` or
     `time.end >= previous.end - 1e-6`, extend `time.end` to `previous.end + d` and append
     `MotionSpec(name="fade_out", duration=d, delay=new_duration - d, ease=...)`.
     The extension cannot exceed `composition.duration`: `d` is bounded by the incoming
     scene's length and the previous scene ends where the incoming one starts. Assert that;
     do not clamp, which would hide a bad duration.
   - Layers that end before the scene end (captions, late pills) are **not** extended.
4. The returned scenes have `transition_in=None`, so the pre-pass is idempotent and the
   emitted IR records what actually happened.
5. Never mutate the input; build new `CompositionLayer`/`CompositionScene`/`Composition` via
   `model_copy(update=...)`.

- [x] **Step 3: Remove the rejection**

Delete `_transition_is_not_emitted` (`composition.py:143-154`). Do not add a replacement
validator that rejects the field; the pre-pass owns validation, because a model validator
cannot know the previous scene's length.

- [x] **Step 4: Run the slice tests**

```bash
uv run pytest tests/test_scene_transitions.py tests/test_composition.py -q
```

### Task 1.2: Add the `fade_out` primitive

**Files:**
- Modify: `packages/core/src/video_editor_core/motion/primitives.py`
- Test: `tests/test_motion.py` (or the existing primitive tests)

**Interfaces:** `@register("fade_out", default_duration=0.45)` returning
`keyframes: from {opacity:1} to {opacity:0}` and
`declaration: "<name> <duration>s <ease> forwards"`.

- [x] **Step 1: Add the primitive.** `forwards` is required: without it the layer snaps back
  to `opacity: 1` at the end of the animation and the outgoing scene reappears over the next
  one.
- [x] **Step 2: Test it in isolation.** Assert the keyframes direction and the `forwards`
  fill mode, and that `build_motion(..., clip_duration=1.0)` with `delay=0.5` emits the delay.
- [x] **Step 3: Confirm `build_motion` clamping still holds.** A `fade_out` whose duration plus
  delay exceeds the extended clip is clamped by the existing code; do not special-case it.

### Task 1.3: Wire the pre-pass into the compiler

**Files:**
- Modify: `packages/core/src/video_editor_core/compile_composition.py`
- Test: `tests/test_compile_composition.py`

- [x] **Step 1: Call the pre-pass once** at the top of `compile_composition`:

```python
from video_editor_core.scene_transitions import apply_scene_transitions

def compile_composition(composition, project_dir):
    composition = apply_scene_transitions(composition)
    ...
```

- [x] **Step 2: Add an HTML-level test.** Build two scenes, declare a crossfade, and assert:
  - the outgoing layer line carries `data-duration="4.500"` (or whatever the extended value is);
  - the outgoing `animation:` value contains the `fade_out` keyframes name;
  - the incoming layer's `animation:` value contains the `fade` keyframes name.
- [x] **Step 3: Keep the no-transition output byte-identical.** The Wiz scene-plan parity
  render is the guard; run it in the verification step.

### Task 1.4: Tests for the rejected shapes

- [x] Unknown name, missing duration, zero/negative duration, duration longer than the
  previous scene, and a transition on the first scene (allowed; it fades in from black).
- [x] A composition whose outgoing layer already ends early keeps its end and gets no
  `fade_out`.
- [x] A composition where the incoming scene has no layers still compiles (no-op).

**Acceptance:** `uv run pytest tests/test_scene_transitions.py tests/test_composition.py
tests/test_compile_composition.py -q` and
`uv run python scripts/parity_rebuild.py --case scene-plan` (SSIM must stay >= 0.9960).

---

## Slice 2 — Musical composer port (A3)

**Depends on:** slice 1. **Closes:** the open A3 item in `docs/ISSUES_BUGS.md`.

### Task 2.1: Media attributes in the compiler

**Files:** `compile_composition.py`, `tests/test_compile_composition.py`

- [x] **Step 1:** In `_layer_html`, for `kind == "media"`, append attributes from `style`:
  - `playback_rate` → `data-playback-rate="{value:.2f}"` (videos only; omit when `1.0`);
  - `media_start` → `data-media-start="{value:.3f}"` (omit when `0`);
  - keep the existing `loop` and `muted playsinline`.
- [x] **Step 2:** Document both attributes in the module docstring, with the reason
  (`data-playback-rate` is how the musical path gets 50% slow motion without an FFmpeg
  pre-pass; `data-media-start` is the trim).
- [x] **Step 3:** Test that an image layer never gets `data-playback-rate`, that a video layer
  does, and that the value is clamped to 0.1–10 with a `MotionError`-style build error rather
  than a silent clamp (the runtime clamps, but the IR should be honest).

### Task 2.2: Segment and window math

**Files:**
- Create: `packages/core/src/video_editor_core/musical.py`
- Test: `tests/test_musical.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class MusicalSource:
    path: Path
    kind: Literal["image", "video"]
    duration: float | None  # probed for video; None for image

@dataclass(frozen=True)
class MusicalSegment:
    source: MusicalSource
    start: float          # absolute final-timeline start
    end: float            # absolute final-timeline end
    visible: float        # end - start
    motion_index: int

def plan_segments(
    sources: Sequence[MusicalSource],
    music_duration: float,
    *,
    segment_seconds: float,
    transition_seconds: float,
) -> list[MusicalSegment]:
    ...
```

- [x] **Step 1:** Port `_build_filtergraph` / `_build_xfade_chain` math exactly:
  `N = max(1, ceil(music_duration / segment_seconds))`,
  `src[i] = min(visible[i] + extra[i], music_duration - i*segment_seconds)` with
  `extra[i] = transition_seconds if i < N-1 else 0`,
  `trans[i] = min(transition_seconds, prev_duration, src[i+1])`,
  `offset[i] = prev_duration - trans[i]`, `prev_duration = offset[i] + src[i+1]`.
  Scene windows are `[offset[i-1] or 0, offset[i]]` and the last ends at `prev_duration`.
- [x] **Step 2:** Validate `0 < transition_seconds < segment_seconds`, positive
  `segment_seconds`, positive `music_duration`, and a non-empty source list.
- [x] **Step 3:** Reuse the existing regression expectations from
  `tests/test_compose_musical_video.py:58-120` (non-clamped offsets, clamped intermediate,
  rejected zero transition) as table-driven tests for `plan_segments`.
- [x] **Step 4:** Assert the returned windows are contiguous and end at `music_duration`
  (or at the video's natural end, whichever is shorter; `-shortest` semantics).

### Task 2.3: Lyrics and Devanagari captions

**Files:**
- Create: `packages/core/src/video_editor_core/lyrics.py`
- Modify: `scripts/burn_musical_captions.py`,
  `packages/fablecut_bridge/src/video_editor_fablecut_bridge/lyrics.py`
- Modify: `packages/core/src/video_editor_core/fonts.py`
- Test: `tests/test_burn_musical_captions.py`, `tests/test_lyrics.py`

- [x] **Step 1:** Move `parse_lyrics` and `FOLDABLE_HEADERS` verbatim into `lyrics.py`. Leave
  the two existing modules importing from core so their tests and callers do not change
  behaviour. This removes the third copy that would otherwise be written for the adapter.
- [x] **Step 2:** Add `Noto Sans Devanagari` to `BUNDLED_FONTS`. The shipped file is a
  ~57 KB WOFF2 subset of the whole U+0900–U+097F block (not a per-video glyph set), so no
  glyph manifest is needed; `font_face_css` already emits `format('woff2')`. The OFL-1.1
  licence sits beside the other `OFL-*.txt` files.
- [x] **Step 3: Resolved — not needed.** The WOFF2 subset is bundled, so there is no
  `font_file` fallback to build. Do not rely on a host font: the script did, and that was
  the reason to bundle.

### Task 2.4: The adapter and pipeline

**Files:**
- Create: `packages/core/src/video_editor_core/adapters/from_musical.py`
- Create: `packages/pipelines/src/video_editor_pipelines/musical_pipeline.py`
- Modify: `scripts/compose_musical_video.py`
- Test: `tests/test_from_musical.py`

**Interfaces:** `from_musical(segments, lyrics, *, job_id, canvas) -> Composition`
(the canvas carries the fps).

- [x] **Step 1: Scenes.** One scene per segment at the planned windows. Scene ids
  `segment_0…`, `transition_in=MotionSpec(name="crossfade", duration=transition_seconds)`
  on every scene after the first.
- [x] **Step 2: Image layers.** `drift` with the script's halved amplitude: alternate
  `from scale(1.0) to scale(1.04)` and the reverse, with the script's pan pairs. Do not reuse
  the pack's `drift` default (1.12); the musical look is deliberately calmer.
- [x] **Step 3: Video layers.** `media_kind="video"`, `style={"object_fit": "cover",
  "playback_rate": 0.5, "media_start": 0.0}`. The script trims the source to half the segment
  and stretches it to 2×; `data-playback-rate` expresses the same thing without a pre-pass.
  If a source is shorter than `visible * 0.5`, the script freezes the last frame (`tpad`).
  HyperFrames holds at the end of the media; verify this on the parity fixture before adding
  a pre-pass. If it does not hold, add `style["preprocess"] = "slow-mo-pad"` and a small
  FFmpeg helper, not a fork of the renderer.
- [x] **Step 4: Captions.** Equal windows across `duration`, `preset="caption_line"`,
  `pop` motion (0.35 s), white text, `background: rgba(0,0,0,0.55)`, lower-bottom placement
  `y = height - 2*text_height - 50` with the same padding as the script. Add a helper
  `musical_caption_layers(...)` in the adapter; do not reuse the karaoke `rich_text` spans,
  which are word-timed and have per-word reveal semantics the musical path does not want.
- [x] **Step 5: Audio.** `audio_mode="external"`; the pipeline muxes the music as the sole
  track and runs `normalize_loudness`. Do not embed it, so the existing loudness gate and
  ducking helpers keep working.
- [x] **Step 6: Pipeline.** `ve musical --music --media-dir --lyrics --output` mirrors the
  script's flags, probes media, plans segments, builds the Composition, compiles, renders,
  muxes and normalises. Reuse `render_project` from `hyperframes_cli`.
- [ ] **Step 7: Keep the scripts as shims** for one release (deferred: they are the parity
  reference; removing them and their path-based tests is the follow-up): `compose_musical_video.py` and
  `burn_musical_captions.py` delegate to the adapter/pipeline so existing invocations and
  tests continue to pass. Their pure filtergraph tests move to `tests/test_musical.py` or are
  deleted with the code they tested.

### Task 2.5: A musical parity case

**Files:** `scripts/parity_rebuild.py`, `tests/test_musical_parity.py`

- [x] **Step 1:** Add fixtures under `tests/fixtures/musical/` (two stills, one short video,
  one music file, one lyrics file). Keep them small (a few hundred KB total); the reference
  video is generated, not committed.
- [x] **Step 2:** Add `--case musical`. It runs the script into `qa/parity/reference/` and the
  IR port into `qa/parity/musical/`, then compares:
  - SSIM >= 0.95 on each scene midpoint;
  - SSIM >= 0.95 on each boundary midpoint (±100 ms around the xfade centre);
  - scene window boundaries within one frame;
  - output duration within 0.1 s.
- [x] **Step 3:** Mark the case `slow` in pytest so the default suite stays fast, but keep it
  runnable by `scripts/parity_rebuild.py`.

**Acceptance:** `uv run python scripts/parity_rebuild.py --case musical` passes; the A3 item
in `docs/ISSUES_BUGS.md` is checked off with the measured numbers.

---

## Slice 3 — MCP spend caps

**Depends on:** nothing.

### Task 3.1: Cap `sfx_placement`

**Files:**
- Modify: `packages/skills/src/sfx_placement/sfx_placement.py`
- Modify: `packages/mcp_server/src/video_editor_mcp/tools/vlog_skills.py`
- Modify: `packages/pipelines/src/video_editor_pipelines/pipeline.py`
- Test: `tests/test_mcp_new_tools.py`, `tests/test_sfx_placement.py`

- [x] **Step 1:** Give `SfxPlacementSkill.__init__` an optional
  `manager: AssetIngestionManager | None`. When present, generate each SFX through
  `manager` with an `AssetRequest(asset_type="sfx", …)`; when absent, keep the direct
  `ElevenLabsSFXProvider` path used by the offline tests.
- [x] **Step 2:** In `_handle_sfx_placement`, build
  `AssetIngestionManager(ctx, config=Settings.load().config.assets, budget=job_budget(arguments.get("budget")))`
  and pass it, exactly as `_handle_broll` does (`vlog_skills.py:369-383`). Keep `provider` and
  `output_name` handling.
- [x] **Step 3:** Pass the pipeline's manager in `_run_sfx_placement` (`pipeline.py:510-520`)
  so the CLI path is capped too.
- [x] **Step 4:** Add a test that the MCP handler constructs a manager whose governor is
  present, and a test that the skill routes through it when given one.

### Task 3.2: Retire the `dubbing` MCP tool

**Files:**
- Modify: `packages/mcp_server/src/video_editor_mcp/tools/vlog_skills.py`
- Modify: `tests/test_mcp_new_tools.py`, `tests/test_mcp_server.py`
- Modify: `docs/ISSUES_BUGS.md`

- [x] **Step 1:** Delete `_handle_dubbing`, its dispatch branch, its schema entry, and the
  `dubbing` entries in the tool-name lists and fake-skill fixtures. Keep
  `packages/skills/src/dubbing` and `tests/test_dubbing.py` — the skill is a usable library,
  just not a working MCP tool.
- [x] **Step 2:** Record why in `docs/ISSUES_BUGS.md`: the tool had no translator, no TTS
  provider and no budget, so every call failed before spending; a working dubbing feature
  needs a translator decision first and is a separate slice.
- [x] **Step 3:** If the user prefers to keep the tool, the alternative is a real wiring task:
  add a `translator` implementation (no translation provider exists in `packages/` today),
  wire `ElevenLabs` TTS as the `tts_provider`, pass `job_budget`, and add an estimate to the
  result. Do this only with an explicit product decision; do not fake it with a null
  translator.

**Acceptance:** `uv run pytest tests/test_mcp_new_tools.py tests/test_mcp_server.py
tests/test_sfx_placement.py -q`, and a manual `job_budget` assertion that the SFX path refuses
above the cap.

---

## Slice 4 — Film-look effects

**Depends on:** nothing.

### Task 4.1: A `Grade` token

**Files:**
- Modify: `packages/core/src/video_editor_core/designs.py`
- Test: `tests/test_designs.py`, `tests/test_grade.py`

**Interfaces:**

```python
class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str = ""                 # neutral, warm-daylight, vintage-wash, ...
    intensity: float = Field(1.0, ge=0.0, le=1.0)
    adjust: dict[str, float] = Field(default_factory=dict)
    details: dict[str, float] = Field(default_factory=dict)  # vignette, grain, ...
    effects: dict[str, float] = Field(default_factory=dict)
    lut: str = ""                    # path to a .cube file
    lut_intensity: float = Field(1.0, ge=0.0, le=1.0)

class DesignTokens(BaseModel):
    ...
    grade: Grade | None = None
```

- [x] **Step 1:** Validate `preset` against the runtime's known list (read it from
  `hyperframes media-treatment --capability=presets --json` during development, then hard-code
  the list; do not shell out at compile time). Reject unknown keys in `adjust` / `details` /
  `effects` with the key names, matching the CLI's error style.
- [x] **Step 2:** Allow a per-layer override in `CompositionLayer.style["grade"]` (a dict) for
  a single scene; the design is the default.

### Task 4.2: Emit `data-color-grading`

**Files:** `compile_composition.py`, `project_assets.py`, `tests/test_compile_composition.py`

- [x] **Step 1:** Build the sparse payload once:

```python
payload = {"preset": grade.preset or None, "intensity": grade.intensity,
           "adjust": grade.adjust, "details": grade.details, "effects": grade.effects}
if grade.lut:
    rel = copier.copy(grade.lut)
    payload["lut"] = {"src": rel, "intensity": grade.lut_intensity}
```

- [x] **Step 2:** Emit it with `html_escape(json.dumps(payload, separators=(",", ":")),
  quote=True)` as ` data-color-grading="…"` on `media` layers only (the runtime only grades
  `img`/`video`). Do not emit an empty payload.
- [x] **Step 3:** Confirm the runtime merges sparse sections: it does (`ml` merges
  `adjust`/`details`/`effects` over `Wd`/`Vd`/`$d`). Keep the payload sparse so a design only
  states what it changes.
- [x] **Step 4:** Test: a design with `{"details": {"vignette": 0.12, "grain": 0.1}}` produces
  one `data-color-grading` attribute on each base media layer; a `.cube` is copied into
  `assets/` and its `src` is `assets/<name>`; a design with no grade emits no attribute.
- [x] **Step 5:** Run `hyperframes lint` and `hyperframes validate` on the generated project in
  the test (or in CI) to prove the attribute is accepted by 0.8.208.

### Task 4.3: Light leak and the "two per video" rule

**Files:** `packages/core/src/video_editor_core/layer_presets.py` or a new
`film_look.py`; `compile_composition.py`

- [x] **Step 1:** `LightLeak` is a design token (`placement`, `intensity`, `duration`,
  `color`). The adapter emits a full-canvas `html` layer at the first cut, the last content cut,
  or both; the gradient and a finite `opacity`/`transform` keyframe live in `extra_css`, so the
  overlay is seek-safe. `placements` dedupe when both names the same cut.
- [x] **Step 2:** `compile_composition` warns above two flourish layers (registry advice), and
  the composition QA report carries a `flourishes` check that always passes and states the
  count. Tested with three leaks.
- [x] **Step 3:** The grade check is fail-closed at compile time: `Grade` rejects unknown
  controls and a named `.cube` must exist before the attribute is emitted. The existing tests
  cover the attribute payload; the visual gate still catches blank frames.

**Acceptance:** a generated project from `designs/saju_series.json` with a grade block passes
`hyperframes check`, and the parity renders stay within their SSIM thresholds (grading changes
every frame, so do not add a grade to the parity designs — use a new test design).

---

## Slice 5 — Sound design

**Depends on:** slice 1 for transition cues; otherwise independent. The earshot trial itself
is external and spend-free.

### Task 5.1: Cue table from the Composition

**Files:** `packages/core/src/video_editor_core/sfx_cues.py`, `tests/test_sfx_cues.py`

**Interfaces:**

```python
class SfxCue(BaseModel):
    id: str
    sound: str          # bank key, e.g. "hit", "click", "card_flip", "whoosh"
    start: float
    volume: float = 1.0
    layer_id: str = ""

DEFAULT_TABLE: dict[str, str] = {
    "stamp": "hit", "pop": "click", "flip": "card_flip", "lock": "latch",
}

def cues_from_composition(comp, *, table=DEFAULT_TABLE, max_cues=12,
                          min_gap=0.05) -> list[SfxCue]: ...
```

- [x] **Step 1:** Emit one cue per **entrance** motion in the table, at
  `layer.time.start + motion.delay`. Include `scene.transition_in` as a `whoosh` at
  `scene.start` when the pre-pass has not already cleared it (run this before
  `apply_scene_transitions`, or read the extended IR and use the scene start).
- [x] **Step 2:** Do **not** map `rise` by default. Every chip, pill and end card in a pack
  gets a `rise` (`from_content_pack.py:145-152, 217, 245, 257`), so a blanket mapping would
  whoosh on every text element. Offer `rise` only for scene transitions.
- [x] **Step 3:** Dedupe cues within `min_gap`, drop cues past `composition.duration`, and cap
  at `max_cues` by earliest-first (or by a priority table: hits > flips > whooshes).
- [x] **Step 4:** Tests: a composition with stamp/pop/flip yields three cues at the right
  times (including `delay`), a wall of `rise` yields none, dedupe works, and the cap is
  deterministic.

### Task 5.2: SFX providers and licensing

**Source decision (2026-10-28):** Pixabay is **not** an automated source. Its public API
documents only images (`/api/`) and videos (`/api/videos/`); there is no audio endpoint, and
its Terms of Service forbid scraping, data extraction and bulk/systematic copying without
permission. The Content License itself is a good fit for our use (commercial and
modification allowed, no attribution required; standalone redistribution prohibited, but a
sound embedded under a video is not standalone). Treat Pixabay as a **manual import**: a
human chooses sounds and drops them into `assets/sfx/` with a manifest; the engine only reads
local files. For the automated free tier, use **Freesound** (APIv2 has a licence filter for
CC0/CC-BY and preview downloads that do not require OAuth2) and keep Openverse as a fallback
index if Freesound's coverage is thin. Keep earshot/ElevenLabs for the cue-table's exact
sounds; stock is the cheap tier for generic whooshes, not a replacement for bespoke hits.

**Unsplash is not a no-attribution substitute either.** Its API Guidelines require hotlinked
image URLs, a call to the `download_location` endpoint whenever a photo is taken, and
attribution to Unsplash and the photographer with `utm` links; they also require
"non-automated, high-quality, and authentic experiences". That conflicts with an agent-driven
render pipeline. The Unsplash License for a downloaded photo does not require attribution,
but API use does. If a stock-photo fallback is ever added, it must be an explicit, opt-in
provider with that bookkeeping — not an MCP and not the default for a generated-style series.

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/openverse_sfx.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/earshot_sfx.py`
- Create: `packages/skills/src/sfx_placement/bank.py`
- Test: `tests/test_openverse_sfx.py`, `tests/test_earshot_sfx.py`

- [x] **Step 1: Freesound (primary) and Openverse (fallback).**
  - Freesound: query `https://freesound.org/apiv2/search/text/?query=<sound>&filter=license:("Creative Commons 0" OR "Attribution")&token=…`.
    Preview URLs (OGG/MP3) need no OAuth2; original-quality downloads do, so use previews for
    short SFX. Record `name`, `username`, `license`, `sound_url` per chosen sound.
  - Openverse: `https://api.openverse.org/v1/audio/?q=<sound>&license_type=commercial,modification`
    when Freesound has no match. Accept only CC0, Public Domain Mark and CC BY; reject NC, ND
    and (by default) SA. Record `title`, `creator`, `source_url`, `license`, `license_url`.
  - Emit `credits.txt` beside the output, formatted for a video description. Freesound CC-BY
    sounds need attribution; CC0 sounds do not.
- [x] **Step 2: Pixabay is manual only.** Do not call, scrape or automate Pixabay. Support a
  local `assets/sfx/` folder plus a manifest for hand-picked Pixabay files; record
  "Pixabay Content License" and the page URL in the manifest. Never bulk-download.
- [x] **Step 3: earshot.** The CLI-backed `earshot_sfx` provider runs `earshot sfx render`
  (the `sfx` command loads no CLAP/AST models) from Earshot's pinned environment, configured by
  `provider_options.command`; results go through the asset manager's cache. Listening commands
  still use Earshot's MCP/Python API. Install steps and bank ids: `docs/EARSHOT_INTEGRATION.md`.
- [x] **Step 4: ElevenLabs.** Keep the existing provider as the generated fallback; it already
  writes an MP3 and is already priced.
- [x] **Step 5: Selection.**  `SfxBank` takes an ordered resolver list; `manager_resolver`
  builds the provider chain (the shipped short path uses Freesound -> Openverse). `bank.resolve(sound, *, provider_order=("earshot", "elevenlabs",
  "freesound", "openverse"))` returns a `Path` plus a `LicenceRecord`. Never silently
  substitute a different sound; a missing bank entry is an error the caller can choose to
  ignore. Local hand-picked files win over any network provider when present.

### Task 5.3: One mix pass with ducking

**Files:** `packages/ffmpeg/src/video_editor_ffmpeg/mix.py`, `tests/test_sfx_mix.py`

- [x] **Step 1:** Add `mix_voice_music_sfx(video, voice, music, sfx_timeline, output, …)` that
  does: normalise/duck music against the voice (`sidechaincompress` as in
  `packages/skills/src/music/mix.py:24-66`), then `amix` the SFX at `sfx_gain_db` (default
  -8 dB), then `loudnorm`. If one filtergraph is too fragile, ship the two-pass fallback
  (`mix_background_music` then `mix_sfx_into_video`) and document the quality trade-off.
- [x] **Step 2:** Keep the existing `mux_voiceover` behaviour for `ve short` when no music/SFX
  are present, so the short path is unchanged by default.
- [x] **Step 3:** Wire the short/explainer pipelines: build the cue list from the Composition,
  resolve the sounds, and call the new mix helper after render. Keep it opt-in per pack
  (`sfx: auto|off`) and record the resolved cues in the manifest.

### Task 5.4: Sync and level QA

- [x] **Step 1:** One `hyperframes audio --json` pass per file provides the onsets and the
  envelope; `CueMeasurement` finds the nearest onset and applies ±80 ms for hits and ±120 ms
  for whooshes (per-range calls would repeat the same decode). `SfxReport.ok` carries the
  result, and `ve short` records it in the QA report (advisory, so a miss is visible).
- [x] **Step 2:** Assert the SFX bus sits below the voice (global LUFS ceiling +2; per-cue levels are review columns): measure integrated LUFS of the SFX
  in its window and compare with the voice window; fail if the SFX exceeds the voice by more
  than a configured margin.
- [x] **Step 3:** `scripts/earshot_trial.py` runs `measure`, `timeline` and `critique` on one
  film and writes their JSON; it reads the command from the asset provider config (or
  `EARSHOT_COMMAND`) and stays out of the unit-test suite. `critique` is advice only.
- [x] **Step 4:** Ran the no-spend trial on
  `videos/2026-10-27/day-master-deck/day_master_deck.mp4` (73.0 s). The cut detector matched all
  12 scene boundaries within a frame; 67% of cuts had an onset within 100 ms, `27.60` had none.
  Metrics and interpretation: `../tool-lab/trials/2026-10-28-earshot/README.md`.

**Acceptance:** a short with stamp/pop/flip motions gets cues at the right times, the mix
passes the loudness gate, and the sync/level checks pass on the trial video.

---

## Slice 6 — 2.5D layer separation

**Depends on:** nothing in code; the integration needs the slice 7 `huggingface-skills` plugin
for model downloads and VRAM fitting. The spike needs the RTX 3050, so it cannot run on a
machine without it.

### Task 6.1: The spike (about one day, isolated environment)

**Files:** `scripts/spikes/layerize_spike.py`, `docs/archive/plans/LAYERIZE_SPIKE_2026-10-28.md`

- [x] **Step 1:** Installed Depth Anything 3 at `3d835ec` in `~/tools/da3-venv` and ran it on
  two DA3 example photos and a robot-video frame. The repo has no `__init__.py`, so it runs
  from `PYTHONPATH=src` rather than `pip install -e .`.
- [x] **Step 2:** DA3MONO-LARGE beats DA3-SMALL/BASE and DA V2 Small on edge alignment for the
  photos (0.69–0.73 vs 0.44–0.49 and 0.60–0.61) and is smoother in flat regions; at 1280 it
  peaks at 2343 MiB. Full table in the report.
- [x] **Step 3:** BiRefNet (bf16) and BEN2 both fit; mask IoU 0.91–0.98. BiRefNet fp32 OOMs at
  1024, so bf16 is required (3.06 GB peak). BEN2 is lighter (2.39 GB) with softer edges.
- [x] **Step 4:** LaMa fills the subject region in 0.5–2.8 s at 0.65–0.83 GB peak; the plate
  changes only inside the mask.
- [x] **Step 5:** Report written with pinned versions, the stage-by-stage VRAM budget, the
  Ollama contention caveat and a recommendation. Human review of edges is still pending
  because the local vision model is unavailable.

### Task 6.2: Opt-in pack field and adapter

**Files:** `content_pack.py`, `adapters/from_content_pack.py`, `tests/test_content_pack.py`

- [x] **Step 1:** `PackScene.layers: Literal["off", "auto"] = "off"`.
- [x] **Step 2:** The adapter expects `<key>__bg`, `<key>__fg` and `<key>__depth` and emits the
  plate at z=0 with `parallax` rate 0.35 and the cut-out at z=5 with rate 1.0. Reused stills
  share the pair, and the scrim/chip/pill z bands are unchanged. A `layers: auto` composition
  compiles and passes `hyperframes check` with real layerized assets.
- [x] **Step 3:** A missing derived key fails with `scene 'hook' asks for layers but still
  'rat__depth' is missing`; tested for all three keys.

### Task 6.3: The `layerize` stage and cache

**Files:** `packages/core/src/video_editor_core/layerize.py`,
`packages/pipelines/src/video_editor_pipelines/short_pipeline.py`, `tests/test_layerize.py`

- [x] **Step 1:** `layerize_still(...) -> LayerizeResult` writes `depth.png`, `fg.png` and
  `bg.png` in a cache dir keyed by `sha256(source bytes) + model + process_res + cutout`;
  a second call is a cache hit without running the CLI.
- [x] **Step 2:** The stage runs `layerize.command` with `run_subprocess` (timeout, captured
  stderr). The engine imports no torch; a failure becomes `ShortPipelineError` with the last
  stderr lines. The command is `scripts/spikes/layerize_spike.py` in `~/tools/da3-venv`.
- [x] **Step 3:** `short_pipeline` calls `layerize_stills` right after `generate_stills` when
  any scene asks for layers, records the paths and cache hit in the manifest, and the adapter
  receives all three keys. Verified live on a real still (depth + fg + plate).
- [ ] **Step 4:** The cheaper path (a pack-supplied background plate, no depth model) is not
  built yet; `layers: auto` still requires all three assets.

### Task 6.4: `parallax` primitive and QA

**Files:** `motion/primitives.py`, `tests/test_motion.py`, `tests/test_composition_qa.py`

- [x] **Step 1:** `parallax` is registered with no default duration; `rate` is the half-travel
  in percent, layers are scaled 1.04 so the pan cannot expose an edge, and `from`/`to`
  override the pair. Percentage-based, so it is resolution independent.
- [x] **Step 2:** The adapter gives the plate 0.35 and the cut-out 1.0 without a depth mask;
  with the layerize CLI's depth-derived `near.png` it splits the background into a far plate
  (0.2) and a masked near copy (0.6). The primitive does not know the depth; the mask does.
- [x] **Step 3 (frozen frames):** the `layers` composition gate samples two frames 0.4 s
  apart in every parallax scene and fails below a mean luma difference of 0.5 (the real smoke
  render measures 0.79). A failed sample fails the gate instead of passing. The 1:1 edge crops
  were reviewed on 2026-10-28 and showed no halo, so no halo check is planned.
- [x] **Step 4:** DepthFlow is not adopted. The spike passed, so `layers: auto` stays available
  and `layers: off` remains the default.

**Acceptance:** `layers: off` produces byte-identical output to today; one fixture with
`layers: auto` renders, the parallax frames differ, and the QA gate passes.

---

## Slice 7 — Tooling and doc hygiene

**Depends on:** nothing. Do this last so it records the shipped state.

### Task 7.1: Project skill

**Files:** `.claude/skills/video-editor/SKILL.md`

- [x] **Step 1:** Encode the build loop: `ve short` / `ve explain` / `ve musical`, the QA gates,
  the cost approval step, the `videos/<date>/<project>/` layout, and the rule that parity
  thresholds are never adjusted to make a render pass.
- [x] **Step 2:** Add the failure remedies the repo already documents: FFmpeg filters,
  HyperFrames version, Chrome, `agy` quota, and the `VE_*` env vars.

### Task 7.2: Correct the hand-off docs

- [x] **Step 1:** `docs/ISSUES_BUGS.md` is stale: #261, #263 and #264 are already fixed in git.
  Replace the open list with a pointer to `docs/ISSUES_BUGS.md` and this plan.
- [x] **Step 2:** `docs/ISSUES_BUGS.md`:
  - correct "MCP `dubbing` is the last uncapped tool" to name `sfx_placement` as the live
    spender, and close it with slice 3;
  - close the dubbing item as retired (or leave it open with the wiring scope);
  - note that the A3 musical verification needs the new `musical` parity case;
  - check off A3 once slice 2 lands.
- [x] **Step 3:** Add this plan to `docs/README.md`'s planning table if it is kept after the
  work lands; otherwise archive it under `docs/archive/plans/` per the existing convention.

### Task 7.3: Plugins (user-local, not a repo change)

- [x] **Step 1:** Recommend enabling `huggingface-skills`, `mcp-server-dev`, `ffmpeg-llm` and
  `image-generation`; note that `huggingface-skills` is required for slice 6's model
  downloads and VRAM fitting.
- [x] **Step 2:** Recommend disabling Vercel, Expo, Supabase and data-engineering plugins to
  cut per-session context. Document the list; do not commit a machine-local config.

---

## Dependency Graph

```
Slice 1 (transitions) ──> Slice 2 (musical port)
Slice 3 (MCP caps)        independent
Slice 4 (film look)       independent
Slice 5 (sound)           needs slice 1 only for transition cues
Slice 6 (2.5D)            independent in code; spike needs slice 7's plugin
Slice 7 (tooling/docs)    last, records the shipped state
```

## Verification

Run after every slice:

```bash
uv run pytest -n 8 -m "not integration and not slow"
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run lint-imports
```

Run after slices 1 and 2:

```bash
uv run python scripts/parity_rebuild.py --case scene-plan
uv run python scripts/parity_rebuild.py --case render-graph
uv run python scripts/parity_rebuild.py --case musical   # new in slice 2
```

Run before closing the A3 item:

```bash
VE_AGY_INTEGRATION=1 uv run pytest -m integration tests/test_agy_image_integration.py -v
```

(the `agy` leg stays blocked until the quota resets; that is external.)

## Risks and Open Questions

| Risk | Mitigation |
| --- | --- |
| Shader transitions cannot take our layers | Ship crossfade; CSS approximations later; do not promise registry parity |
| A crossfade's outgoing fade-out runs past its clip | Extend the layer end before adding the motion; `build_motion` clamps to the extended clip. Unit-test the order |
| The musical port's held last frame differs from `tpad` | Verify on the parity fixture; fall back to a slowed/padded pre-pass, not a renderer change |
| Devanagari subset licence/size | Use an OFL subset + glyph manifest; fallback is a copied `font_file` |
| earshot v0.4 is young | Pin it in its own env, call over MCP with timeouts, cache the bank |
| Openverse is an index, not a licensor | Record title/creator/source/licence per sound and emit `credits.txt` |
| 2.5D models exceed 4 GB VRAM | Spike first; if it fails, keep `layers: off` and record the result |
| A transition changes the parity frames | The pre-pass is a no-op when no scene declares `transition_in`; the Wiz/Saju cases must stay byte-identical or SSIM-identical |
| MCP `dubbing` retirement surprises a caller | Remove the schema and handler together; keep the skill importable and documented |

**Open question for the owner:** for `dubbing`, retire the MCP tool (recommended, because no
translator exists) or fund a translator/TTS wiring slice. This plan assumes retirement until
told otherwise.
