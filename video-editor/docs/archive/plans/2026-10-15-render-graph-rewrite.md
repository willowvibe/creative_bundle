# Render-Graph Rewrite (P1) — Implementation Plan

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax. Execute milestone by milestone; keep `uv run pytest`, `uv run ruff check .`, and `uv run mypy` green after every milestone.

**Goal:** Replace the chain of per-stage MP4 renders with a single **render graph** compiled to **one** HyperFrames composition (FFmpeg fallback), cutting a polished job from 4–9 encodes to 1–2 and making skills *plan contributors* instead of renderers.

**Architecture:** `video_editor_core.render_graph` defines `RenderGraph` (canvas + cuts + overlay layers + audio buses + options). `compile_ffmpeg(graph)` emits one `filter_complex`; `compile_hyperframes(graph)` emits one whole-timeline composition plan. A `Renderer` picks the engine. Skills produce graph fragments. See `docs/archive/audits/AUDIT_2026-10-15.md`.

**Tech Stack:** Python 3.12, Pydantic v2, structlog, FFmpeg 6.1+ (NVENC), HyperFrames CLI, uv workspace.

**Companion:** `docs/archive/audits/AUDIT_2026-10-15.md`.

## Global Constraints

- **HyperFrames is the renderer**; FFmpeg is the compile target + fallback (no Remotion).
- **Keep FableCut** (`ve preview` / `ve finalize`, `packages/fablecut_bridge`).
- **GPU (4 GB):** ASR (`faster-whisper large-v3-turbo`) + NVENC only; no resident vision models; serialise GPU work.
- **Cloud generation first** (PoYo / FAL / ElevenLabs / Sarvam); local is fallback.
- **No behaviour regressions:** the full test suite stays green; CLI surface (`ve run`, `ve graphics`, `ve captions`, `ve music`, `ve export`, `ve export-fablecut`) keeps working.
- Every FFmpeg invocation goes through `packages/ffmpeg/.../run.py` (timeout + stderr capture).
- New code is fully typed (`mypy --strict`) and linted (`ruff`).

## Acceptance targets

- ≤2 encodes per job (1 for the common case).
- One HyperFrames composition per job (not one per segment).
- ASR defaults to `large-v3-turbo` on CUDA with a `small`/CPU fallback.
- `uv run pytest -m "not integration and not slow"` green; `ruff` + `mypy` clean.

## Status (2026-10-16)

| Milestone | Status |
|---|---|
| M1 — render graph model + FFmpeg compiler | ✅ done (21 tests) |
| M2 — HyperFrames compiler + Renderer | 🟡 JSON-plan compiler retained (legacy); HTML renderer implemented via M2b |
| M2b — HyperFrames **HTML composition** compiler | ✅ done — `hyperframes_project.py` writes an HTML project and `hyperframes render <dir>` renders it; `--render-engine hyperframes` / `auto` selects it. Default stays `ffmpeg` (measured ~18× faster for simple overlay comps — see audit §4.5) |
| M3 — migrate skills & pipeline to the render graph | 🟢 graphics+captions+music render in one pass and are the default (legacy via `--no-render-graph`); b-roll runs before the graph (over `base_video`), so it composites under the single render. Not folded: `rough_cut`'s cut (still one encode producing `locked.mp4`, which `ve preview`/FableCut consume) |
| M4 — GPU: ASR + NVENC + scheduler | 🟡 mostly done (settings resolver + `GpuScheduler` + `hwaccel`; NVENC still opt-in, not the default encoder) |
| M5 — single source of truth (models.yaml + codegen) | 🟢 `models.yaml` holds costs + LLM routing + estimate defaults + template tiers; `pricing.yaml` is generated; a driftguard test keeps the inline tables in sync |
| M6 — retire redundancy | 🟢 Remotion stack, WhisperX wrapper, and the v1 `RawFootagePipeline` (its 7 commands + worker handlers) all deleted |
| M7 — perf guardrails & CI | 🟢 1,3,4,5 done (hash memo; compiler flags; CI + xdist; benchmark workflow); 2 moot (per-segment renders gone) |

Test suite after M1/M2/M2b/M3/M4/M5/M6.2/M7: **1508 passed, 8 deselected**; `ruff` + `mypy apps packages` clean.

## File structure

