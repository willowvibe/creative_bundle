# మంత్ర రహస్యం: Episode 1, restart (2026-10-30/31)

Telugu drama for the Mantra Shakti channel, rebuilt from scratch on the user's notebook notes. 16:9, English
`.srt`. Expected length about **5:00–5:15** (39 lines, 208 s of speech, the chant ×3 at 35 s, 0.5 s gaps, short
action shots). Series assets and character bibles: `../../mantra-rahasyam/`. Design notes and decisions:
`docs/DRAMA_FEASIBILITY_2026-10-29.md` §13.

> **2026-11-02: v2 is finished and is the template for the next episode.**
> - Deliverables:
>   - `mantra_rahasyam_ep1_v2_final.mp4` (5:16.88, 1080p25)
>   - `v2/promo/` (thumbnail + 3 Shorts)
>   - `v2/youtube_chapters.txt`
>   - `v2/subtitles/*.srt` (te/en/hi/ta)
>   - `v2/dub/{hi,ta}/` audio tracks
> - Cost: 3,514.6 PoYo cr ($17.57) + 55 Flow credits; RunPod abandoned at ≈ $1.19.
> - The as-built pattern, stage by stage, is in `docs/DRAMA_PRODUCTION_PLAYBOOK.md` (skill: `drama-episode`).
>   Copy `edit/v2_*.py`, `tts.py`, `score.py` and `sfx.py` for Episode 2.
> - Rule for next time: **dub audio only after the final cut is approved.** Here the dubs came last, after every cut
>   change.
> - The notes below run in chronological order. The v1 restart sections (video stage, cut, sound) describe the
>   superseded RunPod build.

> **2026-11-04: uploaded to YouTube as private** https://youtu.be/jsF4rYisfPM (Mantra Shakti), from
> `/mnt/data2/Youtube/Mantrasakthi/mantra-rahasyam/ep01/` (`publish.yaml`, `publish-log.json`,
> `UPLOAD_CHECKLIST.md`). On the video: hi/ta/kn dub audio, te/en/hi/ta/kn subtitles, en/hi/ta/kn titles and
> descriptions, per-language thumbnails (Flow re-lettered from `v2/promo/thumbnail_flowtext_clean.jpg`, 0 credits),
> tags. YouTube added an English auto-dub by itself. Waiting on the user's review before it goes public; the
> playlist isn't created yet. How it was done: playbook §8 (thumbnails) and §9b (upload).

> **2026-11-01: this episode is treated as the test run for drama pipeline v2.** Defects found in its files —
> 16 fps motion clips judder in the 25 fps cut, every clip is lanczos-scaled from 720p to the 1080p master,
> Wan motion is damped by the 4-step LoRA at full strength — are filed as #323–#325, and the episode may be
> re-rendered (stills and clips) after the v2 pilot scene. Plan: `docs/DRAMA_PIPELINE_V2_2026-11-01.md`;
> evidence: `docs/DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md`.
>
> **v2 images stage prepared (2026-11-01):** `edit/v2_prompts.py` → `v2/visuals.json` + `v2/IMAGE_PROMPTS.md`:
> 81 visuals in serial grammar (80 stills + the reused Flow Shiva clip), 29 one-window lip-sync shots, audio reused
> as-is. Not generated yet; next is generation on Flow / agy / PoYo and the image gate.

## Gates (`scripts/drama_review.py status <this folder>`)

| Gate | State |
|---|---|
| 1 script (`script.md`) | approved 2026-10-30 |
| 2 dialogue (`shots.json`) | approved; two rounds of Daddy's Telugu edits, Shiva's last line rewritten by Harish |
| 3 audio (`audio/lines/`) | approved |
| 4 images (`refs/`, `frames/`) | **in review** on the board |
| 5 videos (`clips/motion/`), lip-sync (`clips/lipsync/`) + SFX | rendered 2026-11-01, 31/31 RunPod + 6/6 PoYo, no failures — **in review** on the board. Cut assembled 2026-11-01 (5:19.5); score + 27 SFX cues laid 2026-11-01 |
| 6 final cut (`*.web.mp4`) | published 2026-11-01 at 5:19.5, I −15.0 LUFS / TP −1.1 dBTP; re-published the same day after the score came down 3 dB and the monitor beep was re-cut — **in review** on the board |

