# Architecture

## Overview

The Video Editor Engine is a local-first AI video editing system. It is a Python monorepo; video is rendered either by the in-process FFmpeg render graph or by HyperFrames (HTML compositions).

## Component Responsibilities

| Component | Tech | Responsibility |
|-----------|------|----------------|
| `apps/cli` | Python + Typer | User-facing command entrypoint and skills-first pipeline |
| `apps/engine` | Python | Pipeline orchestration, analysis, and planning models |
| `apps/transcribe` | Python + faster-whisper | Local transcription wrapper |
| `apps/editor` / `apps/renderer` | Remotion | **Retired** — replaced by HyperFrames (see `docs/archive/audits/AUDIT_2026-10-15.md`) |
| `packages/state` | Python | Job manifest and idempotent checkpoints |
| `packages/ffmpeg` | Python + FFmpeg | Media transforms |
| `packages/llm` | Python + httpx | Model-agnostic LLM client and router (Ollama Cloud Pro, OpenRouter, TypeSafe) and the TypeSafe/JEV classifier client (`classify.py`) |
| `packages/asset_ingestion` | Python + httpx | Generative asset providers (TTS, image, video, music, SFX, alignment, speech-to-speech, isolation) including HyperFrames video, OpenRouter image/video/music, and cost-first `auto` provider selection |
| `packages/config` | Python + Pydantic | Unified `config.yaml` loader |
| `packages/core` | Python | Render graph, Composition IR and its single compiler (emits HyperFrames HTML), composition router, timeline, render cache |
| `packages/skills/src/verify` | Python + Pydantic | Requirements verification gate (deterministic checks + LLM-as-judge) |

`apps/assets` and `apps/eval` were removed earlier in development; asset indexing and QA now live in `packages/asset_ingestion` and `packages/skills/src/qa`.

## Templates

Every request is first classified by `TemplateRouter`
(`apps/engine/src/video_editor_engine/templates/router.py`) into one of ten
supported templates (e.g. `podium_cut`, `explainer_reel`, `voice_canvas`).
A template is a declarative recipe that selects the pipeline kind, preset,
aspect ratio, and default providers. Templates do not render video; they
validate inputs and produce a `TemplateResult` that the CLI or worker uses.

See `docs/templates.md` for the full catalog,
`docs/Templates_capabilities.md` for detailed capabilities and examples per
 template, and `docs/not_possible.md` for requests that are not supported.

## Data Flow

### Raw Footage (skills pipeline + render graph)

```text
source video → rough_cut (transcribe → analyse → locked EDL)
locked cut → render graph (FFmpeg single pass | HyperFrames composition)
           ← graphics overlays + captions + music (+ b-roll under it)
render → qa → export → verify
```

### Script-to-Video (v2)

```text
script → scene plan → generative assets → composition → render → export → verify (rendered with HyperFrames)
```

The v2 pipeline uses a hybrid visual strategy to balance cost,
speed, and reliability. The scene planner classifies whether a
scene needs real motion (`props.requires_motion`). Static scenes
use a still IMAGE asset that the HyperFrames composition animates
with a Ken Burns pan/zoom. Scenes flagged for motion can use a
VIDEO asset from the HyperFrames CLI (`hyperframes_video`) or its
stub (`stub_hyperframes_video`). The video provider is only
called when the motion flag is set; the IMAGE fallback is still
generated in parallel so the composition can degrade gracefully
if the motion clip fails.

## Composition pipeline

Both pipelines converge on one intermediate representation, `Composition`
(`packages/core/src/video_editor_core/composition.py`). It holds the canvas,
duration and ordered scenes; each scene holds absolute-time layers whose `kind`
(`media`, `text`, `rich_text`, `html`, `shape`, `svg`) decides the markup and
CSS the compiler emits.

- **Adapters** turn an existing input into that IR:
  `adapters/from_render_graph.py` maps a `RenderGraph` (base video + overlays)
  and `adapters/from_scene_plan.py` maps an explainer scene plan (typographic
  scenes from `typographic_scenes.py`, stills and video clips). Their public
  entry points keep the old call sites working: `compile_hyperframes_project`
  and `compile_scene_plan_project`/`render_scene_plan`.
- **One compiler** (`compile_composition.py`) renders the IR to a HyperFrames
  project, so layer CSS, timeline attributes (`data-start`, `data-duration`,
  `data-track-index`) and asset copying have a single implementation.
- **Motion registry** (`motion/registry.py`) resolves a motion name to a CSS
  primitive and writes its `@keyframes` and per-layer `animation` declaration.
  A primitive that writes its own timing into markup (`count`) cannot also take
  a delay, and the registry refuses rather than dropping it.
- **Five QA gates** (`apps/cli/src/video_editor_cli/composition_qa.py`) run on
  a built composition and fail closed: `glyphs` (declared CJK subsets), `cues`
  (every cue the renderer emits is actually spoken in its scene), `safe_area`
  (text inside the platform profile), `composition` (HyperFrames layout check)
  and `visual` (no blank sampled frames).