| File | Responsibility |
|---|---|
| `packages/core/src/video_editor_core/render_graph.py` (create) | `RenderGraph`, `Cut`, `Layer`, `AudioTrack`, `RenderOptions` wiring |
| `packages/core/src/video_editor_core/compile_ffmpeg.py` (create) | `compile_ffmpeg(graph) -> Command` (single filter_complex) |
| `packages/core/src/video_editor_core/compile_hyperframes.py` (create) | `compile_hyperframes(graph) -> dict` (whole-timeline composition) |
| `packages/core/src/video_editor_core/renderer.py` (create) | `Renderer` protocol, `HyperFramesRenderer`, `FFmpegRenderer`, `render(graph)` |
| `packages/core/src/video_editor_core/gpu.py` (create) | `GpuScheduler` (serialise ASR / ComfyUI), NVENC helpers |
| `packages/core/src/video_editor_core/render_cache.py` (modify) | memoised file hash |
| `apps/cli/src/video_editor_cli/pipeline.py` (modify) | one render graph; contributors instead of per-stage renders |
| `skills/graphics`, `skills/captions`, `skills/music`, `skills/sfx_placement`, `skills/broll`, `skills/export` (modify) | emit graph fragments |
| `apps/transcribe`, `packages/tools/whisperx_wrapper.py` (modify/delete) | single CUDA-aware ASR module |
| `models.yaml` + `apps/cli/.../commands/gen_models.py` (create) | single model/cost source + codegen |
| `tests/test_render_graph.py`, `tests/test_compile_ffmpeg.py`, `tests/test_compile_hyperframes.py`, `tests/test_renderer.py` (create) | coverage |

---

## M1 — Render graph model + FFmpeg compiler

**Files:** create `render_graph.py`, `compile_ffmpeg.py`, `tests/test_render_graph.py`, `tests/test_compile_ffmpeg.py`; modify `packages/core/src/video_editor_core/__init__.py`.

**Interfaces (produced):**
- `RenderGraph(job_id, width, height, fps, duration, source, cuts, overlays, audio, options)`
- `Cut(source_start, source_end, timeline_start, timeline_end, label)`
- `Layer(kind, name, rect, timing, style, source, text)`
- `AudioTrack(kind, source, start, gain_db, duck, loop)`
- `compile_ffmpeg(graph) -> Command` where `Command = list[list[str]]` (one argv per pass)

- [x] **Step 1:** Write `render_graph.py` with Pydantic models + validators (positive canvas, cuts sorted/non-overlapping, timeline continuity).
- [x] **Step 2:** Write failing tests in `test_render_graph.py` (validation + `edl_to_graph` helper).
- [x] **Step 3:** Implement `compile_ffmpeg.py`: trim/concat `filter_complex`, scale/pad, text/image/logo overlays with `enable=between(t,...)`, audio `amix`/`sidechaincompress` (music ducked under speech), `loudnorm`; returns argv.
- [x] **Step 4:** Tests: a 2-cut graph compiles to one argv with `-filter_complex`; a no-cut graph compiles to a single `-vf`; overlay timing appears in the filter string; audio ducking present only when music + speech.
- [x] **Step 5:** Export from `video_editor_core/__init__.py`; run ruff/mypy/pytest.

## M2 — HyperFrames compiler + Renderer

**Files:** create `compile_hyperframes.py`, `renderer.py`, `tests/test_compile_hyperframes.py`, `tests/test_renderer.py`.

- [x] **Step 1:** `compile_hyperframes(graph) -> dict` — one composition: `{width,height,fps,duration,layers:[…]}` with a base `video` layer + timed overlay layers (reuse `lint_plan` contract). No per-segment split.
- [x] **Step 2:** `Renderer` protocol + `HyperFramesRenderer` (materialise the cut via `compile_ffmpeg` pass 0, then one HyperFrames composition render, then audio mux) + `FFmpegRenderer` (single pass).
- [x] **Step 3:** `render(graph, output)` selects engine (`hyperframes_available()`), validates output (non-zero, probeable, non-zero duration — reuse `hyperframes_cli` checks).
- [x] **Step 4:** Tests with `hyperframes` mocked/absent (FFmpeg path) + a compile-shape test for the composition.

## M2b — HyperFrames HTML composition compiler (discovered blocker)