Review board: https://drama-review.ngharish-conti.workers.dev (personal links in `review/reviewer_links.txt`,
git-ignored; the owner link is built from `.env`). Reviewers: Harish (owner), Sruthi, Mother, Daddy, Pawan.

## What's where

| Path | What |
|---|---|
| `script.md` | the approved script (scenes, characters, first dialogue draft) |
| `shots.json` | lines with emotions, characters, style (everyday + divine), picks, 55 visuals with engine per shot |
| `edit/tts.py` | ElevenLabs v4 voices; one unique voice per character |
| `edit/refs.py` | character reference options (Nano Banana Pro for the main cast, Qwen for minor) |
| `edit/shotlist.py` | the visual plan (`FIXES` holds rewritten prompts) |
| `edit/frames.py` | scene frames: Kling O3 by default, Qwen for dark beats (`DARK`), `--pro` Nano Banana Pro, `--qwen` |
| `edit/clips.py` | the video stage on RunPod: Wan 2.2 motion + InfiniteTalk lip-sync, sharded across type-specialised pods |
| `edit/lipsync_poyo.py` | OmniHuman 2.0 on PoYo for the six anguished lines InfiniteTalk smiles through |
| `edit/expression_fix.py` | face-only redos on Nano Banana Pro for stills whose expression contradicts the line |
| `edit/ab_blockswap.py` | the `block_swap` A/B that produced the 2.2–2.4× figure |
| `edit/consistency_test.py`, `edit/bakeoff.py`, `edit/v09_gpt_test.py` | the face test, the model bake-off, the GPT blood test |
| `clips/provided/c15a_shiva_flow.mp4` | the user's Google Flow clip for Shiva's last line, used whole (logo removed; original in `source/`) |
| `edit/flow_test/` | Veo 3.1 Lite vs Wan 2.2 blind test (key in `key.json`: tie) |
| `edit/_superseded/` | frames replaced by redos, kept for comparison |
| `refs/_alternates/` | unpicked reference options and the archived luminous Shiva images |

## Cast and voices (ElevenLabs `eleven_v4`)

