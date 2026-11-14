# Raw to Short — One Command

Turn raw footage into a polished short-form explainer clip with a single CLI command.

## What it does

This workflow runs the full v2 skills-first pipeline:

```text
rough-cut → graphics → captions → optional music → export
```

Only changed segments re-render thanks to the segment render cache, so iterating on a single segment or re-running with the same inputs is fast.

## Requirements

- Python 3.12+ and `uv`
- FFmpeg 6.1+ and ffprobe
- (Optional) Node.js 22+ and the `hyperframes` CLI for HTML composition rendering

Run `bash scripts/bootstrap.sh` once to install dependencies.

## Quick start

```bash
uv run ve run ./demo/raw.mp4 \
  --job-id demo-short \
  --work-root ./projects \
  --output-root ./outputs \
  --preset short_explainer
```

With a pre-generated transcript (skips Whisper/WhisperX) and background music:

```bash
uv run ve run ./demo/raw.mp4 \
  --job-id demo-short \
  --work-root ./projects \
  --output-root ./outputs \
  --preset short_explainer \
  --transcript ./demo/transcript.json \
  --music-path ./demo/music.m4a
```

Or use the convenience wrapper:

```bash
export SOURCE=./demo/raw.mp4
export JOB_ID=demo-short
export PRESET=short_explainer
export TRANSCRIPT=./demo/transcript.json
bash scripts/run_skills_pipeline.sh
```

## Outputs

- `projects/{job_id}/intake/raw.mp4` — copied source footage
- `projects/{job_id}/rough_cut/locked.mp4` — locked rough cut
- `projects/{job_id}/graphics/with_graphics.mp4` — graphics composition
- `projects/{job_id}/captions/with_captions.mp4` — final captioned video
- `projects/{job_id}/music/with_music.mp4` — music-mixed video (only if music provided)
- `outputs/{job_id}/final.mp4` — exported final video
- `outputs/{job_id}/report.md` — human-readable export report
- `outputs/{job_id}/decision_log.jsonl` — edit decision log

## Iterating with Second Pass

After the first run, refine a single segment and re-run the pipeline. Only the edited segment re-renders.

```bash
# Tighten segment seg_0001 and re-run the full chain
uv run ve second-pass demo-short \
  --segment seg_0001 \
  --end 8.5 \
  --work-root ./projects \
  --output-root ./outputs

uv run ve run ./projects/demo-short/intake/raw.mp4 \
  --job-id demo-short \
  --work-root ./projects \
  --output-root ./outputs \
  --preset short_explainer \
  --transcript ./projects/demo-short/transcript/transcript.json \
  --skip-music
```

> Note: re-running `ve run` on the same job reuses the cached transcript and rough-cut analysis. Use a fresh `job_id` to start from scratch.

## Available presets

- `short_explainer` — 1080×1920 short-form style
- `tiktok_raw` — raw vertical clip look
- `long_form` — 1920×1080 talking-head with lower-thirds
- `brand` — brand-aware colors and fonts from `presets/brand.json`

## When to use the legacy per-skill commands

The individual commands (`ve rough-cut`, `ve graphics`, `ve captions`, `ve music`, `ve export`) are still useful when you want to inspect or tweak each stage manually. See `workflows/raw_to_short.md` for the manual path.
