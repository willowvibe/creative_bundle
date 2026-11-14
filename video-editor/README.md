# Video Editor Engine

A local AI video-editing engine with two workflows:

1. **Raw Footage Editing** — transcript-first editing with filler-word removal, silence trimming, jump cuts, captions, b-roll, and QA.
2. **Script-to-Video Generation** — scene-plan-first generation with generative assets, motion graphics, and multi-aspect exports (rendered with HyperFrames).

Everything runs locally except model inference, which routes through the provider declared in `config.yaml` (default is **OpenRouter**; `config.free.yaml` uses **Ollama Cloud Pro**).

## Architecture

- `apps/cli` — Typer CLI (`ve`) and the skills-first pipeline
- `apps/engine` — Python orchestrator and analysis models
- `apps/transcribe` — faster-whisper transcription wrapper
- `packages/state` — job manifest and checkpointing
- `packages/ffmpeg` — idempotent FFmpeg wrapper
- `packages/llm` — LLM client + model router (Ollama Cloud Pro, OpenRouter, TypeSafe) + the TypeSafe/JEV classifier client
- `packages/asset_ingestion` — generative asset providers (TTS, image, video, music, SFX, forced alignment, speech-to-speech, audio isolation) including OpenRouter image/video/music and cost-first `auto` selection
- `packages/config` — unified `config.yaml` loader
- `packages/fablecut_bridge` — FableCut timeline import/export bridge
- `packages/core` — render cache, timeline, render graph, and the HyperFrames/FFmpeg renderers
- `tools/` — WhisperX, FFmpeg wrappers, and bundled FableCut editor
- `packages/skills/` — all skill modules (rough_cut, graphics, captions, music, export, broll, reframe, transitions, sfx_placement, screen_tutorial, motion_graphics, chapters, dubbing, shorts_factory, thumbnail, beat_sync, audio_cleanup, second_pass, review, verify) in one distribution
  - `rough_cut/` — WhisperX + filler/silence removal + locked edit
  - `graphics/` — HyperFrames composition builder
  - `second_pass/` — partial re-render + segment refinement
  - `captions/` — word-level animated captions
  - `music/` — background music mixing
  - `export/` — final MP4 + report + decision log
  - `qa/` — render validation
  - `verify/` — requirements verification gate

## Model Routing

The active routing lives in [`config.yaml`](config.yaml). The project default
routes through **OpenRouter**; [`config.free.yaml`](config.free.yaml) routes the
same tasks through **Ollama Cloud Pro**.

| Task | OpenRouter (default) | Ollama Cloud (free mode) |
|------|----------------------|--------------------------|
| Planning / decomposition | `anthropic/claude-sonnet-5.5` | `minimax-m3:cloud` |
| Code gen / tool-heavy execution | `anthropic/claude-sonnet-5.5` | `kimi-k2.7-code:cloud` |
| Fallback executor | `google/gemini-3.8-flash` | `glm-5.3:cloud` |
| Small utility tasks / verification judge | `google/gemini-3.8-flash` | `glm-5.3-flash:cloud` |

Rates for every provider and model live in [`models.yaml`](models.yaml) (the
single source of truth). `pricing.yaml` is generated from it — run
`ve gen-models` after editing. Override routing per-project by creating
`config.yaml` (see `config.example.yaml`):

```bash
ve plan my-job --config ./config.yaml
ve run ./raw my-job --config ./config.yaml
```

**Per-item judgments** (scoring, judging, detecting, classifying) use the
calibrated **TypeSafe/JEV** classifier instead of a chat LLM — one batched call
per stage, across 11 engine stages, always optional and fallback-safe. See
[`docs/JEV_CLASSIFIER.md`](docs/JEV_CLASSIFIER.md).

**Generation** is broader than text: the opt-in `openrouter` asset provider
serves image, video, and music too (PoYo stays the default), and when a provider
is `auto` with a named model the **cheapest** configured provider wins
(`models.yaml:model_equivalents`). See
[`docs/OPENROUTER_GENERATION.md`](docs/OPENROUTER_GENERATION.md) and
[`docs/MODEL_ROUTING.md`](docs/MODEL_ROUTING.md).