Narrator Bharadwaj · Abhinav **Dhruva S** · Lakshmi Vijaya · Raghava Sai Datta · Doctor **Kaka** · Tatayya Srirama V ·
Nurse Swathi Janaki · Nurse Rani Seeta · Passerby Jayesh M · Yama 1 Rakesh · Yama 2 Maddy · Lord Shiva **Vagmin**
(his last line uses the Flow clip's own voice). The narrator runs at ~30% emotion.

Faces (picked on the board): Abhinav A, Lakshmi B, Raghava B, doctor B, Tatayya B, Nurse Swathi A, Nurse Rani B,
passerby B, Yama 1 A, Yama 2 B. Shiva: two frames of the Flow clip.

## Shot plan (55 shots)

| Engine | Shots |
|---|---|
| InfiniteTalk 720p (RunPod), one face each | 29 lip-sync — minus the 6 anguished ones, which go to OmniHuman |
| Wan 2.2 (RunPod) | 19 ordinary motion, including the new accident beat v08b, **plus a draft of each Flow beat** (a Flow take can replace the draft later), so the batch renders 25 motion clips |
| Flow Omni 1.1 Flash | 6 extraordinary: v08, v34, v35, v36, v40, v43 (~72 credits) |
| User-provided | v53 (Shiva's last line) |

Frame redos so far: v08, v08b (new), v09 (Qwen, with blood), v12 (free Flow frame), v13, v33, v40, v42, v43, v49,
v52, v14–v18 (Kling O3, grief beats; v18 is the user's PoYo task image 9SKWBJ09W9XP9337, two people on the scooter).

Lip-sync engine trial (`edit/avatar_test.py`, `edit/avatar_compare.sh`): the same still and the same line
(`c05b_line`) on Kling Avatar 2.0 Pro and OmniHuman 2.0, run twice (`v15_run1_*` on the older worried still,
current `v15_*` on the final crying one).
- **Kling Avatar 2.0 Pro** (1904×1072, 30 fps): re-frames wider than the still and adds body motion (a hand
  gesture in run 1). Charged **70 credits ($0.35)** for the 4.3 s clip although the catalogue says "fixed 14
  per request" — the task's own `credits_amount` is the authority, and it works out at ~14 credits per started
  second of audio (5 s billable). Budget 70 credits/shot, not 14.
- **OmniHuman 2.0** (1276×720, 24 fps): holds the still's framing and background, more static body. 21 credits
  per output second → **89.5 credits ($0.45)** for the same line.
- **Expression**: run 1 (worried still) made both models smile mid-clip; run 2 (crying still) kept the grief —
  tears, red eyes and a pained mouth throughout. So the still's emotional intensity anchors the performance,
  and every grief shot must be generated with a crying still, not merely an unsmiling one.
- **Cost verdict**: the two are within 1.3× of each other (70 vs 89.5 credits), so OmniHuman's framing fidelity
  is nearly free next to Kling — while Kling's wider re-frame breaks a tightly composed shot. 29 lip-sync shots
  would be ~2,030 credits (~$10) on Kling.

## Video stage (`edit/clips.py`, RunPod)

One process renders the whole batch and shards it across pods; the ledger `clips/_runs.json` makes a
stopped run resume instead of paying twice, so a batch can be killed and relaunched freely.

**Pods are type-specialised.** A 32 GB card holds one model family at a time — InfiniteTalk ≈21 GB
resident, Wan 2.2's fp8 high+low pair ≈28.6 GB — so mixing both on one pod evicts weights mid-batch.
`--pod a=infinitetalk,b=infinitetalk,c=wan22_i2v,d=wan22_i2v` runs each half on its own pods and splits
each group round-robin (the shot list is sorted worst-first, so both pods of a group get the same mix
of grief and ordinary lines instead of one inheriting the slow tail).

**`--steps 4`, not the graph's 6.** InfiniteTalk node 3 loads the LightX2V adapter, a 4-step
distillation with `cfg=1.0`: six steps spend a third of the sampling budget re-refining a latent the
LoRA was trained to finish in four. This is alignment with the adapter's own recipe, not an
optimisation experiment. Rollback if a clip looks soft: delete it, re-render at 6 steps, ~$0.15.

**Scaling is by pod, not by server.** Two ComfyUI servers on one card measured 316 s/window against
159 s solo — exactly 2.0×, so aggregate throughput is 1.0×. Diffusion sampling saturates the SMs;
there is no VRAM headroom to sell. Two or four pods is the efficient fleet size, three is dominated
(one workflow type gets a single pod and runs the whole time). Delete a pod as soon as its stage
lands — in a 4-pod run the lip-sync pair typically idles well before the motion pair.

**What this batch cost — done 2026-11-01, 31/31 shots, no failures.** ≈ $2.60 billed against a $2.90–3.70
budget. 42 min on four pods then 23 min on two = 3.61 pod-hours × $0.72, of which **2.85 GPU-hours actually
rendered (79% utilisation)**; the 0.76 pod-hour gap is boot, model loads and the tail where one pod waits for
the last two-window clip.

| | Budgeted | Actual |
|---|---|---|
| Cold start + idle, 4 pods | $0.88 | $0.55 |
| Lip-sync, 6 clips / 11 windows | $0.92–1.39 | $0.79 (361 s/window → $0.0721) |
| Motion, 25 clips | $1.10–1.40 | $1.26 (81-frame clips $0.0428, median 215 s) |
| **Total** | **$2.90–3.70** | **≈ $2.60** |

Two unit costs came out of it. InfiniteTalk lands at **361 s per window**, 6% better than the modelled 386 s.
Wan 2.2 at 81 frames is **214 s ($0.0428)**, dead on the model — but the seventeen 81-frame clips finished
between 209 and 219 s while every non-81 clip paid a second window: 85 frames 256 s (+20%), 121 frames 426 s
(2.0×). `wan_frames()` pads the dialogue and rounds up, so 0.8 s of unseen tail bought a whole extra window on
six of 25 shots, **$0.15 — 12% of the motion line**. Snap to 81 next time. The arithmetic behind all of this,
and why the cheapest *per hour* card is not the cheapest *per render*, is in
`runpod-comfyui/docs/GPU_ECONOMICS.md`.

**Next episode, before spending on pods:** one 48 GB card (L40S $1.09/h or RTX 6000 Ada ~$0.74/h) at
`--swap 0`, on a known lip-sync clip. `block_swap=0` measured 2.2–2.4× on a 96 GB card and 48 GB is
the smallest size that fits the model resident, so ~$1 of experiment should land near
$0.033–0.052 per window against today's $0.077 — about half.

Six anguished shots (v15, v20, v22, v24, v25, v39) went to `edit/lipsync_poyo.py` instead: InfiniteTalk
opens the mouth with both rows of teeth, which reads as a grin on grief however the still and prompt
are set. OmniHuman 2.0 from the same still holds the framing and the pained mouth. Delivered 2026-10-31:
**692.16 credits (~$3.46)** for the six, plus one v20 retake carried in the ledger's `prior`. The guard in that
script now skips a shot that is both on disk *and* has a recorded `credits`, so re-running it on a finished stage
does not quietly re-render all six.

## Cut (`edit/assemble.py`)

`plan | segments | audio | srt | final | all`. 55 visuals, 39 spoken lines, 5:19.5. Output
`mantra_rahasyam_ep1.mp4` (1080p25 master, 226 MB) and `mantra_rahasyam_ep1.web.mp4` (720p, 67 MB) for the board.
Subtitle text is each line's `en` in `shots.json`.

**A lip-sync clip was rendered *from* its line**, so the mouth starts on the line wav's first frame. Cross-correlating
each clip's embedded audio against its source wav gives r = 1.000 at lag 0.000 s for both engines, which fixes the
rule: the voice starts **on the cut**, and the slot is the line plus a 0.25 s tail. A motion shot has no mouth to
match, so there the picture leads and the voice comes in 0.25 s later. Placing lip-sync audio 0.25 s into the slot —
the obvious reading of "half a gap before each line" — puts every one of the 29 lip-sync shots a quarter-second out.

**Slots are quantised to whole frames** (`round(d*25)/25`) before anything is placed. A segment is an integer number
of frames, so a sub-frame slot makes the picture drift against the audio a little more at every cut.

| Rule | |
|---|---|
| lip-sync | real time, never slowed — the mouth is on the voice; hold the last frame if the clip is short |
| motion with lines | slot = lines + 0.5; slowed up to 1.5× to fill it, held on the last frame for any remainder |
| motion, no lines | keeps its whole clip: there is nothing to time it to, and the motion is the point |
| v53 | the user's Flow clip with its own voice lifted out, real time, 0.5 s hold |

**The chant is the spine of the divine section.** Three cycles of `mantra_cycle.wav` joined with an 80 ms crossfade
(34.34 s), starting on v32's lipsync clip and running over v33–v36, which are stretched 1.11× so the third cycle lands
exactly as the Yama doothas speak. Raghava chants, Lakshmi prays, the light breaks, the doothas arrive — one mantra
across it.

**Delivery level.** The mix is mastered to −14 LUFS / −2.0 dBTP, but the number that ships is the one measured on
the decoded MP4. An AAC encoder rings above its source peak — 1.7 dB at 128k, 1.4 dB at 192k, 1.1 dB at 256k on
this material, whose 800 Hz monitor beep is a sustained tone — so the first master landed at **−0.3 dBTP**, past
the −1 dBTP streaming target despite a perfectly on-spec mix. Both files now encode at 256k with `volume=-0.3dB` of
measured headroom, and `loudnorm`'s −2.0 dBTP (instead of −1.5) is the other half of that: it buys the headroom the
encoder needs without spending loudness. Delivered: **I −15.0 LUFS, TP −1.1 dBTP**. The 720p review copy takes its
sound from `mix.wav`, never from the master's AAC — re-encoding an already-lossy track rings twice, and doing that
once put the web copy at −0.1 dBTP.

## Sound (`edit/score.py`, `edit/sfx.py`)

**Score.** Three instrumental beds on PoYo's Suno-backed `generate-music` — `a_house` at 0:00 (household warmth
turning to dread), `b_mantra` at 3:02 (tanpura, temple bells, low strings, one quiet undertrack so the chant stays
the loudest thing in the room), `c_return` at 4:06 (the reunion). 20 credits a track, **60 credits ≈ $0.30**. They
overlap by 2 s, each fade-out is pulled to its section's real end, and the whole bed is ducked under the voice with
`sidechaincompress` at **−17 dB** gain (`SCORE_DB`, `audio --score-db` to audition another) — a mastered music track
sits at −14 LUFS, i.e. exactly as loud as the finished mix, so it has to come down before it is a bed at all. The
first pass used −14 dB and the bed read as surging back at every 0.5 s gap between lines; 3 dB lower was picked by
ear off the reunion section.

**Effects.** 27 cues: 21 from Freesound **CC0 only** (each file's own licence record re-read on download; nothing
needs crediting, so the series stays commercial) and 6 from earshot's generated bank for the designed hits and
shimmers no stock library carries. The bed is one ffmpeg pass into `edit/sfx_bed.wav`; it is **not** ducked — the
levels are drawn under the voice by hand, and a sidechain would pump the room tone at every line.

Levels are set by **measured target**, not a fixed offset. Stock recordings differ by up to 35 dB (the birds bed
is 35 dB quieter than the monster rumble), so each cue names either an RMS the used segment should hit (`home_birds`
−36 dBFS, `hosp_atmo` −38) or a peak a hit should reach (`crash` −8, `defib` −11), and `place()` measures the trimmed
segment and corrects to it. The first pass, with hand-guessed offsets, left the ambience at −73 dBFS: inaudible.

**Looping a source that does not end where it begins.** The monitor beep file is 3.323 s and stops *inside* a beep,
so repeating it raw fired a premature beat at every seam — measured gaps `0.97, 0.78, 0.16, 0.30, …` where the rest
were ~0.95 s. Its four beeps land at 0.26 / 1.23 / 2.16 / 3.12 s, so one cycle is **2.86 s**: `loop_span=(0.24, 2.86)`
starts and ends 20 ms inside the silence either side of a beat, and the wrap interval then equals the source's own
0.96 s instead of adding 40 ms every turn. A naive `(0.2, 2.9)` still left a repeating 3-beat pattern (0.97, 0.93,
**1.00**) — the loop length has to be a whole number of source periods, not a round number. The bed is now one
continuous cue that ramps +6 dB into the flatline, replacing a fade-out plus a second cue that restarted 0.1 s later.

| Beat | Sound |
|---|---|
| the morning that ends it | birds, a plate set down, the scooter starting |
| v08 the accident | street bed, the hit, a long low tail |
| the crowd, 108, the ride | walla, a distant siren |
| the call home | the phone ringing under v14 |
| v18, v24 the two prayers | temple bells |
| the hospital | corridor tone, monitor beeps |
| v28 no pulse | the tone going flat, then the defibrillator |
| v31 the flashback | a reverse swell |
| v33 the light breaks | a shimmer |
| v37, v38 the doothas | a low rumble, chains |
| v41 Shiva speaks | a deep hit and a shimmer |
| v44 the room after | the tone flat again for 4.7 s, then the beep returns at v45 |


## Spend so far

ElevenLabs ~$0.23 · PoYo images ~920 credits (~$4.60, including the tests) · PoYo lip-sync trials 319 credits
(~$1.59: 2 × Kling 70 + 2 × OmniHuman 89.5) · Flow 10 credits · RunPod ~$0.24 before this batch, plus
**~$2.60 for the 31-shot video stage** (4 PRO 4500 pods at $0.72/h, 3.61 pod-hours billed, $2.05 of it
rendering) and **$3.46 for the six OmniHuman lip-syncs** (692.16 credits — PoYo credits run at ~200 credits to the
dollar, not the 179.5 the ~537-credit estimate assumed) · **60 credits (~$0.30) for the three score beds**.
All pods deleted 2026-11-01; nothing is billing now. Episode total so far ≈ **$13.05**.
Still to come: ~72 Flow credits for the six extraordinary beats. Assembly itself is free and local — the 27 SFX
cues cost nothing (Freesound CC0 and earshot are both free).

### v2 stills (2026-11-01)
- All 80 stills are in `v2/frames/` (55 Flow Nano Banana Pro `.jpg`, 25 PoYo `.png`); s80 is the provided Shiva clip. Rejected takes are in `v2/frames/_takes/`. Review sheets: `v2/stills_sheet_1.jpg`, `v2/stills_sheet_2.jpg`.
- `v2/frames_clean/` holds the copies to build from: Gemini sparkle removed from Flow stills with `ffmpeg -vf delogo=x=2606:y=1390:w=66:h=66` (mark sits at x 2615–2663, y 1399–1447 on 2752×1536). A faint soft patch remains on textured backgrounds.
- flow-mcp bug seen twice: when a Flow job fails quietly it saves the previous job's image (s45 == s74, s29 == s62 byte-for-byte). Dedupe-check every collected batch.
- `edit/v2_sample.py` resume re-downloads any task whose file is missing; drop superseded entries from `v2/frames/_tasks.json` after moving a take.

### v2 animatic (2026-11-01)
- Image gate approved on the board (Harish, 2026-11-01 17:24 UTC) for the 79-still version.
- `edit/v2_animatic.py plan|segments|audio|final|all` → `v2/animatic/mantra_rahasyam_ep1_v2_animatic.mp4` (720p, 4:56.9,
  81 shots, voices + chant only; `animatic_phone.mp4` is the 540p copy). Stills get camera moves and a label (id + route).
- `v2/animatic/edit_list.json` is the locked-to-be edit list: every shot's start/end (whole frames at 25 fps) and every
  line's start. GPU clips are cut to these slots; score and SFX get re-cued to these line times.

### v2 videos stage (2026-11-02): PoYo + Flow
- RunPod tried and dropped (pods slow to boot): ≈ $1.19 spent. Route: PoYo OmniHuman 2.0 lip-sync (29), Veo 3.1 Lite motion
  (29), Kling O3 for the two shots with the little girl (s11, s12: Veo refused s11, Flow never finished either upload),
  Flow Omni 1.1 Flash hero clips (s56, s57, s62, s66). Runners: `edit/v2_poyo_clips.py` (ledger `v2/clips/_poyo_runs.json`),
  Flow map `v2/flow_hero_map.json`. Clips: `v2/clips/{lipsync,motion,hero}/`, rejected takes `v2/clips/_takes/`.
- PoYo spend 3,282.6 cr ($16.41), confirmed by the account balance 10,757.4 → 7,474.8: OmniHuman 2,701.4, Veo 468.0
  (+43.2 rejected first takes s45/s64/s78), Kling 70.0. Two PoYo server failures (s14, s50) were refunded.
- Flow: 34 credits hero + 21 credits for three lip-sync tests (`v2/clips/tests/`: Flow speaking Raghava's Telugu lines,
  with and without the Flow character "Raghava" / voice Gacrux).
- Assembly notes: s78 use the bright first ~2.5 s; OmniHuman clips are 4 s minimum (trim to slot); Flow clips carry the
  sparkle mark (delogo); ball is red in s11, white in s12.
- Cut (2026-11-02): `edit/v2_assemble.py all` → `mantra_rahasyam_ep1_v2.mp4` (1080p25, 4:56.88, −14.8 LUFS, −1.9 dBTP)
  and `mantra_rahasyam_ep1_v2.web.mp4` (720p, on the board's final stage via the v2 folder). Score/SFX beds re-timed to
  v2 in `SFX_V2`/`SCORE_V2`, rendered by score.py/sfx.py into `v2/cut/`. Not yet: SeedVR2 (clips upscaled with lanczos).
- Finished episode (2026-11-02): `edit/v2_graphics.py all` → `mantra_rahasyam_ep1_v2_final.mp4` (1080p25, 5:16.88 = 4 s cold
  open + cut + 15 s end card, −15.0 LUFS / −2.0 dBTP) and `.web.mp4` (on the board's final stage). HyperFrames
  compositions in `v2/graphics/*.html` (title, 6 supers/name tags, mantra word-highlight, end card with the channel
  avatar `v2/graphics/assets/channel_avatar.jpg`), rendered to transparent MOV in `v2/graphics/renders/`. User changes
  folded in: 1 s ring-only pause before the nurse (s20, `PAUSE_BEFORE`), s04 reuses s40's curd clip (`CLIP_OF`).
  YouTube chapters: `v2/youtube_chapters.txt`. Still open: thumbnail, Shorts.
- Promo (2026-11-02): `edit/v2_promo.py` → `v2/promo/`: thumbnails `thumbnail_flowtext_poster.jpg` / `_clean.jpg` (Flow
  Nano Banana Pro painted the scene and the Telugu lettering, spelling checked), earlier HyperFrames-lettered
  `thumbnail_flow.jpg` / `thumbnail.jpg`; Shorts `short_accident.mp4` (36.2 s), `short_shiva.mp4` (39.4 s),
  `short_mantra.mp4` (34.8 s), 1080x1920, blurred-fill letterbox + HyperFrames title/CTA overlay. Crash SFX now at
  0:45.1 in the final (user: 0.5 s then 3 s earlier).
- Save shot (2026-11-02, user: the episode never showed Abhinav saving the girl): s12 is now `PARTS` in
  `edit/v2_assemble.py` = Kling O3 save clip (`v2/clips/motion/s12_save.mp4`, 0–2.9 s; Veo refused it) + Veo lorry
  charging at camera (`s12_truck.mp4`, 2.8–3.9 s), same 4.0 s slot; crash SFX moved to the lorry (0:48.59 in the final).
  Old near-miss take: `v2/clips/_takes/s12_nearmiss_kling.mp4`. Cost 54.4 PoYo cr.
- Dubs + subtitles (2026-11-02, user: Hindi and Tamil audio tracks for YouTube multi-language audio):
  translations `v2/dub/translations.json` (39 lines each; the Sanskrit mantra is not dubbed), on the board for native
  review as `2026-10-30-mantra-rahasyam-ep1-v2-hi` / `-ta` (dialogue stage; folders
  `videos/2026-10-30/mantra-rahasyam-ep1-v2-{hi,ta}/`). Next after approval: ElevenLabs eleven_v4 with the same cast
  voices, fitted to the Telugu line lengths, mixed with the existing score/SFX/chant beds into one audio track per
  language. Subtitles: `edit/v2_subs.py` → `v2/subtitles/mantra_rahasyam_ep1.{te,en,hi,ta}.srt`, timed to the final
  upload (cold open included).
- Dub audio (2026-11-02, scripts approved by the user in chat): `edit/v2_dub.py all <hi|ta>` voiced every line with the
  Telugu cast (edit/tts.py now takes `language=`), fitted to the Telugu slots (atempo ≤1.25; hi median 0.98, ta 1.00),
  mixed with the same chant/score/SFX and the final's cold-open/end-card audio. Tracks:
  `v2/dub/<lang>/mantra_rahasyam_ep1_<lang>_audio.wav` (master) + `.m4a` (upload copy), 316.88 s, ≈ −15 LUFS.
  Review videos on the board (`…-v2-hi` / `…-v2-ta`, final stage). Upload: YouTube Studio → Languages → Add audio.
- Kannada dub (2026-11-04, user: Kannada track like Tamil; script approved in chat): `kn` in
  `v2/dub/translations.json` (39 lines; board `…-v2-kn`, dialogue stage), voiced with `edit/v2_dub.py all kn` (same cast,
  pace, stability, emotion; eleven_v4 `language_code=kn`, ~3,400 chars), fit median 1.00× / max 1.18× (18 lines sped,
  none re-voiced), no line runs into the next. Track `v2/dub/kn/mantra_rahasyam_ep1_kn_audio.{wav,m4a}`, 316.88 s,
  −15.0 LUFS, −2.3 dBTP (hi −15.1, ta −15.2); review video on the board (`…-v2-kn`, final stage). Subtitles
  `v2/subtitles/mantra_rahasyam_ep1.kn.srt` (51 cues). Mix fix: on today's ffmpeg the mix came out 60 ms short
  (`loudnorm` drops its tail, and the WAV writer closed the timestamp gap), so `mix()` now pads `[main]` and fills gaps
  with `aresample=async=1` before trimming to the upload length. Re-mixing hi today gives 316.82 s without the fix; the
  hi/ta tracks on disk are 316.88 s and were not touched.
