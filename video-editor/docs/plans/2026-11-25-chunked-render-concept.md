# Chunked render with a per-scene cache — concept

**Status:** built 2026-11-26 and on by default (plan: `2026-11-25-chunked-render.md`; measurements:
`../RENDERING.md`, "Chunked render cache"). Caption-chunk caching (§6) built 2026-11-26 (`../RENDERING.md`, "Caption chunk cache").

## What is left to solve

Speed alone is mostly solved (see `../RENDERING.md`):

- **Parallel scene pass:** HyperFrames already splits one render across 6 Chrome workers on the NVIDIA
  card, with NVENC encoding. The 9:20 tiffin video's scene pass takes about 5 min.
- **Captions:** they are a transparent overlay rendered in chunks of about 30 s (`overlay_burn`), about
  9 min for 9:20.

The remaining problem is **re-work**. Fixing one scene, such as a nudged map bubble, a chart label or a
swapped stock shot, re-renders all 22 scenes and burns all captions again. That is 15+ minutes for a
change that touches 20 seconds of video. The goal is for a one-scene fix to cost about one scene's render
plus a join.

## How others do it (research, 2026-11-25)

| Tool | What it does | What we take from it |
|---|---|---|
| **HyperFrames 0.8.216 segmented capture** (`HF_SEGMENTED_CAPTURE=true`, `--resume`, `--keep-segments`) | Splits the render into fixed-size frame segments (`HF_SEGMENT_FRAMES`) with a `segments.json` manifest, re-validates each kept segment (size and probed frame count) and skips finished ones. | It does not solve our problem. The manifest key (`computeSegmentPlanHash`) includes the hash of the **whole composition**, so editing any scene invalidates every segment. It also cuts by frame count, not at scenes. It is good for **crash recovery** of a long render, so turn it on for that. We copy its manifest and validation pattern. |
| **HyperFrames distributed `plan` / `renderChunk` / `assemble`** (exported from `hyperframes/api`, used by its Lambda and Cloud Run backends) | Writes a plan directory (`plan.json`, `meta/chunks.json`, `meta/encoder.json`). Each chunk renders independently, with GOP = chunk size so every chunk starts on an IDR frame, and `assemble` stitches the MP4. | It shows that frame-range rendering of one compiled project works in HyperFrames. Chunks are still cut by size or estimated cost and tied to one plan hash, so there is no per-scene reuse. Take the **encoder rules**: identical encoder JSON for every chunk, and an IDR frame at each chunk start. |
| **Manim** (partial movie files) | Each `play()` call is encoded to its own small file, named by a hash of what it renders, and a cache hit skips the render. A final concat joins the partials. | This is the model we want, with one call per scene. Their bug report ([ManimCommunity/manim#4896](https://github.com/ManimCommunity/manim/pull/4896)) is the warning: a hash that is not stable (ordering or races) silently re-renders everything. Our key must be canonical JSON. |
| **Av1an** (scene-based parallel encoding) | Splits at scene cuts, encodes chunks in parallel, records progress in `done.json`/`chunks.json`, resumes by skipping finished chunks, `--keep` keeps temp files, and joins with ffmpeg, mkvmerge or IVF. | Cut at scenes, store a done-manifest, resume by skipping chunks, join with stream copy. |
| **Remotion** ([distributed rendering](https://www.remotion.dev/docs/distributed-rendering), [Lambda](https://www.remotion.dev/docs/lambda/how-lambda-works)) | Every frame is a pure function of frame number and props. Chunks get a `frameRange`, and audio is the hard part (AAC joins only at 1,024-sample multiples). | No runtime state across chunks, and **audio is never chunked**: mix it once after the join. |
| **Revideo / Helios** | Split the timeline across N browser processes, then join with the ffmpeg concat demuxer. Helios notes that small chunks pay browser start-up and ffmpeg start-up overhead. | Keep chunks well above about 10 s. Single short scenes are grouped. |

Sources: [HyperFrames rendering guide](https://hyperframes.heygen.com/guides/rendering),
[HyperFrames engine](https://hyperframes.heygen.com/packages/engine),
[Manim scene_file_writer](https://docs.manim.community/en/stable/_modules/manim/scene/scene_file_writer.html),
[Manim deep dive](https://docs.manim.community/en/stable/guides/deep_dive.html),
[av1an-core concat](https://docs.rs/av1an-core/latest/src/av1an_core/concat/mod.rs.html),
[Revideo renderVideo](https://docs.re.video/api/renderer/renderVideo),
[Helios distributed rendering](https://mintlify.wiki/BintzGavin/helios/rendering/distributed-rendering),
[Remotion faster Lambda (seamless AAC)](https://www.remotion.dev/blog/faster-lambda). HyperFrames
internals were read from the installed `hyperframes` 0.8.216 bundle
(`dist/chunk-*.js`: `segmentManifest.ts`, `distributed/plan.ts`, `renderChunk`).

## Design: scene chunks with a content-addressed cache (Manim's model, Av1an's manifest)

```
full plan ─► from_scene_plan (once, whole video: tally "from", ids, timings resolved)
         ─► group scenes into chunks (scene cuts only, ≥ ~10 s each)
         ─► per chunk: slice the compiled layers, rebase to t=0, hash
                 ├─ cache hit  → reuse  cache/<hash>.mp4
                 └─ cache miss → compile chunk project → hyperframes render (video only, 6 workers)
         ─► ffmpeg concat (stream copy) ─► mux narration + music + SFX once ─► caption overlay ─► final
```

### 1. Compile once, then slice

`from_scene_plan` already resolves derived state across scenes: the tally chip's `previous_tally`,
per-index ids and timings. So the full `Composition` is built once from the full plan, and each chunk is a
**time slice of the compiled layers**, rebased to 0. This is the same thing
`overlay_burn.chunk_graph` already does for caption `RenderGraph`s, and both can share one helper. A chunk
then never has to recompute state across a cut. This is Remotion's "pure function of the frame" rule,
which we get for free.

### 2. Cache key = what is actually drawn

Hash the canonical JSON (`sort_keys`, fixed float rounding) of:

- the chunk's sliced, rebased layers (this already contains the tally "from" value, text and styles);
- the **content hash** of every file a layer references (stock clips, stills, fonts), not its path;
- width, height, fps, the encoder settings, the HyperFrames version and a `ve` render-schema version number.

Because the hash covers the compiled output and not the script, it catches indirect changes, for example a
change in scene 3's tally that changes scene 4's chip. Scenes that did not change hash the same. This is
Manim's partial-movie key, kept stable by being canonical.

### 3. Where to cut

- Cut only at scene boundaries. Group neighbouring scenes greedily until a chunk is at least about 10–15 s
  long (Helios's overhead lesson). Short title cards ride along with the next scene.
- A small chunk means a small re-render on a cache miss. A 2–3 scene chunk (about 30–60 s) is a good default.
- Captions are not in these chunks. They stay the separate overlay pass, so where captions fall does not
  constrain the cuts. This **replaces the earlier "burn captions per chunk" answer**: the overlay is
  already chunked, and it can get the same cache (§6).
- Nothing in `from_scene_plan` crosses scenes today (no transitions). The planner must refuse to cut inside
  any future transition or straddling layer, using the same `straddled()` check as `overlay_burn.plan_chunks`.

### 4. Rendering a chunk

- Each chunk is its own small HyperFrames project rendered with the existing NVIDIA profile (`--gpu
  --browser-gpu --workers 6`). Run chunks **one after another**, since each already uses 6 workers and
  about 2.2 GB VRAM. Parallelism stays inside HyperFrames.
- **Video only:** chunks drop narration `<audio>` and the music bed. Audio is never chunked (the Remotion
  lesson).
- **Encoder:** identical settings for every chunk, from one encoder spec (the HyperFrames distributed
  rule). Each chunk is a separate encode, so its first frame is an IDR frame and stream-copy concat is safe.
  Use a fixed `--keyframe-interval`, so a cached chunk and a fresh chunk match exactly.

### 5. Join, audio, QA

- `ffmpeg -f concat -safe 0 -c copy` over the chunk files gives `rendered_video.mp4`.
- Audio: build the full narration track once from the per-scene clips at their plan timings, then the music
  bed, then SFX. The existing `mix_background_music` and `mix_sfx_into_video` run on the joined video as now.
- **Seam check:** the joined frame count equals the sum of the planned frames. Also compare one frame each
  side of every seam against a fresh single-frame `hyperframes snapshot`. The existing gates run unchanged.

### 6. Cache store and manifest

- Location: `projects/<job>/render_cache/<hash>.mp4` plus `manifest.json`, which lists each chunk's
  index, scene ids, frame range, hash, bytes, frame count and time. Before reuse, re-validate each entry
  the way HyperFrames does: the file exists, the bytes match and the probed frame count matches.
- **Caption chunks** get the same treatment later. Key each `overlay_burn` chunk on its caption layers and
  size, and cache the PNG directory or an encoded alpha chunk. A script edit to one scene then re-burns
  only that scene's captions. The final overlay composite (one FFmpeg pass, NVENC) still runs over the
  whole video, at minutes for 9:20.
- Cleanup: `ve cache prune --job <job>` keeps only the hashes in the latest manifest.

### 7. Crash recovery

Inside a chunk, set `HF_SEGMENTED_CAPTURE=true` and `--resume` for chunks longer than about 2 min. That
is the HyperFrames-native recovery, and a killed render resumes from finished segments. At our level, the
manifest is the resume: re-running `ve explain` with the same `--job-id` reuses every finished chunk (Av1an's
`done.json`).

## Expected gain (estimates, to be measured)

| Case | Today | With the chunk cache |
|---|---|---|
| First render (9:20) | ~5 min scenes + ~9 min captions | same, plus a few seconds of join and hashing |
| Fix one scene's visuals | ~5 + ~9 min | about 30–60 s chunk render + join + overlay composite (~3–4 min with the caption cache) |
| Fix one line of narration text | full re-run | that scene's narration clip plus its chunk, if its duration is unchanged. A changed duration shifts later scenes' start times only, and rebased slices still hash the same. |

The last row is why slices are rebased to t=0. Moving a scene later in the timeline does not change its key.

## Open questions

1. **Narration mux:** is the per-scene clip timing in the plan exact enough to build one narration track
   outside HyperFrames? It should be, since `per_scene_timings` measures each clip. Verify against a
   current render with a waveform diff.
2. **Stock clip seeking:** a chunk that starts mid-way through a stock clip must seek the clip's
   `source_start` with the offset applied. Check how the slice rebases a video layer's in-point.
3. **Reuse the HyperFrames distributed API instead of separate projects?** `plan` and `renderChunk` take
   chunk ranges only from their own sizing, and `plan.json` is hashed, so not today. Revisit if HyperFrames
   adds explicit chunk boundaries.

## Out of scope

- Cloud or distributed rendering (the bottleneck is Chrome capture on one machine).
- Changing the HyperFrames capture method.
- Footage-pass speed (parallel stock search and judge) has its own plan.