**Motion quality** is built into the renderers. Visuals reveal and highlight
when the narration names them (`video_editor_core.cues`), scenes never freeze
(ambient layers, camera drift, varied Ken Burns), karaoke captions dim the
words not yet spoken, and QA flags held frames. See
[`docs/VIDEO_MOTION_QUALITY.md`](docs/VIDEO_MOTION_QUALITY.md).

## Requirements

- Python 3.12+
- Node.js 22+ (only for optional tooling)
- **HyperFrames 0.8.208** — pinned in `package.json`; run `pnpm install`
- **FFmpeg 8.1+ + ffprobe** — install with `bash scripts/install_ffmpeg.sh`
  (static build; adds NVENC/AV1/Vulkan encoders). 6.1 will still run most paths.
- uv
- pnpm

## Setup

```bash
bash scripts/bootstrap.sh
```

This installs the Python workspace via `uv` and the Node workspace via `pnpm`, and verifies FFmpeg. Run `ve doctor` at any time to check FFmpeg filters, HyperFrames, Chrome and `agy`.

## What you can build

- **Raw-footage polish** — auto-cut a talking-head video into a tight reel with captions, music, and brand overlay: `ve run talk.mp4 --job-id talk`.
- **Script-to-video generation** — turn a markdown script into a fully generated explainer with TTS, b-roll, motion clips, music, and SFX: `ve run concept.md --template explainer_reel`.
- **Multi-voice, multi-language story videos** with persona-tagged scripts, Sarvam Bulbul TTS for Indian languages, PoYo music, and FableCut timeline preview — try `ve run story.md --template storyteller_hd`.
- **Vlogger reels** — a directory of raw clips becomes a polished vlog with B-roll, reframe, transitions, SFX, and burnt-in captions: `ve run ./clips/ --template vlog_reel --reframe --broll --sfx`.
- **Tech tutorials** — screen recordings get cursor zoom, click highlights, and captions via the `tech_tutorial` preset: `ve run screen.mp4 --template tech_tutorial` (chapter markers via the MCP server's `chapters` tool).
- **Podcast videos** — multi-track audio with speaker diarization, chapter markers, and a camera/speaker layout: `ve run podcast.mp4 --template podcast_video`.
- **Shorts factory** — long-form video auto-extracts into vertical Shorts with kinetic captions and 9:16 reframe via the MCP server's `shorts_factory` tool (`video_path` + optional `transcript` + `count`; default 4).
- **Music-driven lyric montages** from your own images and short clips: `ve run song.mp3 --media-dir ./visuals --lyrics ./lyrics.txt`.

## First Demo — Raw Footage Editing

Create a sample video and run the full skills-first pipeline in one command:

```bash
# Generate a 10-second test video with synthetic audio
mkdir -p demo
ffmpeg -f lavfi -i testsrc=duration=10:size=1920x1080:rate=30 \
       -f lavfi -i sine=frequency=1000:duration=10 \
       -pix_fmt yuv420p demo/raw.mp4

# Run the full pipeline: rough-cut → graphics → captions → export
uv run ve run ./demo/raw.mp4 --job-id demo-1 --work-root ./projects --output-root ./outputs --preset short_explainer

# Optional: provide a transcript to skip transcription and add background music
uv run ve run ./demo/raw.mp4 --job-id demo-1 --work-root ./projects --output-root ./outputs \
       --transcript ./demo/transcript.json --music-path ./demo/music.m4a --preset short_explainer

# Or use the convenience wrapper
export SOURCE=./demo/raw.mp4
export JOB_ID=demo-1
export PRESET=short_explainer
export TRANSCRIPT=./demo/transcript.json
bash scripts/run_skills_pipeline.sh
```

Outputs:

- `projects/demo-1/` — intermediate artifacts
- `outputs/demo-1/final.mp4` — polished final video
- `outputs/demo-1/report.md` — human-readable export report
- `outputs/demo-1/decision_log.jsonl` — edit decision log

Every command takes `--work-root` / `--output-root`, and so do `JobContext` and
`JobManifest` when built from a library. When they are omitted the roots
resolve in this order: `VE_WORK_ROOT` / `VE_OUTPUT_ROOT` →
`VIDEO_EDITOR_DATA_DIR` (then `projects/` / `outputs/` beneath it) → `/work` /
`/output` if those directories exist (the container layout) → `./projects` /
`./outputs`. The data-dir setting also gives the queue DB, the machine-wide
cache (`<data>/cache/layerize`) and future `videos/` outputs one home outside
the checkout; the per-root variables still win. Setting
`VIDEO_EDITOR_DATA_DIR` (for example in `.env`) is the recommended way to keep
multi-gigabyte run data out of the repository (A13). `ve plan-script` finds
`config.yaml` the same way: explicit
`--config` → `VE_CONFIG` → nearest ancestor of the current directory → repo
root.

## Free Mode — Zero API Key Operation

Run the engine with only free/local providers:

```bash
ve run ./demo/raw.mp4 --job-id demo-free --config config.free.yaml
```

This uses:
- **Kokoro / KittenTTS / MOSS-TTS** for local narration (no API key)
- **Pexels / Coverr / Mixkit** for free stock footage
- **ACE-Step** for free music generation
- **HyperFrames** for local motion graphics

The only API key needed is `OLLAMA_CLOUD_API_KEY` for LLM tasks.

## CLI Commands

For the full command surface (including the `ve enqueue` /
`ve worker` payload contract), see [`docs/CLI_USAGE.md`](docs/CLI_USAGE.md).

```bash
ve --help
ve run ./demo/raw.mp4 --job-id my-job --work-root ./projects --output-root ./outputs  # full skills pipeline

# Individual new-architecture skills (v2)
ve rough-cut ./demo/raw.mp4 my-job
ve graphics my-job --preset short_explainer
ve captions my-job
ve music my-job --music-path ./demo/music.m4a
ve export my-job

# Generative extras
ve music-generate my-job --prompt "upbeat corporate tech"
ve sfx-generate my-job --provider elevenlabs_sfx
ve sfx-mix my-job
ve align my-job --audio ./narration.mp3 --text "Hello, world."
ve audio-isolate my-job --audio ./noisy.mp3
ve speech-to-speech my-job --audio ./voice.mp3 --voice-id Xb7hH8MSUJpSbSDYk0k2
ve brief my-job --from-md ./project.md

# Human-editable timeline bridges (requires bundled FableCut in tools/FableCut)
ve export-fablecut my-job --project-dir ./outputs/my-job-fablecut
python scripts/open_in_fablecut.py ./outputs/my-job-fablecut   # starts server, opens browser
# edit and save at http://localhost:7777, then:
python scripts/finalize_fablecut.py                                # copies back + imports

# Requirements verification gate
capture:
ve requirements my-job --min-duration 25 --max-duration 35 --require-aspect 16:9
run with verification:
ve run ./demo/raw.mp4 --job-id my-job --verify
manual gate:
ve verify my-job

# Queue / debug utilities
ve enqueue run -j my-job --payload '{...}'
ve worker --max-jobs 5
ve dequeue
ve queue-size
ve probe ./demo/raw.mp4
# Script-to-video render (HyperFrames)
ve render my-job
# Content pack → short (HyperFrames)
ve short ./shorts/saju-v1-your-animal/short.yaml
```

## Development

```bash
# Python lint/format/typecheck
uv run ruff check .
uv run ruff format .
uv run mypy .

# Python tests
uv run pytest

# Dev helper (prints HyperFrames usage)
bash scripts/dev.sh
```

## Project Layout

```text
video-editor/
├── apps/            # CLI, engine, transcribe
├── packages/        # state, ffmpeg, llm, asset_ingestion, config, core, tools, mcp_server, skills
├── tools/           # WhisperX and FFmpeg wrappers, bundled FableCut editor
├── packages/skills/ # one distribution for every skill module
│   ├── src/rough_cut/   # WhisperX + filler/silence removal + locked edit
│   ├── src/graphics/    # HyperFrames composition builder
│   ├── src/second_pass/ # partial re-render + segment refinement
│   ├── src/captions/    # word-level animated captions
│   ├── src/music/       # background music mixing
│   ├── src/export/      # final MP4 + report + decision log
│   ├── src/qa/          # render validation
│   └── src/verify/      # requirements verification gate
├── presets/         # short_explainer, tiktok_raw, long_form, brand
├── workflows/       # markdown workflow guides
├── scripts/         # bootstrap.sh, dev.sh, open_in_fablecut.py
├── docs/            # architecture and workflow docs (index: docs/README.md)
├── projects/        # runtime per-job workspace
└── outputs/         # runtime final exports
```

## New Architecture CLI

Run the full skills-first pipeline with one command:

```bash
# rough-cut → graphics → captions → export (music is optional)
ve run ./demo/raw.mp4 --job-id demo-rc --work-root ./projects --output-root ./outputs \
       --preset short_explainer --transcript ./demo/transcript.json --music-path ./demo/music.m4a

# Same pipeline with QA + requirements verification gate
ve run ./demo/raw.mp4 --job-id demo-rc --work-root ./projects --output-root ./outputs \
       --preset short_explainer --qa --verify
```

Or run each skill separately:

```bash
# Run the new rough cut skill (WhisperX + cache-aware render)
ve rough-cut ./demo/raw.mp4 demo-rc --work-root ./projects --output-root ./outputs

# Optional: provide a pre-generated transcript to skip WhisperX
ve rough-cut ./demo/raw.mp4 demo-rc --transcript ./demo/transcript.json

# Apply a graphics preset to the locked rough cut
ve graphics demo-rc --work-root ./projects --output-root ./outputs --preset short_explainer

# Refine a single segment (only the edited segment re-renders)
ve second-pass demo-rc --segment seg_0001 --end 8.5 --work-root ./projects --output-root ./outputs

# Burn word-level animated captions onto the graphics composition
ve captions demo-rc --work-root ./projects --output-root ./outputs

# Mix in background music with speech-aware ducking
ve music demo-rc --work-root ./projects --output-root ./outputs --music-path ./demo/music.m4a

# Export final MP4, report, and decision log
ve export demo-rc --work-root ./projects --output-root ./outputs
```

## Roadmap

### v1 (stable)

- [x] Repository structure
- [x] Dependency list
- [x] Minimal CLI
- [x] Core orchestrator scaffold
- [x] FFmpeg wrapper layer
- [x] Full raw-footage pipeline
- [x] First working demo

### v2 / new architecture (complete)

- [x] Directory structure: engine/, tools/, packages/skills/, presets/, workflows/, projects/, outputs/
- [x] Render cache engine with segment hashing
- [x] Rough cut skill with WhisperX integration
- [x] Graphics skill with preset loader and HyperFrames/FFmpeg fallback
- [x] Second Pass skill with partial re-render
- [x] Caption skill with word-level animation
- [x] Music skill with speech-aware ducking
- [x] Export skill with final MP4 + report + decision log
- [x] config.yaml model-agnostic provider selection
- [x] Single `ve run` command for the skills-first pipeline
- [x] `ve worker` dispatcher with typed enqueue payloads
- [x] Generative asset providers (TTS, image, video, music, SFX, forced alignment, speech-to-speech, audio isolation)
- [x] Per-segment and pipeline QA gates
- [x] Render options (codec/bitrate/fps) wired from config through FFmpeg
- [x] OpenRouter generation provider (image/video/music) + live pricing in `models.yaml`
- [x] Cost-first `auto` provider selection (cheapest provider for a named model via `model_equivalents`)
- [x] TypeSafe/JEV classifier used across 11 engine stages (optional, batched, fallback-safe)
- [x] Integer-tick time base for drift-free timeline arithmetic (`video_editor_core.ticks`)
- [x] Opt-in linear-light float compositing (`RenderGraph.color_mode="linear"`)
- [x] Typed command registry + JSON control channel (`ve commands`, `ve control`) and MCP `run_command`

## License

MIT
