# Drama test — "The Wet Letter" (10 s, 16:9, Tamil)

A 10-second scene for testing video models before building the long-form Tamil
drama format. It tests the three hard things in that format: recurring
characters, lip-synced Tamil dialogue, and short motion shots.

**Story:** Latha (the stepmother) catches Nila (the stepdaughter) at the sink
and scolds her. Nila answers softly. The final shot shows her college
admission letter getting soaked beside the sink.

## Shot list

| Shot | Time | Frame | Type | Audio |
|---|---|---|---|---|
| s1 | 0–2 s | `frames/s1.jpg` | motion (image-to-video) | — |
| s2 | 2–5 s | `frames/s2.jpg` | **lip-sync**, Latha | `audio/s2_latha_3s.wav` |
| s3 | 5–8 s | `frames/s3.jpg` | **lip-sync**, Nila | `audio/s3_nila_3s.wav` |
| s4 | 8–10 s | `frames/s4.jpg` | motion (image-to-video) | — |

**Dialogue** (bulbul:v3, ta-IN):

- **l1, Latha** (voice `roopa`): "நிலா! இன்னும் பாத்திரம் கழுவலையா?" ("Nila! You still haven't washed the vessels?")
- **l2, Nila** (voice `priya`, pace 0.9): "இதோ கழுவுறேன் சித்தி…" ("I'm washing them right now, Chithi…")

The rejected takes (`kavitha`, `kavya`) are kept in `audio/` for comparison.
Sarvam's bulbul:v4-flash has no female Tamil voice, so these lines use v3.

## What's in this folder

- `refs/`: character reference portraits (nano-banana-2, 2K). Use them for any model that accepts reference images.
- `frames/`: start frames for each shot (nano-banana-2-edit with the refs, 2752×1536).
- `audio/s2_latha_3s.wav`, `audio/s3_nila_3s.wav`: each line with 0.3 s of silence before it, padded to the shot length (16 kHz mono). Feed these to the lip-sync models.
- `audio/dialogue_10s.wav`: the full 10 s dialogue track, aligned to the shot list.
- `animatic_stills.mp4`: **baseline version A**, stills with a slow push-in plus the dialogue. Every model test should beat this.
- `shots.json`: the full spec, including prompts. `generation_log.jsonl` records models and timings.

## Prompts for the video models

Every test is image-to-video, starting from `frames/<shot>.jpg`.

**s1 (2 s, motion).** Prompt:
> Latha steps forward from the doorway and jabs her finger toward the sink; Nila flinches and turns her head toward her. Slow push-in. Sunlight dust in the air. Photoreal, natural motion, no camera shake.

**s2 (3 s, lip-sync Latha).** Prompt:
> A middle-aged Indian woman in a green silk saree scolds angrily, speaking, head shaking slightly, hand gesturing. Static camera. Photoreal.

**s3 (3 s, lip-sync Nila).** Prompt:
> A young Indian woman in a pink churidar answers softly, speaking, lowering her eyes, a tear forming. Very slow push-in. Photoreal.

**s4 (2 s, motion).** Prompt:
> Hands scrub the steel pot under running water; water splashes onto the soaked envelope; a single tear drop falls and lands on the envelope. Static macro shot. Photoreal.

**Negative prompt** (for Wan-family models):
> blurry, deformed hands, extra fingers, distorted face, text, watermark, subtitles, morphing, flicker, cartoon

## Test matrix

Run the same four shots on each route:

| Route | s1 / s4 (motion) | s2 / s3 (lip-sync) |
|---|---|---|
| **A. Stills baseline** | done: `animatic_stills.mp4` | none |
| **B. Self-hosted** (RunPod / Vast.ai) | Wan 2.2 I2V A14B, LightX2V 4-step distill, 720p | InfiniteTalk (image + audio → video) |
| **C. API, cheap** | PoYo `wan-2-2-fast` | API lip-sync model of choice |
| **D. API, quality** | Kling v3 std / Seedance 2 mini | Kling lip-sync, or Veo 3.1 with native audio (expect the voice to differ from Sarvam) |

