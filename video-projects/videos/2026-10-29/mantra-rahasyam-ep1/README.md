# మంత్ర రహస్యం — Episode 1 (The Secret of the Mantra)

143 s (v2; v1 was 180 s), 16:9, 1920×1080, Telugu, for the Mantra Shakti channel. Built 2026-10-29
as an end-to-end test of the self-hosted pipeline, before any face-LoRA work.

**Deliverables:** `mantra_rahasyam_ep1.mp4` (−14.8 LUFS, peak −1.3 dBFS) and
`mantra_rahasyam_ep1.en.srt` (English subtitles, upload separately).

## Story

Adapted from the user's 90 s blueprint, expanded to 180 s. The channel's chant is
a male voice, so **Abhinav's father Raghava** plays the channel's
*Mahamrityunjaya Mantra 108 Times* (`j4dxjdQKaD4`) and chants along. One cycle
(11.5 s, source 25.7–37.2 s) is used once, in s09, lip-synced to him. Added
scenes: family flashback, the accident, the grandfather's shrine, the nurse,
Raghava's thanks.

## How each part was made

| Part | Tool | Where |
|---|---|---|
| Script, shot list | `shots.json` (25 shots) | — |
| Voices (16 lines, 1,221 chars) | Sarvam `bulbul:v3` te-IN: narrator `ratan`, Raghava `mani`, doctor `gokul`, Abhinav `aayan`, Lakshmi `rupali` | API |
| Character refs (5) + keyframes (26 incl. 2 retakes) | PoYo `nano-banana-2` / `nano-banana-2-edit` | API |
| Motion (7 clips) | Wan 2.2 I2V `wan22_i2v`, ~213 s a clip | RunPod RTX PRO 4500 (no 5090 free) |
| Lip-sync (6 shots incl. the chant) | InfiniteTalk 480p, 158–489 s a shot | same pod |
| Stills (11) | ffmpeg zoompan push-ins and pans | local |
| Score and SFX | synthesized drone, divine pad, heartbeat, monitor beeps, boom; ducked under voices | local |
| Title card | HTML rendered by headless Chrome (ffmpeg drawtext mis-shapes Telugu) | local |
| Edit, mix, .srt | `edit/assemble.py` | local |

Scripts: `edit/tts.py`, `edit/images.py`, `edit/render_gpu.py`, `edit/assemble.py`.

## Cost

| Item | Cost |
|---|---|
| PoYo images (31) | $1.24 |
| Sarvam TTS (~2K chars incl. voice samples) | ~$0.07 |
| RunPod pod, 63 min at $0.72/h | $0.76 |
| fal uploads | ~$0 |
| **Episode total** | **~$2.07** (budget was <$8) |
| Ongoing: RunPod volume, now 130 GB (adds the 720P lip-sync base) | ~$9.10 a month |

## v2 re-render (same day)

| Fix | How |
|---|---|
| Lip-sync 720p instead of 480p | InfiniteTalk on the Wan 2.1 I2V 720P base (`infinitetalk_720`), 1280×720 |
| Motion judder | RIFE 4.9 interpolation, 16 → 32 fps (`interpolate` workflow, ~20 s a clip) |
| Deity murals on hospital walls | 11 keyframes retaken with a plain-hospital style (s03–s07, s09, s14–s17); s04 retaken again so Raghava keeps his grey hair |
| Raghava's eyes open while chanting | s09 keyframe and prompt now have his eyes closed |
| Pacing | Every shot = its line + 0.5 s (0.25 s before, 0.25 s after); silent beats 2–3.5 s. 180 s → 143 s |

GPU: the 32 GB RTX PRO 4500 ran 720p InfiniteTalk at ~7 min a window because it had
to swap model blocks to system RAM. On a 96 GB RTX PRO 6000 with no block swap it
was ~2.3 min a window.

