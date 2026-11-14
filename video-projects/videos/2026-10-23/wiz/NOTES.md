# wiz-explainer — real build record (2026-10-23)

Task 11 smoke build for the `explainer_stills` pipeline. Every number below is
from the final passing run unless marked otherwise.

## Command

```bash
uv run ve explain videos/2026-10-23/wiz/script.md \
  --job-id wiz-explainer \
  --music-path projects/human-body/music/bed.m4a \
  --auto-approve
```

Providers actually used: Sarvam TTS (`bulbul:v4-flash`, voice
`shubh_en_narration`, `en-IN`), PoYo image (`nano-banana-2-lite`), and a local
music bed (no AI music charge). Credentials were checked by environment-
variable name only: `SARVAM_API`, `POYO_API_KEY`, plus the existing repo
`.env` file. No values were printed.

Output: `outputs/wiz-explainer/final.mp4`.
Work root: `projects/wiz-explainer/`.

## Still reuse (printed before the estimate gate)

```
still reuse: 2 still scenes -> 1 renders (1 saved, 5 credits / $0.025)
  cluster scene_2    scenes scene_2, scene_5         overlap 0.44   -> render of scene_2
```

Report: `projects/wiz-explainer/plan/reuse_report.json`
(`still_scenes: 2`, `renders: 1`, `saved: 1`, cluster owner `scene_2`,
scenes `scene_2` + `scene_5`, overlap `0.444`, no unclustered scenes).

## Estimate (and gate)

```
Estimated costs for wiz-explainer (7 scenes, ~42s music bed):
Provider             Model                  Count   Est. Cost
---------------------------------------------------------------
sarvam_tts           bulbul:v4-flash        1       $0.0230
poyo_ai              nano-banana-2-lite     1       $0.0250
---------------------------------------------------------------
TOTAL                                       2       $0.0480
```

`projects/wiz-explainer/plan/cost_estimate.json` records the same two line
items and `total_cost: 0.048`.

## Final QA (all ten checks PASS)

```
canvas        PASS  1080x1920 vs 1080x1920 9:16 (+0.00%) @30fps
loudness      PASS  -14.2 LUFS (target -14)
true_peak     PASS  -3.4 dBTP (ceiling -1)
duration      PASS  53.4s (target 50, +6.9%)
scene_starts  PASS  7/7 within 0.35s (worst 0.12s)
captions      PASS  99.0% of words captioned
stills        PASS  2 scenes -> 1 renders (report says 1)
silent_gaps   PASS  longest gap 0.82s
tail          PASS  0.9s after the last word
cost          PASS  $0.05 of $0.05 estimate
```

Raw inputs in `projects/wiz-explainer/qa.json`: durations 53.433 s,
loudness -14.22 LUFS, true peak -3.39 dBTP, caption coverage 0.990,
max silent gap 0.820 s, tail 0.893 s, cost $0.048 of $0.048 estimate.

`ffprobe` confirms 1080x1920 H.264 at 30 fps, AAC audio, 53.433 s, 25.8 MB.

Scene windows (from `projects/wiz-explainer/plan/scene_plan.json`):

| scene | kind | window (s) |
|---|---|---|
| 1 | typographic `big_stat` | 0.00–8.58 |
| 2 | still (racks) | 8.58–15.20 |
| 3 | typographic `bar_compare` | 15.20–20.32 |
| 4 | typographic `definition` | 20.32–30.98 |
| 5 | still (reuses scene 2) | 30.98–37.52 |
| 6 | typographic `sequence` | 37.52–48.54 |
| 7 | typographic `array` | 48.54–53.42 |

## Frame inspection

Frames were extracted from the final MP4 and inspected with the `Read` tool.

1. **Text stays inside the safe area.** In `frame_1`, `frame_3`, `frame_5`
   (scene 1), `frame_24`, `frame_26` (scene 4), `frame_38` (scene 6) and
   `frame_51` (scene 7) all authored text — big stat, sub, term/gloss, steps,
   array — sits well inside the top and side margins, with the caption band
   below it in the lower third. Nothing is clipped on the 1080x1920 canvas.
2. **Typographic scenes are genuinely typographic and animated.** None of
   frames 1/3/5/24/26/38/51 contains an image or a frozen still: each is flat
   cream with the layout's own type. Motion is present rather than frozen:
   frame-to-frame grayscale differences over the layout area are 0.95 mean /
   1.08% changed pixels (frames 3→5) and 1.52 mean / 1.66% changed (24→26),
   consistent with the slow camera drift. Staggered entry/reveals are visible
   in `frame_38` (step 1 still fading in ~0.4 s into scene 6; later steps not
   yet on screen) and `frame_51` (array cell 17 caught mid-reveal in pink
   while cells 3, 7, 8 and 12 are already red).
