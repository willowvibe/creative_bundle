---
name: video-editor
description: >
  Use when building, reviewing or debugging a video in this repository:
  `ve short`, `ve explain`, `ve musical`, the QA gates, cost approval, the
  `videos/<date>/<project>/` layout, or a parity/regression check.
---

# Video editor build loop

One Composition IR, one compiler, one HyperFrames render. A change to a
template, caption, transition or effect belongs in the IR or the compiler, not
in a per-project script.

Drama episodes (script → voices → stills → animatic → clips → cut → promo → dubs) live in the sibling repo
`../drama-studio` (skill `drama-episode`, playbook `docs/DRAMA_PRODUCTION_PLAYBOOK.md` there). Video scripts
and short packs live in `../video-projects`, reached here through the `videos` and `shorts` symlinks.

Editing the user's own phone-recorded talking-head clips (stitch, 9:16 → 16:9, voice cleanup, trim to a
time limit from a transcript): skill `phone-vlog-edit`.

Coding/tech tutorials with the user's talking cartoon avatar over real screen captures (Python Decoder channel):
skill `tech-tutorial-video`, recipe `docs/TECH_TUTORIALS.md`.

Trying an outside repo, skill pack or model before adopting it: do it in `../tool-lab` (skill `tool-trial`,
`REGISTRY.md` first); adopted pieces are ported here with tests.

## Commands

```bash
ve short <pack>/short.yaml          # content pack -> stills/voice/align/render/QA
ve explain <script.md>              # script or --from-topic -> explainer
ve musical --music <file> --media-dir <dir> --lyrics <file> --output <mp4>
ve run <source> --job-id <id>       # v2 raw-footage pipeline
ve doctor                           # ffmpeg filters, HyperFrames, Chrome, agy
```

Every render writes under `projects/<job>/` (work) and `outputs/<job>/`
(final); `videos/<date>/<project>/` is the delivery copy. The queue DB follows
the output root.

## Explainer features to reach for

- **Business breakdowns** (`docs/STOCK_EXPLAINERS.md`):
  - `visual: footage` gives a vision-checked stock clip with an AI-still fallback.
  - A `tally` chip keeps a running cash box.
  - `calc` cards are arithmetic-checked, and the tally must move by their cash rows.
  - `layout: ledger` closes the day.
- **Map-story reels** (`docs/MAP_STORIES.md`):
  - `frame: full` plus `design: night_map`.
  - `journey` (km counter, travelling icon, `follow`, `along: <river>`, `shape: heart`).
  - `state:`/`district:` regions, filled or photo-masked, with
    `image_prompt`/`image_query` photos fetched by the pipeline.
  - `bubbles` and `stickers`.
- **Telugu and other non-Latin narration:**
  - Use `narration_mode: per_scene`; Whisper skips synthetic Telugu.
  - Spell numbers for the voice with `pronunciations`.
  - Write map cues in the narration's script.
  - The ElevenLabs Telugu voice runs about 25% slower than Sarvam's.
- **Before a paid run:** read the estimate gate's table (`echo n | ve explain ...`
  prints it and spends nothing). Quote it again if a change raises the price,
  for example switching to per-scene Sarvam narration.
- **Credits in descriptions:**
  - District shapes need the ODbL line.
  - Disclose AI-generated images.

## Gates (fail closed)

- **Cost:** every paid request is priced from `pricing.yaml`; a run without an
  approval file refuses to spend. Do not bypass the gate in a script.
- **Composition:** `hyperframes check` (lint, runtime, layout, contrast) plus a
  snapshot at each scene midpoint; a blank frame fails.
- **Measured file:** duration, loudness (-14 LUFS, -1 dBTP), true peak, caption
  coverage, silent gaps, tail. The check reads the shipped file, not the plan.
- **Parity:** `uv run python scripts/parity_rebuild.py` (scene-plan,
  render-graph, musical). Never change a threshold to make a case pass; fix the
  primitive default or the adapter mapping.

## Rules

- Times are absolute seconds, quantised to the frame grid.
- Scene windows are contiguous and cover the composition duration.
- Media layers are root-level clips; never nest them.
- No JS-dependent motion; every effect must survive a deterministic frame seek.
- No test makes a network call or spends money; providers are faked.
- `uv run pytest -n 8 -m "not integration and not slow"`, `uv run ruff format
  --check .`, `uv run ruff check .`, `uv run mypy apps packages`, `uv run
  lint-imports` before a commit.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| FFmpeg filter rejected | `ve doctor`; FFmpeg 6.1+ static validation rejects runtime variables in `zoompan` `x`/`y` |
| Render freezes at t=0 | root `data-no-timeline`; CSS-only motion |
| Text falls back | the family must be in `BUNDLED_FONTS`; Devanagari and Korean are bundled |
| `agy` 429 | account `generate_image` quota; re-run `VE_AGY_INTEGRATION=1 uv run pytest -m integration tests/test_agy_image_integration.py` |
| Parity SSIM miss | compare the primitive/adapter output at the failing frame; do not lower the threshold |

## Planning

- Open work: `docs/ISSUES_BUGS.md`.
- Forward plan: `docs/archive/plans/ROADMAP_2026-10-28.md`.
- Executable slices: `docs/archive/plans/2026-10-28-roadmap-execution.md`.

Optional plugins: `huggingface-skills` (model downloads/VRAM for 2.5D),
`mcp-server-dev`, `ffmpeg-llm`, `image-generation`. Disable unrelated servers
(Vercel, Expo, Supabase, data-engineering) per session to cut context; that
list is machine-local and not committed.