| v2 cost | |
|---|---|
| 11 keyframe retakes (PoYo) | $0.44 |
| RTX PRO 4500, 71 min (s14, RIFE, s04/s05 at 720p) | $0.85 |
| RTX PRO 6000, 31 min (s09, s15, s16, s17 at 720p) | $1.30 |
| **v2 total** | **$2.59** |
| **Episode total, v1 + v2** | **~$4.66** |

## What still needs improving

| # | Gap | Effect | Fix |
|---|---|---|---|
| 1 | ~~Lip-sync at 480p~~ | Fixed in v2 (720p) | — |
| 2 | ~~16 fps judder~~ | Fixed in v2 (RIFE, 32 fps) | — |
| 3 | 11 of 25 shots are stills with camera moves | Lower production value than full motion | More motion clips (~$0.04 each on the pod) |
| 4 | Faces depend on references | Mostly held; LoRAs would lock them | Per-character face LoRAs |
| 5 | ~~Deity murals on hospital walls~~ | Fixed in v2 (per-shot style) | — |
| 6 | ~~Raghava's eyes open~~ | Fixed in v2 | — |
| 7 | Score is synthesized | Functional, not cinematic | ACE-Step or a music API cue |
| 8 | **Some dialogue was a bit off** (user, after v2); Telugu TTS not checked line by line | Wrong pronunciation, emotion or timing on some lines | From Ep 2: the user reviews every voice line before any GPU work (see below) |
| 9 | ffmpeg drawtext can't shape Telugu | Needed a Chrome workaround | Engine renders text via HyperFrames (Chrome) |
| 10 | RTX 5090 sold out in EU-RO-1 all day | 32 GB cards crawl at 720p lip-sync | Use a 96 GB RTX PRO 6000 ($2.49/h) for 720p dialogue; PRO 4500 is fine for motion, RIFE and images |
| 11 | Pipeline is ad-hoc scripts | Not repeatable at 2 episodes a week | Route B engine work (feasibility report §11) |

## Decisions for the next episodes (user, 2026-10-29)

- **720p lip-sync is the standard.** It is clearly better than 480p; render it on a
  96 GB RTX PRO 6000 (~$0.10–0.12 per 3.2 s window). 480p only as a cheap draft.
- **Pod sizing:** RTX PRO 4500 (32 GB, $0.72/h) for keyframes, Wan motion and
  RIFE; RTX PRO 6000 (96 GB, $2.49/h) for 720p dialogue. Details in
  `runpod-comfyui/README.md`, "Pod sizing".
- **Two review checks** (`scripts/drama_review.py`, enforced by the scripts in `edit/`):
  1. **Dialogue:** the user validates every line in `review/dialogue.md` before TTS.
  2. **Voice emotion:** after TTS, the user plays each take in `review/voices.html`
     and confirms the emotion **before** keyframes, motion, lip-sync or the edit.
  Episode 1 is not approved, so re-running its stages now stops at check 1. A fix at that stage
  costs a fraction of a cent; after lip-sync it costs ~$0.25–0.50 a line.
- **Pacing:** 0.5 s of silence between lines; a shorter episode is fine.
- **Workspace:** after each job, delete superseded takes, QA previews and
  intermediates; keep finals, inputs and scripts.

## Keyframe trial: self-hosted Qwen vs PoYo (2026-10-29)

All 24 scene keyframes re-made with **Qwen-Image-Edit 2511 (8-step Lightning)** on
RunPod, from the same PoYo character portraits and prompts (`edit/qwen_frames.py`):
one-character shots get a face crop + the full portrait, 2–3 characters one portrait
each, no-character shots prompt only. Output `frames_qwen/`; blind A/B sheets in
`edit/compare/` (key in `key.json`, not yet revealed).

| | Cost | Speed |
|---|---|---|
| Qwen, self-hosted | ~$0.41 pod time (6 min RTX PRO 6000 + 13 min RTX PRO 4500) ≈ $0.017 an image | ~31 s an image warm on the PRO 4500 |
| PoYo `nano-banana-2-edit` | $0.04 an image ($0.96 for 24) | ~45–70 s |

