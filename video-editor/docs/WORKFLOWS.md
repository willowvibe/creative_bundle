# Workflows

## Raw Footage Editing

1. **ingest** — validate source files, create proxies, extract WAV audio.
2. **transcribe** — produce word-level transcript with timestamps.
3. **analyze** — detect silence, filler words, and pace.
4. **plan** — LLM or deterministic planner proposes cuts.
5. **cut** — FFmpeg applies cuts and assembles the timeline.
6. **captions** — burn styled captions.
7. **broll** — insert indexed assets or placeholders.
8. **preview** — low-bitrate preview.
9. **qa** — automated checks.
10. **export** — final MP4, cut report, decision log.

## New-Architecture Skill Flow

```text
ve rough-cut ./demo/raw.mp4 demo-rc
ve graphics demo-rc --preset short_explainer
ve captions demo-rc
ve music demo-rc
ve export demo-rc
```

The single-file `ve run` command runs the same sequence in one shot, with
optional QA (`--qa`) and a requirements verification gate (`--verify`).

See `docs/CLI_USAGE.md` for individual flags and `docs/ISSUES_BUGS.md` for the
current roadmap and hand-off state.

## Script-to-Video Generation (v2)

1. **plan_script** — parse script / outline and decompose into scenes.
2. **assets** — resolve per-scene generative assets (TTS, IMAGE, DATA_VIZ, optional VIDEO).
3. **render** — HyperFrames (HTML) composition to MP4; the composition router
   falls back to a single-pass FFmpeg render for simple graphs.
4. **captions** — sync word-level captions.
5. **music / SFX** — optional AI-generated background music and per-scene SFX.
6. **export** — final MP4 + report + decision log.
7. **verify** — optional requirements verification gate.

> Note: the legacy Remotion editor (`apps/editor`) and renderer (`apps/renderer`)
> are **retired** and replaced by HyperFrames (see `docs/archive/audits/AUDIT_2026-10-15.md`).

## ComfyStudio Human-in-the-Loop Workflow

Use the free ComfyStudio desktop editor as a review / timeline-adjustment
surface before final render.

```text
# 1. Generate the initial scene plan and assets
ve plan-script ./script.md -j my-reel --config ./config.yaml
ve assets my-reel --image-provider stub_image --tts-provider stub_tts

# 2. Export the plan to a ComfyStudio project
ve export-comfystudio my-reel \
    --project-dir ./outputs/my-reel-comfystudio \
    --work-root ./projects --output-root ./outputs

# 3. Open ./outputs/my-reel-comfystudio in ComfyStudio.
#    Drag/trim clips, adjust captions, tweak transitions. Save.

# 4. Re-import the edited timeline
ve import-comfystudio my-reel \
    --project-dir ./outputs/my-reel-comfystudio \
    --plan-out-name scene_plan_edited.json \
    --work-root ./projects --output-root ./outputs

# 5. Render the updated plan
ve render my-reel --plan-out-name scene_plan_edited.json
# or continue with music / captions / export as usual
```

`ve export-comfystudio` maps each `ScenePlan` scene to a clip on a video
track, narration to a captions text track, and an optional `--music-path`
to an audio track. `ve import-comfystudio` reconciles clip durations back
into the plan; only duration changes are applied in this first cut (asset
swaps and deep transform edits are logged but ignored).

## External NLE round-trip (OpenTimelineIO)

Any OTIO-capable editor — **FilmCraft**, DaVinci Resolve, Premiere (via an
adapter) — is a first-class human-in-the-loop surface:

```bash
# Export the job's timeline with portable (absolute) media references
ve export-otio my-job --project-dir ./outputs/my-job-otio
#   → writes ./outputs/my-job-otio/project.otio (+ copied media); --otio-path for a copy

# Open project.otio in FilmCraft/Resolve, edit, re-export OTIO, then:
ve import-otio my-job --otio-path ./edited.otio
#   → reconciles clip durations into <work>/plan/scene_plan_edited.json
```

`ve import-otio` and `ve import-fablecut` share the same reconcile core
(`plan_from_fablecut_project`); `ve export-fablecut` also writes a
`project.otio` next to `project.json`. See
[`../tool-lab/trials/2026-10-17-filmcraft/README.md`](../../tool-lab/trials/2026-10-17-filmcraft/README.md).