The installed HyperFrames v0.7.42 renders **HTML compositions** (`hyperframes render [DIR]`),
not JSON plans. The repo's `hyperframes_cli.render_segment` targets a `--input plan.json`
interface the CLI rejects. Until this lands, `render_graph` defaults to `engine="ffmpeg"`
and `auto` falls back to FFmpeg. See audit §4.5.

- [x] **Step 1:** Emit an HTML composition (project dir with `index.html`, `data-fps`,
  `data-composition-*`, and one timed layer element per graph overlay) from `RenderGraph`.
- [x] **Step 2:** Rewrite `hyperframes_cli.render_segment` to `hyperframes render <dir> -o <out>`
  (with `--gpu` where a GPU exists) and validate the artifact as today. *(added
  `render_project`; `render_segment` kept for the legacy JSON plan)*
- [x] **Step 3:** Map the render graph audio buses onto the composition's music/audio blocks,
  or keep the FFmpeg audio mux. *(kept the FFmpeg mux: HyperFrames rejects a video file
  used as `<audio>`, and ducking/loudnorm live in the FFmpeg audio chain)*
- [ ] **Step 4:** Flip `render_graph` default back to HyperFrames once the HTML path is proven;
  keep FFmpeg as the fallback. *(deferred: HTML render measured ~18× slower than the
  FFmpeg single-pass for simple overlay comps on this laptop; `--render-engine hyperframes`
  works and `auto` falls back, but `ffmpeg` stays the default for performance)*

## M3 — Migrate skills & pipeline to the render graph

**Files:** modify `apps/cli/.../pipeline.py`, `skills/graphics`, `skills/captions`, `skills/music`, `skills/sfx_placement`, `skills/broll`, `skills/export`.

- [x] **Step 1:** Add a `RenderGraphBuilder` (in `packages/core`) that accumulates cuts/layers/audio and persists `plan/render_graph.json`.
- [~] **Step 2:** `rough_cut` emits `cuts` (EDL) — no render. *(still renders `locked.mp4`; it is consumed by `ve preview`/FableCut, so folding it needs a downstream migration)*
- [x] **Step 3:** `graphics` emits overlay `Layer`s (from preset/brand) instead of rendering segments. *(via `RenderGraphBuilder.extend_graphics_plan`)*
- [x] **Step 4:** `captions` emits timed text `Layer`s. *(`CaptionsSkill.plan_graph_layers`)*
- [~] **Step 5:** `broll` emits `video` layers; `sfx_placement`/`music`/`sfx` emit `AudioTrack`s. *(music emits an `AudioTrack` and folds in; b-roll composites over `base_video` before the graph render)*
- [ ] **Step 6:** `export` runs `Renderer.render(graph)` once + report/decision log. *(export still runs on the composed video)*
- [x] **Step 7:** Rewire `SkillsPipeline.run_async` to build the graph → render once. Keep stage ids/manifest keys so resume/ledger keep working. *(graph path is the default; legacy path via `--no-render-graph` / `use_render_graph=False`)*
- [x] **Step 8:** Update affected tests; keep `ve run`/`ve graphics`/… green.

## M4 — GPU: ASR + NVENC + scheduler

**Files:** modify `apps/transcribe/.../whisper_local.py`, `packages/config`, `packages/ffmpeg/.../render_options.py`, create `packages/core/.../gpu.py`.

- [x] **Step 1:** Wire `config.transcription` (`engine`→ignored, `model`, `language`, `device`, `compute_type`) into a single `transcribe()`.
- [x] **Step 2:** Defaults: `model=large-v3-turbo`, `device=cuda`, `compute_type=int8_float16`; graceful fallback to `small`/CPU if CUDA/cuDNN unavailable.
- [ ] **Step 3:** `RenderOptions` gains `hwaccel` + NVENC option; default intermediates to `h264_nvenc`, final to `libx264`. *(hwaccel added; NVENC-as-default still open)*
- [x] **Step 4:** `GpuScheduler` (context manager) serialises ASR vs ComfyUI; env `VE_GPU=auto|cuda|cpu`.
- [x] **Step 5:** Tests: config→ASR kwargs mapping; NVENC argv shape; CPU fallback path.

## M5 — Single source of truth for models/costs

**Files:** create `models.yaml`, `apps/cli/.../commands/gen_models.py`; modify `pricing.yaml`, `pricing.py`, `router.py`, `estimate.py`, `budget.py`, `storyteller_planner.py`, `templates/base.py`.