**Verdict (user, blind):** 13 of 24 equal (54%), PoYo better on 8 (33%), Qwen better on 3
(13%); "a complete mix, in some cases PoYo seems slightly better". By shot type:

| Shot type | PoYo better | Equal | Qwen better |
|---|---|---|---|
| 2–3 characters | 2 | 1 | 0 |
| 1 character (of which lip-sync start frames: PoYo 3, equal 3) | 5 | 7 | 2 |
| No characters | 1 | 5 | 1 |

Decision: PoYo stays the default for shots with faces, above all multi-character
shots and lip-sync start frames; self-hosted Qwen is acceptable for shots with no
characters. Per-character face LoRAs are the next step to close the face gap.
Ratings and winners: `edit/compare/result.json`.

## Lip-sync with several people in frame: OmniHuman vs InfiniteTalk (2026-10-29)

InfiniteTalk made every visible person talk in s04, s15 and s16. OmniHuman 2.0 (PoYo)
re-rendered them from the same keyframe and audio (`edit/omnihuman_trial.py`, clips in
`clips_omni/`). In the blind A/B (`edit/compare_lipsync/`) the user picked **OmniHuman on
all three** ("all the shots B was good"). It cost 306.7 credits, about $1.53.

**Rule from now on:** dialogue shots with more than one person in frame use OmniHuman.
Single-person shots stay on InfiniteTalk 720p. Cost estimate: feasibility report §12.

## Voice trial: Sarvam vs Smallest.ai (blind, 6 lines, 2026-10-29)

Six lines from Episode 1, each with three takes: Sarvam bulbul:v3 (the takes used in the
episode) and two Smallest.ai Lightning v3.1 voices. All three used the same text and pace setting
and were loudness-matched (`edit/voice_trial.py`, `voice_trial/`).

| Line | Sarvam | Smallest.ai |
|---|---|---|
| s03 narrator | **best** | venkat **best**, teja fast |
| s04 doctor | **best** | prashanth fast, lokesh very fast |
| s05 Lakshmi sobbing | slow | nandini ok |
| s15 doctor shocked | ok | lokesh ok, prashanth ok |
| s16 Lakshmi joy | slow | lavanya ok, nandini fast |
| s17 Abhinav eerie | ok | harish ok, arun slightly fast |

**Verdict:** neither provider was clearly better on voice or emotion; the difference was pace.
- Smallest.ai speaks about 2× faster at the same `speed` and doesn't pause at "...". Sarvam's
  pauses won the two long lines.
- Sarvam's Lakshmi (pace 0.9) is too slow on emotional lines.
- One Smallest.ai mispronunciation: prashanth said "శ్రమ్మించండి" for "క్షమించండి".

Sarvam stays the default for now. Fixes to try: Lakshmi's Sarvam pace 0.9 → 1.0, and Smallest.ai
at speed ~0.75 with the "..." pauses inserted as silence. Smallest.ai's voice cloning (one fixed
voice per character) is still worth testing if it matches Sarvam on pace.

### Round 2 (blind, 2026-10-30): pace matched

Smallest.ai speed was calibrated to each Sarvam take's length, with 0.45 s pauses at "...".
Sarvam's Lakshmi was re-voiced at pace 1.0 (`edit/voice_trial2.py`, `voice_trial2/`).

| Line | Sarvam | Smallest.ai Pro | Smallest.ai v3.1 |
|---|---|---|---|
| s03 narrator | **good** | ok (shankar) | bad (venkat) |
| s04 doctor grave | **good** | ok (manohar) | bad (lokesh) |
| s05 Lakshmi sobbing | ok | **good** (sravani) | bad (lavanya) |
| s15 doctor shocked | ok | ok | ok |
| s16 Lakshmi joy | bad | **good** (sravani) | ok |
| s17 Abhinav eerie | **good** | ok (charan) | bad (harish) |

