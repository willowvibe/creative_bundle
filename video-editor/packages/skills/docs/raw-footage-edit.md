---
type: code
description: Build and operate the raw-footage editing pipeline.
---

# Raw Footage Editing Skill

Use this skill when editing raw footage (a file or a directory of clips) in
`apps/cli` / `apps/engine`. The pipeline is skills-first: the v1
`ingest`/`transcribe`/`analyze`/`plan`/`cut`/`preview` commands are retired.

## Pipeline order

1. **rough-cut** — transcribe, analyse (silence, fillers, pace), lock an EDL and
   render it cache-aware.
2. **graphics** — apply a preset from `presets/` (titles, lower thirds, logo).
3. **captions** — burn word-level animated captions.
4. **music** — optional; ducked mixing against speech.
5. **export** — final MP4 + `report.md` + `decision_log.jsonl`.
6. **qa** — automated render checks (`--qa`).
7. **verify** — requirements gate (`ve requirements`, then `ve verify`).

`ve run` drives all of it; every stage is also a command of its own.

## Idempotency rule

Each stage records what it produced in `<work-root>/<job_id>/manifest.json` and
skips when the output already exists and the input hashes match. Re-runs append
to `operations.jsonl` only when work is actually performed.

## Artifact paths

Work root (`--work-root`; `ve run`'s built-in default is the container path
`/work`, so pass it explicitly — this repo uses `./projects`):

- `<work-root>/<job_id>/transcript/transcript.json`
- `<work-root>/<job_id>/analysis/{silence,filler}.json`
- `<work-root>/<job_id>/plan/edit_plan.json`
- `<work-root>/<job_id>/captions/captions.json`
- `<work-root>/<job_id>/hyperframes/` and its render output
- `<work-root>/<job_id>/qa/qa_report.json`
- `<work-root>/<job_id>/verify/verification_report.json`

Output root (`--output-root`):

- `<output-root>/<job_id>/final.mp4`, `report.md`, `decision_log.jsonl`

## CLI

```bash
ve run ./demo/raw.mp4 --job-id demo-rc \
  --work-root ./projects --output-root ./outputs \
  --preset short_explainer --transcript ./demo/transcript.json \
  --music-path ./demo/music.m4a --qa

ve rough-cut ./demo/raw.mp4 --job-id demo-rc   # individual stages
ve graphics demo-rc --preset short_explainer
ve captions demo-rc
ve export demo-rc
ve verify demo-rc
```

## Model routing

Providers and models come from `config.yaml` (`ve` reads it, or `--config`).
Edit-plan decomposition defaults to `minimax-m3:cloud`. See
`docs/MODEL_ROUTING.md` and `docs/CLI_USAGE.md`.