3. **Captions are in the lower third with the spoken word highlighted.**
   Each caption sits in a band around y≈1430 of 1920, in white Montserrat
   with the active word in amber `#FDB813` — the `karaoke` `CaptionStyle`
   preset's `highlight_color`, emitted to the composition as `--ve-hl`, e.g.
   `frame_1` "billion that is what" with "billion" highlighted, `frame_12`
   "job is to watch the" with "job", `frame_24` "security means seeing every"
   with "means", `frame_34` "racks, the same cloud," with "cloud", and
   `frame_51` "buying software. It is buying the" with "software". `frame_3`
   lands on the exact boundary frame t=3.000 between caption lines 0 and 1 and
   shows no caption; frames 89 (2.967 s) and 91 (3.033 s) both render their
   line normally. This is a single-frame (33 ms) clip-boundary dropout, not a
   visible caption gap.
4. **The reused still is the same picture as scene 2.** `frame_34` (scene 5)
   and `frame_12` (scene 2) show the identical photograph: the same two black
   server racks, the same red "…ATACOM SOLUTIONS ID #882105 …T REMOVE" rental
   sticker taped to the left rack, the same yellow cable bundles top-right and
   the same rack labels. Only the Ken Burns crop differs, as expected from
   scene-local still animation — it is a reuse, not a near-miss. The
   composition confirms it: `composition/index.html` has two `<img>` clips
   (scene 2 and scene 5) that both point at the same cached PNG
   (`346788de…png`), with per-scene `kb_1`/`kb_4` camera moves.

## Defects fixed during this build

- **Whisper skipped the opening seconds of the narration.**
  `run_explainer_job` passed `initial_prompt=script.narration[:200]`.
  faster-whisper treats a leading transcript as already-spoken context, so the
  transcript started around 16 s, scene durations collapsed (final 38.1 s) and
  QA failed duration, scene_starts and tail. Fixed in
  `apps/cli/src/video_editor_cli/explainer_pipeline.py` to
  `initial_prompt=script.title or None`, with a comment explaining the
  failure mode. Re-verified by transcribing the first 20 s with and without
  prompts: both begin at 0.00 s.
- **silent_gaps FAIL at 1.06 s.** The pause after "billion." in scene 1 was a
  genuine ~1 s breath. The QA threshold was not touched; scene 1's punctuation
  was changed from a full stop to an em dash ("Thirty-two billion dollars —
  that is…"), giving 0.82 s and PASS.
- **Docs path correction.** The brief's snippet pointed at
  `build/reuse_report.json`; the shipped pipeline writes
  `projects/<job>/plan/reuse_report.json`. The docs use the real path.

## Deviations and provider notes

- `ve explain` was run with `--music-path` because AI music providers were
  unusable in this environment:
  - PoYo primary `minimax-music-2-6` now 404s (live model id is
    `minimax-music-2.6`), and the `generate-music` fallback rejects the
    provider's payload (requires `mv` and a `title` when `custom_mode` is set).
  - ElevenLabs `POST /v1/music/generate` returned 401 `insufficient_credits`.
  - OpenRouter Lyria returned 400 `Audio output requires stream: true`, which
    the provider does not send.

  These are provider-configuration defects outside Task 11; the local bed
  keeps the build real (real narration, real generated still, real render,
  real mix) without pretending AI music succeeded.

## Spend

| Item | Cost |
|---|---|
| Sarvam TTS, first generation (09:44) | $0.023 |
| PoYo still, one render for two scenes (09:44) | $0.025 |
| Sarvam TTS, regenerated after the scene-1 punctuation fix (09:51) | $0.023 |
| Local music bed, render, mix, captions, QA | $0.000 |
| **Total for the smoke build** | **$0.071** |

The passing run itself cost $0.048 ($0.023 TTS + $0.025 image, music local),
matching its estimate. Failed attempts reused the cached image and the first
narration, so the extra $0.023 is the only retry cost.

## Known concerns

- The PoYo music provider is stale relative to the live API (model rename and
  payload contract); `poyo_music` as a default will fail until it is updated.
- `--music-provider` is documented in `ve explain --help` as the driver for
  AI music, but there is no `--music-prompt` option on the command; the
  script's `music_mood` supplies the prompt. Docs follow the script-driven
  behaviour rather than the help text's wording.
- Caption coverage is 99.0%, not 100%; the single missed token is a
  punctuation-adjacent word and the check threshold (≥98%) passes.
- One frame at each caption-line boundary (e.g. frame 90, t=3.000 s) renders
  with no caption. It is a 33 ms clip-boundary rounding artifact; playback and
  the QA caption-coverage check are unaffected, but a stricter frame-level
  check would see it.