**Wan 2.2 settings:**
- Output 1280×720 at 16 fps. Use 33 frames for 2 s shots and 49 frames for 3 s shots (Wan needs 4n+1 frames).
- Downscale the start frame to 1280×720 first.

**InfiniteTalk settings:**
- Input: the shot frame plus its padded 3 s audio file.
- The clip length follows the audio, so each output should be about 3 s.

**What to record for each shot** (B and C only):
- Run time per clip
- GPU type and $/h
- Peak VRAM
- Number of retries
- Notes on face identity, hands, lip-sync, and Tamil mouth shapes

These numbers replace the guessed speeds in the cost estimate.

## Assembling a test

Name each route's clips `s1.mp4` … `s4.mp4` in one folder, then:

```bash
ffmpeg -i s1.mp4 -i s2.mp4 -i s3.mp4 -i s4.mp4 -i ../audio/dialogue_10s.wav \
  -filter_complex "[0:v]scale=1280:720,fps=30,trim=0:2,setpts=PTS-STARTPTS[a];[1:v]scale=1280:720,fps=30,trim=0:3,setpts=PTS-STARTPTS[b];[2:v]scale=1280:720,fps=30,trim=0:3,setpts=PTS-STARTPTS[c];[3:v]scale=1280:720,fps=30,trim=0:2,setpts=PTS-STARTPTS[d];[a][b][c][d]concat=n=4:v=1:a=0[v]" \
  -map "[v]" -map 4:a -c:v libx264 -crf 20 -c:a aac -shortest test.mp4
```

This drops the clips' own audio and uses the Sarvam track, so every route is
judged with the same voices.

## Known flaws in the frames

- s3: Nila's eyes aren't teary yet. Let the video prompt add the tear.
- s4: the envelope looks more like plastic than paper. Acceptable for a motion test.

## Blind lip-sync test (2026-10-29)

`blind_s2.mp4` / `blind_s3.mp4`: five models on the same 4 s line, labelled A–E
(key in `blind_key.json`). Rated by the user without knowing the labels:

| Letter | Model | s2 | s3 | Cost per second |
|---|---|---|---|---|
| A | InfiniteTalk, self-hosted (RunPod 5090, 480p) | good | good | ~$0.01 |
| E | OmniHuman 2.0 (PoYo) | good | good | $0.105 |
| D | LongCat-Video-Avatar (fal, 720p) | acceptable | **fail**: expression only, no dialogue | $0.30 |
| C | Wan 2.2 clip + Sync lipsync-2 | acceptable | acceptable | $0.05 + clip |
| B | Wan 2.2 clip + Sync lipsync-2-pro | acceptable | acceptable | $0.083 + clip |

HeyGen Avatar IV was not run: the API needs wallet credit.

Reproduce: InfiniteTalk via `rpc run infinitetalk` and the hosted trials via
`scripts/lipsync_trial.py`, both in `/mnt/data2/git_repos/creative_repos/runpod-comfyui`. The
grids can be rebuilt with that repo's `scripts/blind_compare.py` (`--seed 2910` gives this
letter order). Decision: Route B with InfiniteTalk (feasibility report rev 4, §11).

## Blind motion test (2026-10-29)

`blind_motion_s1.mp4` / `blind_motion_s4.mp4`: PoYo `wan2.2-image-to-video-fast`
against self-hosted Wan 2.2 I2V A14B + LightX2V 4-step (`runpod-comfyui`
`wan22_i2v`, secure RTX 5090), same start frame, prompt and seed. User's ratings:

| Shot | PoYo | Self-hosted |
|---|---|---|
| s1 | went a bit blurry | better |
| s4 | — | good (preferred) |

Self-hosted: 135 s warm per clip (197 s cold), ~$0.045 at $1.19/h against PoYo's
$0.06; output is 16 fps against PoYo's 32 fps (PoYo interpolates).
