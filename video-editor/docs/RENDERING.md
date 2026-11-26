# Rendering: speed, the NVIDIA card, captions, where files go

How a `ve explain` render runs on this machine and what makes it fast or slow. Measured on the DeveeKitchen
tiffin video (9:20, 22 scenes, 35 stock shots), 2026-11-25.

## Passes and their cost

| Pass | What it does | Time (9:20 video) |
|---|---|---|
| Footage | Stock search, download, vision judge per shot; AI fallback stills | 10–30 min (the long pole) |
| Scene render | HyperFrames: one Chrome per worker, frames streamed to one encoder | ~5 min |
| Audio mix | Narration, music bed, SFX | seconds |
| Caption burn | Transparent caption page → RGBA PNGs → FFmpeg overlay | ~9 min cold (0.9 s per video second); 79 s with the chunk cache warm |
| QA | Gates, `hyperframes check`, frame snapshots | 1–3 min |

A full re-run with cached narration, music and stock is about 20–40 min. The narration cache key is the
narration text, so a re-run with the same `--job-id` and unchanged narration costs no ElevenLabs characters.
Check the account's `character_count` before and after a render.

## The NVIDIA profile (automatic)

`video_editor_core.hyperframes_cli.gpu_render_profile()` adds settings to every HyperFrames call (render,
check, snapshot):