`scripts/parity_rebuild.py` rebuilds two shipped videos offline from their
saved inputs and compares them with FFmpeg SSIM, so compiler changes are
measured against real output and not only against unit tests.

### Content packs (`ve short`)

A short is authored as one `shorts/<job-id>/short.yaml` and built by
`ve short <pack>`. The pack carries words, on-screen text, image prompts and
the voice choice — never a colour, font or rect; styling comes from the series
design in `designs/<name>.json` through `video_editor_core.layer_presets`.

```text
short.yaml ─┐
designs/*.json ─┼→ preflight → cost gate → stills ─┐
                │                                  ├→ narration.wav → whisper
                └→ per-line TTS ───────────────────┘         │
                                                              ↓
                        Alignment (contiguous scene windows, word times)
                                                              ↓
        pack_to_composition + caption_layers → Composition ─→ one compile
                                                              ↓
                        render → mux → loudnorm → five composition
                        gates + measured-file QA → final.mp4 + qa.json
```

Two rules make the path safe to spend money on. The estimate is built from
`short_job_requests`, the same list the run executes, so the approved number
and the spend cannot drift; and every QA value is measured from the shipped
file, with an unmeasurable gate failing rather than passing.

The two model fields worth knowing: `voice.voice_id` is provider-specific
(Sarvam takes `sarvam:<speaker>`), and `end_card.tail_seconds` (0.8–2.0) is the
window the tail gate will accept, so the pack cannot choose a value the build
must then reject.

The pack can also carry an optional **director's brief** (P4.2): `logline`,
`references`, `characters`, `beat_sheet`, `on_screen_text`, `deliverables` and
`direction`. `brief_warnings()` reports the gaps; they are advisory and never
block a build. `core/look_bans.py` adds the deterministic anti-generic checks
(P4.1) — motion/font/length monotony, centred text on a full-bleed gradient and
missing 2.5D depth — as an advisory `look_bans` QA check that fails only when
`strict_look` is set (`ve short --strict-look`).

The pack has since grown four more optional surfaces: `graphic` (a static
chart component), `shots` (visual changes on line boundaries; `video:` for a
local clip), `layers: generated` (BG/MG/FG prompts, colorkey + parallax) and
`html` (a sandboxed static shot). `transition_out` names the scene's exit
transition, and `captions.mode: sidecar` writes `final.srt` instead of burning.

## Idempotency

Each job has a manifest at `work/{job_id}/manifest.json`. Stages record their outputs and skip if already completed. Re-runs append to `operations.jsonl` only when actual work is performed.

## Requirements Verification Gate

The verification gate (`packages/skills/src/verify`) is the last stage in both
pipelines. It reads `work/{job_id}/verify/requirements.json`
(written by `ve requirements`) and produces
`work/{job_id}/verify/verification_report.json`.

Checks are split into two layers:

1. **Deterministic checks** run locally with FFmpeg/ffprobe and
   artifact inspection: duration range, aspect ratio, required/forbidden
   elements, transcript must-mention phrases, filler-word removal, and
   segment preservation.
2. **LLM-as-judge** handles subjective checklist items
   (`--judge-check`). The calibrated **JEV** classifier is tried first (one
   batched call across all items); the `verify_judge` task in the `ModelRouter`
   is the fallback. Both return `PASS`, `FAIL`, or `NEEDS_REVIEW` per item.

The overall verdict is the worst result across all checks:
`FAIL` beats `NEEDS_REVIEW`, which beats `PASS`. A `FAIL` raises
`VerificationError` and the job is not reported ready;
`NEEDS_REVIEW` is logged but does not block. Results are appended
to the export `report.md`.

## Classification (JEV)

Generation goes through the `ModelRouter`; the many small, independent
**per-item judgments** (scoring, judging, detecting, classifying) go through the
TypeSafe/JEV classifier — one batched, sub-second call for a whole list instead
of a per-item LLM round trip. JEV is used in 11 stages (verify, shorts_factory,
rough_cut, broll, sfx_placement, qa, captions, chapters, thumbnail,
scene_planner, second_pass). Every use is **optional and fallback-safe**: with no
`TYPESAFE_API_KEY` the stage behaves exactly as before. See
[`JEV_CLASSIFIER.md`](JEV_CLASSIFIER.md).

## Time base (integer ticks)

Timelines are authored in float seconds, but all exact arithmetic runs on an
integer tick base borrowed from FilmCraft:
`video_editor_core.ticks.TICKS_PER_SECOND = 254_016_000_000` — chosen so every
common frame rate (including 1001-denominator NTSC rates: `24000/1001`,
`30000/1001`, `60000/1001`) and audio sample rate (44.1/48 kHz) divides it
exactly. `ticks.fps_fraction` snaps `23.976/29.97/59.94` to `n/1001`, so NTSC
frame spans are whole numbers of ticks.