Score (good 2, ok 1, bad 0): Sarvam 8, Pro 8, v3.1 2.

**Decision:**
- Sarvam stays the voice for the narrator and the male characters.
- **Lakshmi moves to Smallest.ai Pro `sravani`**, which was good on both her emotional lines.
- Smallest.ai v3.1 is dropped.

**Rules for Smallest.ai:**
- Calibrate speed per line to a target length: its speed setting is uneven, ranging 0.57–1.6 here.
- Join pieces under 12 characters to the next one: on their own, v3.1 babbles and Pro returns silence.
- Run a speech-to-text check on every take.

### Round 3 (blind, 2026-10-30): ElevenLabs v4 with emotion tags

ElevenLabs `eleven_v4` was tested with native Telugu library voices: Bharadwaj (narrator), Vagmin
(doctor), Vijaya (Lakshmi) and Kaka (Abhinav). Each line got one inline tag taken from its intended
emotion, e.g. `[sobbing, desperate]`, and speed was matched to the Sarvam length. It ran against
round 2's Sarvam and Smallest.ai Pro takes (`edit/voice_trial3.py`, `voice_trial3/`).

| Line | ElevenLabs v4 | Sarvam | Smallest.ai Pro |
|---|---|---|---|
| s03 narrator | **good** | ok | ok |
| s04 doctor grave | **good** | ok | ok |
| s05 Lakshmi sobbing | **good** | bad | ok |
| s15 doctor shocked | **good** | ok | average |
| s16 Lakshmi joy | **good** | bad | ok |
| s17 Abhinav eerie | **good** | ok | bad |

Score (good 2, ok 1, bad 0): **ElevenLabs 12/12**, Smallest.ai Pro 5, Sarvam 4.

**Decision:** ElevenLabs v4 voices every character. This replaces round 2's split between Sarvam and Smallest.ai.
- All six takes passed a speech-to-text check, and no tag was spoken.
- The trial used 755 characters.
- Speed only goes down to 0.7, so long whispered lines come out shorter than Sarvam's; allow for that in timing.

## v3 (2026-10-30): ElevenLabs voices, OmniHuman for multi-person shots, chant doubled

`mantra_rahasyam_ep1.mp4` is now 147.1 s, 1080p, −14.2 LUFS (peak −1.4 dBFS), with English subtitles in
`mantra_rahasyam_ep1.en.srt`. A 720p phone preview is in `review/`.

**Voices.** All 20 spoken lines are ElevenLabs `eleven_v4` (`edit/tts.py`), each with an emotion tag:
- Narrator: Bharadwaj, calmed to ~30% of the characters' emotion (stability 0.8, soft tags such as "calm, a hint of
  suspense"), at the user's request.
- Doctor: Vagmin. Lakshmi: Vijaya. Abhinav: Kaka. Raghava: Sai Datta.
- Both review checks were approved by the user (`review/approvals.json`). Every take passed a speech-to-text check.

**Lip-sync.** The new voices changed every line's timing, so all dialogue shots were re-rendered from new drive audio
(`edit/drive.py`):
- s04, s15 and s16 (more than one person in frame) on OmniHuman 2.0, about 319 credits (~$1.60).
- s05 and s17 (single person) on InfiniteTalk 720p, on an RTX 5090 with `block_swap=10`: 519 s and 667 s.
  `block_swap=0` ran out of memory on 32 GB. That default is now fixed in runpod-comfyui. The L40S was sold out.

**Chant.** At the user's request the channel's mantra cycle plays twice. The chant clip is repeated to match, each copy
trimmed to one cycle, with a 0.3 s breath between. The chant shot is now 23.8 s.

**Cost of v3:** $2.66 (OmniHuman 318.8 credits = $1.59, RunPod 5090 ~39 min = $0.77, ElevenLabs ~3.7k chars ≈ $0.30). Full breakdown below.

**Bug found and fixed:** the first review reel delayed only the left channel (`adelay` without `:all=1`), so each
voice played twice, 400 ms apart. The episode mix was never affected; the reel builder in `scripts/drama_review.py`
now delays all channels.