- **Flags:** `--gpu --browser-gpu --workers 6`. NVENC encodes, and Chrome renders WebGL and raster on the GPU.
- **Environment:** PRIME render offload (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`,
  `__VK_LAYER_NV_optimus=NVIDIA_only`). Without it Chrome picks the AMD Radeon 680M iGPU even with `--gpu`.
  The HyperFrames log shows the renderer it picked (`browserGpuMode probe → ... NVIDIA GeForce RTX 3050`).
- **Switches:** it is on when `nvidia-smi -L` finds a GPU. `VE_RENDER_GPU=0` turns it off and `=1` forces it
  on. `VE_RENDER_WORKERS=N` overrides the worker count.
- **VRAM** (RTX 3050 Laptop, 4 GB, 3 GB budget): 6 workers use about 2.2 GB, 10 workers about 3.3 GB.
  Keep the default of 6.
- **Gain:** on a 30 s benchmark the GPU did not speed up frame capture (Chrome BeginFrame stays CPU-bound,
  about 1× realtime per job). It moves encoding off the 16 CPU threads, and with 6 workers the 9:20 scene
  pass takes about 5 min.

Modal and RunPod would not help: the work is Chrome frame capture, not GPU compute.

## Captions: transparent overlay, never drawtext for Telugu

The old caption pass loaded the whole 600 MB composed video into Chrome as a `<video>`. Chrome decoded it
frame by frame on the CPU, took 112 min, hit the HyperFrames timeout, and `render_graph` silently fell back
to FFmpeg `drawtext`. drawtext cannot shape Telugu, so vowel signs came out detached and conjuncts never
formed.

`video_editor_core.overlay_burn.burn_overlays` (used by `burn_captions`) works like this:

1. It renders only the caption layers on a transparent page (`overlay_only`, no base video) as a PNG
   sequence. The page is split into chunks of about 30 s (`OVERLAY_CHUNK_SECONDS`), cut between caption
   lines, because Chrome's cost grows with the number of clips on the page.
2. Chunks with no caption are hard links to one blank PNG.
3. FFmpeg overlays frame N on frame N of `mixed.mp4`, encodes with `h264_nvenc` (falling back to `libx264`),
   and copies the audio.
4. If the HyperFrames caption render fails, captions that need shaping (`shaped_text.needs_shaping`) raise
   `ComplexScriptRenderError` instead of falling back. Latin captions may still use the FFmpeg path.

To check a Telugu render, crop the caption band and look for a formed conjunct (for example క్యూ).

## AI labels and images

- **Label:** `ai_label.label_ai_stills` draws the script's `ai_label` pill on every AI still: any image from
  `poyo_ai`, `openrouter`, `fal_ai`, `replicate_image`, `flow_image` or `agy_image`, and any `file:` shot
  marked `ai: true`. Telugu text is drawn with Noto Sans Telugu and raqm shaping; Latin uses Inter.
- **Image chain:** `assets.image_chain` is `[poyo_ai, openrouter, fal_ai]`.
  - `agy_image` stalled without writing a file.
  - `flow_image` (flow-mcp) times out since Flow renamed its agent toggle. Each still lost about 2 min
    before PoYo answered.
  - Both stay available by name (`--image-provider`). Free Flow stills come from Claude driving Flow in the
    user's Chrome (STOCK_EXPLAINERS.md, "Flow stills through Claude in Chrome").

## Running a long render

- Run it detached with a memory cap, so it outlives the session and cannot exhaust RAM:

  ```bash
  setsid systemd-run --user --scope -p MemoryMax=20G --unit=<job>-$(date +%s) \
    .venv/bin/ve explain <script.md> --job-id <job> --music-path <bed.mp3> -y > <log> 2>&1 < /dev/null & disown
  ```

  Stop it with `systemctl --user stop <unit>.scope`; that stops Chrome and FFmpeg too.
- The log shows warnings only. Progress shows in file times under `projects/<job>/`: `rendered.mp4`, then
  `mixed.mp4`, then the caption PNGs, then `outputs/<job>/final.mp4`.
- **Where the finished file goes.** `ve explain` writes `/mnt/data2/video-editor-data/outputs/<job>/final.mp4`.
  Once QA passes, copy it into the video's folder in video-projects as
  `videos/<date>/<slug>/<slug>.mp4`. `*.mp4` is gitignored there. Keep older cuts in the outputs folder under
  names that say what was wrong with them (for example `final_v3_badcaptions.mp4`).
- **Tests:** use `.venv/bin/python -m pytest <files>`. Inside Claude Code's sandbox, `uv run` fails on
  `snap-confine`.

## Chunked render cache (on by default)

The scene pass renders in chunks of whole scenes (about 45 s each), and each chunk is cached by a hash of its
compiled project. A re-run renders only the chunks whose scenes changed, then joins them by stream copy and
mixes the audio once. Design and research: `plans/2026-11-25-chunked-render-concept.md`. Plan:
`plans/2026-11-25-chunked-render.md`.

- **Switch:** `VE_RENDER_CHUNKS=0` falls back to the single HyperFrames pass. The cache lives in
  `projects/<job>/render_cache/` (`manifest.json` plus `<hash>.mp4`); each run prunes hashes it did not use.
  `projects/<job>/chunked/chunked_render.json` reports which chunks were rendered or reused.
- **Workers:** `VE_RENDER_WORKERS=8` stays within a 3 GB VRAM budget on the 4 GB RTX 3050 (6 workers ≈ 2.2 GB,
  10 ≈ 3.3 GB). Chunks render one after another, so VRAM never stacks.
- **Parity with the single pass** (tiffin, 9:20, 2026-11-26): the joined video has exactly the planned frame
  count (16,814; the single pass rounds up to 16,815). Average PSNR 37.7 dB. The few low-PSNR frames are
  shot or scene cuts landing one frame (33 ms) apart, which no one can see. Loudness matched after the cue
  mix stopped losing 3 dB on mono narration.
- **Measured on the tiffin video, full `ve explain` with cached narration and footage:**

  | Run | Chunks rendered | Wall time |
  |---|---|---|
  | Cold (every scene changed by the timing fixes) | 10 of 10 | 19:04 |
  | Three scenes changed (4, 10–12, 21–22) | 3 of 10 (≈ 68 s of rendering) | 13:33 |
  | One scene changed (scene 22 text) | 1 of 10 | 12:53 |

  The scene pass is no longer the long pole.

## Caption chunk cache (on by default)

Each non-blank ~30 s caption chunk (`overlay_burn`) is keyed by its compiled, rebased HyperFrames project plus
width, height, fps, the HyperFrames version and an `overlay-png-v1` tag, so keys never collide with scene-chunk
keys. Entries live in `projects/<job>/overlay_cache/<key>/` (PNG frames) with `manifest.json`. A hit needs the
exact PNG count and is hard-linked in; a miss renders, checks the count, then stores. Unused keys are pruned
after a successful run. `VE_OVERLAY_CACHE=0` turns it off.

Because a chunk is rebased to t=0, unchanged captions still hit when an earlier scene's length changes, as long
as the chunk cuts fall at the same caption lines. A narration edit that reflows the lines misses only the
chunks it touches.

**Measured on the tiffin video (8:16), 2026-11-26, identical re-run with every cache warm:** 10:38 wall time,
0 of 9 scene chunks and no caption chunk rendered, picture byte-identical (video MD5) to the run that filled the
caches. Scene join about 18 s, music mix 14 s, caption burn 79 s (was about 9 min). What is left is the
footage pass before the render (about 8 min: the vision judge and asset checks re-run because their verdicts are
not cached) and QA after it. Caching the footage verdicts is the next step.
- **Keeping approved footage:** the stock cache key includes a duration estimated from the narration's word
  count, so editing display text (for example words → digits) re-runs stock search for that scene. To keep
  approved shots, pin them as `file:` shots (see STOCK_EXPLAINERS.md). That is how the tiffin edits kept
  every shot.