- `RenderGraph.duration_ticks` / `.frame_count()` / `.cuts_contiguous_exact()`
- `Cut`/`Segment` `*_ticks` helpers; `EDL.duration_ticks()` / `.frame_count(fps)`
- `seconds_to_ticks` / `ticks_to_seconds` / `ticks_for_frames` /
  `frames_for_ticks` / `frame_count` / `format_seconds` / `snap_to_frame`

Use it where drift matters (long or mixed-rate timelines, frame-exact trims):
convert to ticks at the edges, do arithmetic in integers, convert back only
when handing values to FFmpeg/HyperFrames. See `tests/test_ticks.py`.

## Colour mode (linear-light compositing)

`RenderGraph.color_mode` — `"gamma"` (default) or `"linear"` — selects how the
FFmpeg compiler blends **media** overlays. Linear mode converts the base and each
`image`/`logo`/`video` overlay into linear light in the alpha-capable 32-bit
float format `gbrapf32le` (via `zscale`) before `overlay=`, then converts back;
text overlays stay in gamma. Default `"gamma"` is byte-identical to previous
renders. See `tests/test_linear_compositing.py`.

## Commands & control channel

Every agent-drivable engine capability is a typed command
(`video_editor_engine.commands`): a stable `id`, a pydantic params model, a
summary/category, and a handler. The same registry backs three front ends:

- **CLI** — `ve commands [--json]` lists the catalog; `ve control --port N`
  serves a JSON-lines TCP control channel (`engine.ping`, `engine.commands`,
  `engine.execute`).
- **MCP** — `list_commands` and `run_command(command, params_json)` tools.
- **Python** — `await default_registry.execute("<id>", {…})`.

Built-in commands today: `probe.media`, `templates.list`, `pricing.cheapest`,
`ticks.info` (introspection) plus the pipeline commands `project.status`,
`qa.run`, `assets.resolve`, `render.run`. See `tests/test_command_registry.py`
and `tests/test_command_pipeline.py`.

## Concurrency rule (A9, 2026-10-28)

The orchestration core is **synchronous**: `SkillsPipeline.run`,
`MusicVideoPipeline.run`, `run_short_job`, `run_explainer_job` and the command
handlers do blocking FFmpeg/subprocess work and are the unit of execution.

Async edges — `ve worker`, the MCP stdio server and the control server — must
enter that core through `asyncio.to_thread`:

- `_dispatch_message` runs every sync handler in a worker thread.
- The `run` and `render_fablecut` handlers offload their sync pipeline calls.
- `CommandRegistry.execute` offloads sync registry handlers.

`run_async` stays on the pipelines for direct async callers and tests; it is
not the worker's entry point (the worker calls `run`). Provider I/O stays async
inside `AssetIngestionManager`.

Why: the worker heartbeat is an asyncio task. Anything that blocks the loop
starves it and lets another worker reclaim a live job (#298).

## Error-handling policy

The rule is simple: **best-effort side channels may log and continue; anything
that affects the output file or spend must raise or mark the job degraded.**

- **Raise** when the failure means the requested artifact is wrong or missing:
  a render, mux, probe of the file that ships, required input path, or a
  provider call that has no viable fallback.
- **Mark degraded** when the build can still finish but its accounting or
  evidence is incomplete. `AssetIngestionManager.cost_tracking_degraded`
  records pricing, budget-snapshot and ledger-write failures; QA surfaces the
  reasons in `qa.json` and `ve cost-report` names the project.
- **Log and continue** only for optional telemetry, cache cleanup, best-effort
  metadata, and providers explicitly treated as best-effort fallbacks. The
  failure must not be the only signal that output or spend is incomplete.
- **Never swallow** a failure that changes what a caller is told: a missing
  cost is a warning, not a zero; a missing QA measurement is a failed check,
  not a pass.

Broad `except Exception` sites are audited against these rules. **The rule is
enforced in CI** by ruff `BLE001` and `S110` (`pyproject.toml`); every broad
handler carries `# noqa: BLE001 - <side channel>` (or `S110`) naming what it
protects. `scripts/audit_broad_except.py` reproduces the raw count for audits.

**Audit result (2026-10-27).** The tree has 119 broad `except Exception`
handlers. They fall into five groups: optional config/env probing; cleanup,
close and reap paths; advisory QA/classifier calls; MCP tool boundaries that
return a structured error; and cost/ledger writes. The output and spend paths
follow the rule above: render and mux wrappers raise, QA gates fail closed,
the estimator returns `None` for an unpriceable build (so the cost check
fails), and the asset manager marks cost tracking degraded when pricing, a
budget snapshot or a ledger write fails. The remaining broad handlers are the
best-effort side channels named in their comments; none is the only signal
that a shipped file or a spend total is complete.
