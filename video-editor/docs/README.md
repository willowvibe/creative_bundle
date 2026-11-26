# Documentation index

Start with the root [`README.md`](../README.md) for install and quick-start.
This page maps the rest of `docs/`. Files under `docs/archive/` are historical
records — kept for provenance, not maintained.

## Architecture and using the CLI

| Doc | What it covers |
|-----|----------------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Components, data flows, idempotency, verification gate, ticks, colour modes, command registry |
| [`CLI_USAGE.md`](CLI_USAGE.md) | Every `ve` command and flag |
| [`templates.md`](templates.md) | Template layer and the supported catalog |
| [`Templates_capabilities.md`](Templates_capabilities.md) | Per-template capabilities, variations, and examples |
| [`not_possible.md`](not_possible.md) | Request shapes that will not produce correct results |
| [`WORKFLOWS.md`](WORKFLOWS.md) | End-to-end recipes (raw footage, script-to-video, music) |
| [`RENDERING.md`](RENDERING.md) | Render passes and timings, the automatic NVIDIA profile (NVENC, Chrome on the RTX), transparent caption overlay (no Telugu drawtext), image chain, detached capped runs, where final cuts go |
| [`EXPLAINER_PLAYBOOK.md`](EXPLAINER_PLAYBOOK.md) | The order to build a numbers-led explainer (research → script → calcs → geography → screen plan → timing → stills → voice → music/SFX → captions → hook/end → review) and the rules learned on the tiffin teardown |
| [`STOCK_EXPLAINERS.md`](STOCK_EXPLAINERS.md) | Stock footage scenes (vision-checked), running tally, checked calculations, ledger, per-scene narration, AI image labels |
| [`MAP_STORIES.md`](MAP_STORIES.md) | Map-story reels: journeys with km counter and icon, night_map, state/district fills and photos, bubbles, stickers |
| [`DEVOTIONAL_CHANTS.md`](DEVOTIONAL_CHANTS.md) | Japa ×108 chant videos: chant-grid sync, 75% Tamil+English captions, counter, petal/glitter loops, rotating deity images, memory-safe pieces render |
| [`TECH_TUTORIALS.md`](TECH_TUTORIALS.md) | Coding tutorials: verified demos, real page captures, editor/terminal screen track, talking avatar (hybrid lip sync), section titles, SFX, music bed, channel bumpers, Flow thumbnails, upload gotchas |

## Providers, models, and routing

| Doc | What it covers |
|-----|----------------|
| [`MODEL_ROUTING.md`](MODEL_ROUTING.md) | `ModelRouter`, `config.yaml`, cost-first `auto` selection |
| [`POYO_ROUTING.md`](POYO_ROUTING.md) | PoYo model IDs, credit prices, tier selection guide |
| [`OPENROUTER_GENERATION.md`](OPENROUTER_GENERATION.md) | OpenRouter image/video/music generation |
| [`SARVAM_INTEGRATION.md`](SARVAM_INTEGRATION.md) | Sarvam Bulbul TTS for Indian languages |
| [`EARSHOT_INTEGRATION.md`](EARSHOT_INTEGRATION.md) | Code-synthesized SFX bank: install, config, bank ids |
| [`JEV_CLASSIFIER.md`](JEV_CLASSIFIER.md) | Batched per-item classifier used in 11 stages |

## Quality and motion

| Doc | What it covers |
|-----|----------------|
| [`VIDEO_MOTION_QUALITY.md`](VIDEO_MOTION_QUALITY.md) | Cue timing, spotlights, karaoke captions, freeze checks, safe areas |
| [`prompt_for_optimal_results.md`](prompt_for_optimal_results.md) | Prompt patterns and template selection |
| [`ISSUES_BUGS.md`](ISSUES_BUGS.md) | Canonical issue log and closed index |

## Operations

| Doc | What it covers |
|-----|----------------|
| [`PACKAGING.md`](PACKAGING.md) | Packaging the engine for another machine |
| [`LIVE_PROVIDER_RUN.md`](LIVE_PROVIDER_RUN.md) | A full render against real cloud providers |
| [`CLAUDE_TOOLS.md`](CLAUDE_TOOLS.md) | Optional Claude Code tooling (pxpipe proxy) |

## Planning and evaluation

Open work lives in [`ISSUES_BUGS.md`](ISSUES_BUGS.md); finished audits, plans and specs are in `archive/`. Evaluations of
outside repos, skills and models live in `../tool-lab` (`REGISTRY.md` lists every candidate and its verdict).

| Doc | What it covers |
|-----|----------------|
| [`../tool-lab/trials/2026-10-22-motion-canvas-local-i2v/README.md`](../../tool-lab/trials/2026-10-22-motion-canvas-local-i2v/README.md) | Tier-A local image motion (DepthFlow) and Motion Canvas/Revideo (2.5D supersedes DepthFlow) |
| [`../tool-lab/trials/2026-10-17-filmcraft/README.md`](../../tool-lab/trials/2026-10-17-filmcraft/README.md) | ArtCraft / FilmCraft evaluation |
| [`design/capsule-frame.md`](design/capsule-frame.md) | Capsule frame-layer spec for FableCut |
| `../drama-studio/docs/DRAMA_*.md` | Drama pipeline docs (playbook, v2 pipeline, video-stack research, feasibility, story rules), in the `drama-studio` repo since 2026-11-11 |
| `../tool-lab/trials/2026-11-04-freefield/README.md` | Open-weights model lab on Modal: LTX-2.5, MiniMax H3, SeedVR2 finishing upscale (#324) |

## Archive

| Path | Contents |
|------|----------|
| [`archive/audits/`](archive/audits/) | Finished audits and reviews, including 2026-10-15 to 2026-11-10 and the repo-split proposal (done 2026-11-11) |
| [`archive/plans/`](archive/plans/) | Completed implementation plans, the 2026-10-28 roadmap and its execution plan, spikes and trials |
| [`archive/specs/`](archive/specs/) | Completed design specs |
| [`archive/research/`](archive/research/) | Dated research and model surveys |
| [`archive/design/`](archive/design/) | Design docs whose feature has shipped |
| [`archive/architecture-explained.md`](archive/architecture-explained.md) | Long-form architecture walkthrough (pre-render-graph; superseded by `ARCHITECTURE.md`) |