- [x] **Step 1:** `models.yaml` = providers → models → costs + task routing + template tiers. *(providers/costs + `llm.tasks` + `estimate.defaults` + `templates.tiers`)*
- [~] **Step 2:** `ve gen-models` generates `pricing.yaml` (header note), router cost table, `estimate.DEFAULT_PROVIDER_MODELS`, budget rates, `TEMPLATE_TIER_PRESETS`. *(`pricing.yaml` is generated; routing/estimate/tier tables stay inline and are guarded by `tests/test_models_registry_sync.py`)*
- [~] **Step 3:** Delete `_BUILTIN_DEFAULTS` duplication (generate) and `eleven_templates.py`. *(built-in kept as last-resort fallback; `eleven_templates.py` still used as the no-YAML fallback)*
- [x] **Step 4:** Test `gen-models` is idempotent (generated == committed).

## M6 — Retire redundancy

- [x] **Step 1:** Delete `apps/editor`, `apps/renderer`, `packages/remotion-types`; remove from `pnpm-workspace.yaml` + root README/architecture. *(done; `remotion_renderer.py` + `pnpm-workspace.yaml`/`pnpm-lock.yaml` also removed. `ve render`/`MusicVideoPipeline` now render via HyperFrames through `video_editor_cli.pipeline.render_scene_plan`)*
- [x] **Step 2:** Delete `packages/tools/whisperx_wrapper.py`; repoint `rough_cut` at the unified ASR.
- [x] **Step 3:** Delete `apps/engine` `RawFootagePipeline`; route directory input through `SkillsPipeline`/graph. Migrate v1 CLI commands or mark deprecated. *(deleted `raw_footage.py`, the 7 v1 commands (`ingest/transcribe/analyze/plan/cut/preview/qa`) and their worker handlers; directory input now runs the skills pipeline on the first clip; affected tests updated)*
- [ ] **Step 4:** Archive stale docs to `docs/archive/`; update `docs/ARCHITECTURE.md` + `docs/ISSUES_BUGS.md`.
- [ ] **Step 5:** Full suite + ruff + mypy green.

## M7 — Performance guardrails & CI

- [x] **Step 1:** Memoise `render_cache._file_hash` by `(path, size, mtime_ns)`.
- [~] **Step 2:** Parallelise independent segment work (bounded `ThreadPoolExecutor`); cap NVENC concurrency. *(moot: the per-segment render loop is gone — graphics/captions now render as one FFmpeg pass or one HyperFrames composition, and generative asset generation already runs concurrently via the manager's semaphores)*
- [x] **Step 3:** `run_ffmpeg`: `-loglevel error`, optional `-progress pipe:`; outputs `-movflags +faststart -pix_fmt yuv420p`. *(render-graph compilers now emit `-nostdin -hide_banner -loglevel error` and `-movflags +faststart -pix_fmt yuv420p`; the shared `run_ffmpeg` wrapper is unchanged to avoid churning existing argv assertions)*
- [ ] **Step 4:** Add `pytest-xdist`; add `.github/workflows/ci.yml` (ruff + mypy + pytest). *(done: `pytest-xdist` added to dev deps; CI workflow added; CI runs pytest serially to avoid races)*
- [x] **Step 5:** `benchmarks/` harness: 10-min 1080p talking-head → time-to-export before/after. *(harness added; `.github/workflows/benchmark.yml` runs it on dispatch/nightly)*

## M8 — (P2 stretch, not in P1 scope)

PySceneDetect, DeepFilterNet3, RIFE/ComfyUI, OpenTimelineIO, pyannote, MCP SDK update, FFmpeg 8.1.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| HyperFrames CLI absent on this machine | FFmpeg compiler is the tested default; HyperFrames path covered by compile-shape tests |
| FableCut bridge depends on per-stage artifacts | Reconstruct artifacts from the graph for `export-fablecut`; keep bridge tests |
| One big filter_complex is harder to debug | Emit the exact argv into the decision log; keep `FFmpegError` stderr capture |
| CUDA/cuDNN missing for `faster-whisper` | Auto-detect + fall back to CPU `small`; document install |
| Regressions during migration | Migrate one contributor at a time behind the same stage ids; suite green each step |