**Narration comparison** for future episodes (Sarvam narration + ElevenLabs characters): a blind A/B is in
`voice_trial4/`, waiting for the user's ratings.

## Final cost analysis (v3, 147 s)

**A. Spent on this episode, v1 to v3: $7.32** (budget was under $8).

| Run | What it bought | Cost | Still in the final video? |
|---|---|---|---|
| v1 | 31 PoYo images: 5 portraits and keyframes | $1.24 | yes, apart from the 11 frames retaken in v2 |
| v1 | Sarvam TTS | $0.07 | no, replaced by ElevenLabs |
| v1 | RTX PRO 4500, 63 min: Wan motion, 480p lip-sync | $0.76 | motion clips yes, lip-sync no |
| v2 | 11 PoYo keyframe retakes | $0.44 | yes |
| v2 | RTX PRO 4500, 71 min: s14 motion, RIFE, s04/s05 at 720p | $0.85 | s14 and RIFE yes; s04/s05 no |
| v2 | RTX PRO 6000, 31 min: s09, s15, s16, s17 at 720p | $1.30 | s09 (the chant) yes, the rest no |
| v3 | ElevenLabs v4, ~3.7k characters incl. one narrator redo and the echo tests | ~$0.30 | yes |
| v3 | OmniHuman 2.0: s04 143.2, s15 86.1, s16 89.5 credits | $1.59 | yes |
| v3 | RTX 5090, ~39 min: s05 and s17, plus two out-of-memory attempts and idle time | $0.77 | yes |

About $2.6 of that went on work that was later replaced: the Sarvam voices, two rounds of lip-sync and the
out-of-memory runs.

**B. Trials, not in the video: ~$2.00.** These were the OmniHuman trial ($1.53), Qwen keyframes ($0.41) and
ElevenLabs voice trials ($0.06). Smallest.ai ran on its free credit.

**C. Cost to make exactly this video again, in one clean pass: ~$4.10 (~$1.67 per finished minute).**

| Part | Basis | Cost | Share |
|---|---|---|---|
| Dialogue, OmniHuman (3 multi-person shots, 13.8 s) | actual credits | $1.59 | 39% |
| Images (5 portraits + 24 keyframes, PoYo) | 29 × $0.04 | $1.16 | 28% |
| Dialogue, InfiniteTalk 720p (s05, s17, one chant cycle) | 9 windows × 240 s on a 5090 | $0.71 | 17% |
| Motion (8 Wan 2.2 clips + RIFE) | ~1,250 s on a 5090 | $0.41 | 10% |
| Voices (ElevenLabs v4, 1,753 chars incl. tags) | $0.08 per 1k | $0.14 | 3% |
| Pod boots (2 batches) | ~2 min each | $0.08 | 2% |
| Chant, score, SFX, title, edit, subtitles | own recording, synthesized, local | $0 | — |

**Per-unit rates measured here:**
- Lip-sync per second of dialogue: OmniHuman $0.115 (with the 4 s minimum), InfiniteTalk 720p ~$0.026. That is ~4.4×.
- Keyframe: $0.04 on PoYo, ~$0.017 on Qwen self-hosted (fine for shots without faces).
- Motion clip: ~$0.045 on a 5090. Voice line: ~$0.007.
- Ongoing: the RunPod volume (130 GB) costs ~$9.10 a month whether or not anything renders.

**Where the money goes, and what to change:**
1. Multi-person lip-sync costs the most. Framing listeners off-screen or from behind would move those shots to
   InfiniteTalk at about a quarter of the cost.
2. Start the RunPod pod only after the API jobs finish. The pod idled ~8 min during the OmniHuman renders (~$0.15).
3. `block_swap=10` is now the 720p default, so the out-of-memory retries (~$0.18) won't recur.
4. Character-free keyframes can go to Qwen (~$0.02 saved each).
