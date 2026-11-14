# Drama pipeline v2: the from-scratch recommendation (2026-11-01)

**Status (2026-11-02): the shot grammar (§2) was built and shipped in Ep1 v2; the hosting plan (§3–§5, RunPod
serverless + RIFE/SeedVR2) was not.** The user dropped RunPod mid video stage (pods too slow to boot) and Ep1 v2
was finished on PoYo (OmniHuman 2.0, Veo 3.1 Lite, Kling O3) + Flow (Omni 1.1 Flash) for 3,514.6 PoYo credits
($17.57) + 55 Flow credits. The as-built pattern, stage by stage with measured costs, is
**`DRAMA_PRODUCTION_PLAYBOOK.md`**; read that first. This doc stays as the design record.

*Original status (2026-11-01): proposed, not built; the user asked for it to be documented first.* Open findings are filed as #323–#328 in `docs/ISSUES_BUGS.md`. This doc supersedes the
video-stage parts of `DRAMA_FEASIBILITY_2026-10-29.md` (§11–§12); the evidence behind each choice is
in `DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md`.

## 0. What Ep1 taught (the observations this plan answers)

| # | Observation (measured on the Ep1 files or in Ep1 tests) | Answer in v2 |
|---|---|---|
| 1 | Wan clips are **16 fps** (`wan22_i2v.json` node 16) and `edit/assemble.py` conforms them with `fps=25` → uneven frame repeats, **judder** on every move | RIFE in ep-finish before assembly (#323) |
| 2 | Every clip is 1280×720 and **lanczos-scaled** to the 1920×1080 master → soft | SeedVR2 in ep-finish (#324) |
| 3 | Wan runs LightX2V v1 4-step at 1.0/1.0, cfg 1, a generic prompt → damped, slow-looking motion (community-reported; to confirm blind) | CFG on the high expert + per-shot prompts (#325) |
| 4 | InfiniteTalk animates **every visible face** (blind 3/3) | Speaker masks + silent listener track (#326), keyframe framing rule |
| 5 | InfiniteTalk **grins on grief** however the still and prompts are set | Grief route → OmniHuman; challengers LongCat / Wan→LatentSync / Wan Animate |
| 6 | A still whose expression is weaker than the line drifts (worried → smile; only a crying still held grief) | Keyframe checklist on gate 4 |
| 7 | Wan and InfiniteTalk render in **81-frame windows**; 85 frames cost +20% | Always 81 frames for motion |
| 8 | Pods: retakes pay boot + model load each time; type-specialised fleets; stock fallbacks in EU-RO-1 | Serverless endpoints per model family |
| 9 | 720p dialogue clearly beats 480p upscaled (user, Ep1 rev 5) | No 480p generation for faces |
| 10 | Both lip-sync failures (grief, extra faces) sit in shots a TV serial would not lip-sync at all | Serial shot grammar (§2.1–§2.2) |

Treats Mantra Rahasyam Ep1 as a finished test run and designs the production pipeline as if starting
over. It folds in every suggestion reviewed in `DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md` (§3.5, §4.3,
§4.4) and the measured numbers in `runpod-comfyui/docs/GPU_ECONOMICS.md`. The front half — script →
dialogue → ElevenLabs voices → PoYo music/SFX — is unchanged.

---

## 1. The decisions

1. **The still decides the shot.** Every Ep1 failure that reached the video stage started in the
   keyframe: a worried face that lip-synced into a smile, a second face that InfiniteTalk animated, a dark
   frame. So v2 spends its care on the image gate. The video models only amplify what they are given.
2. **Route each shot to one engine by shot type**, decided at the shot list, not at render time. No shot
   passes through every model.
3. **Self-host what we can control; buy what we can't match.** Open models on RunPod for single-face
   dialogue, ordinary motion and finishing (cheap retries, our own settings, no content filter — the
   accident blood only rendered on uncensored models). Hosted APIs for what open models still lose:
   grief and multi-face dialogue (OmniHuman) and hero shots (Omni Flash).
4. **RunPod serverless for production, pods for R&D.** One image per model family, deployed as a
   serverless endpoint that scales 0 → N and back. Pods only for bake-offs and debugging.
5. **Every clip is finished the same way:** frame rate to 25 fps, then a video upscaler to 1080p.
6. **No 480p generation for anything with a face.** The user already judged 720p clearly better than
   480p upscaled.
7. **Cut it like an Indian TV serial: stills first, lip-sync is the exception** (user's strategy,
   2026-11-01). Dialogue as singles, long reaction holds, inner voice over a closed-mouth face, camera
   moves on places and objects, and *subtle real motion* on every face. Lip-sync only where a character
   is seen speaking, one window at a time. The budget moves to more and better stills (§2.1).

---

## 2. Pipeline

```
script → dialogue → voices (ElevenLabs)                      [fixed, gates 1–3]
   ↓
shot list in serial grammar: shot type + line plan           [new: drives everything below]
   ↓
character refs → keyframes (PoYo), ~2× Ep1's still count     [gate 4: image lint + user review]
   ↓
render by shot type ── local: camera moves, 2.5D parallax, serial zooms (no GPU spend)
                    ── RunPod serverless: ep-talk (lip-sync), ep-motion (living stills + action)
                    ── PoYo API: OmniHuman (rare), Wan Animate, (Veo)
                    ── Flow: hero shots (pre-paid credits)    [gate 5/6: videos, lip-sync]
   ↓
finish  ── RunPod serverless: ep-finish (RIFE → 25 fps, SeedVR2 → 1080p)
   ↓
assemble 1080p25: J/L-cuts, inner-voice echo, score + SFX     [fixed, gate 7: final]
```

### 2.1 Serial shot grammar (new)

Every shot in `shots.json` gets a **shot type**, plus the tags `faces_in_frame`, `speaker`, `emotion`
(from the dialogue gate), `hero`, `content` (e.g. blood). The type picks the engine:

| Shot type | Picture | Motion | Engine / where | Cost |
|---|---|---|---|---|
| **Establishing** (house, hospital, temple, street) | still | slow push/pan, or 2.5D parallax (`layers: auto`, already in the engine) | local | free |
| **Insert** (phone, photo frame, diya, prescription) | still | slow macro push | local | free |
| **Living still**: reaction, listening, inner voice | close-up still | Wan with a "barely moves" prompt (blink, breathing, eyes shift, a tear rolls) + a camera push in assembly | ep-motion | ~$0.04–0.09 |
| **Shock reaction** | close-up still | the serial triple zoom with a whoosh/thunder SFX, in assembly, over a living still | local + ep-motion | ~$0.04–0.09 |
| **Speaking single** (CU/MCU, mouth closed in the still) | still | lip-sync, **one window ≤ 5.06 s** | ep-talk: InfiniteTalk 720p (LongCat 1.5 if it wins E3) | ~$0.08 |
| **Over-the-shoulder dialogue** (speaker's face + listener's back) | still | lip-sync, one face visible | ep-talk | ~$0.08 |
| **Action** (driving, walking, accident, crowd) — cannot be skipped | still | full motion | ep-motion: Wan 2.2 fixed sampling (§3), or Veo if E2 says so | ~$0.05–0.09 |
| **Directed beat** (a precise gesture or breakdown) | still + phone reference | performance transfer | PoYo Wan 2.2 Animate (E8) | price unknown |
| **Flashback / memory** | stills | glow or desaturation, slow crossfades | local | free |
| **Divine / hero** | still | generated | Flow Omni 1.1 Flash | Flow credits |
| *Fallback only:* grief or two-face line that must be seen spoken | still | lip-sync | PoYo OmniHuman 2.0 (or InfiniteTalk masks if E4 passes) | ~$0.45/line |

**Faces never get camera-only motion.** A frozen face under a zoom reads as a slideshow; every face
shot is a living still, a lip-sync or an action clip. Camera-only moves are for places and objects.

### 2.2 Line rules (how spoken lines map onto shots)

1. **Lip-sync shots are capped at one window (≤ 5.06 s; aim for ~4.5 s).** A longer line starts on the
   speaker and continues over the listener's living-still reaction (an L-cut), or opens on the reaction
   and cuts to the speaker for the end (a J-cut). In Ep1, 18 of 34 lines exceed 5 s and 3 exceed 8 s.
2. **Grief, shock and "heart" lines play mostly on the crying/stunned living still.** If the line opens
   the scene, lip-sync only its first 2–3 s. This replaces most OmniHuman grief renders.
3. **Inner voice:** living still, closed mouth, echo/reverb on the voice in the mix. No lip-sync.
4. **Dialogue is shot as singles or over-the-shoulder**, never two faces with one speaking, so
   InfiniteTalk's every-face problem never arises.
5. **At least one visible lip-sync per speaking character per scene**, so the audience keeps believing
   the characters talk.
6. **Pace:** a real motion shot or a lip-sync every 3–4 shots; serial zooms only on genuine shocks
   (overused they turn comic); reaction holds carried by score and room tone.

**Talking while the camera moves** stays a speaking single with a push in assembly; LTX-2.5 only if
E7 passes.

### 2.3 Keyframe rules (gate 4)

The serial grammar roughly **doubles the still count** (Ep1 57 → ~90–120), which is cheap (~$0.0175 per
Kling O3 still) but makes face consistency the main risk: more stills, more chances to drift. The image
gate is where the episode is won. Enforced as a checklist before the user reviews the sheet:
- Models: **Kling O3** default, **Qwen Image 3** for blood, **Nano Banana Pro** for main-cast refs and
  pinned redos ([[poyo-image-models-drama]]). 2K, bright daylight look.
- **Expression matches the line's emotion** at full strength: grief stills actually cry.
- **Lip-sync starts:** mouth closed, face ≥ ~25% of frame height (lip-sync and repair models work on a
  face crop).
- **Listeners** off-screen, turned away or out of focus (singles / over-the-shoulder, §2.2 rule 4).
- **Living-still stills** are close-ups with the emotion fully readable in the frame itself (tears,
  wide eyes), because the motion added on top is deliberately small.
- Same-character refs on every shot; face crop as a reference ("use only for the face"). If drift shows
  up across the larger still count, per-character face LoRAs move up from "later" (§7).

### 2.4 Image prompts: how they are built (Ep1 v2 sheet, 2026-11-01)

The Ep1 v2 shot list and prompts live in the episode: `edit/v2_prompts.py` writes `v2/visuals.json` (machine
input for Flow / agy / PoYo) and `v2/IMAGE_PROMPTS.md` (the review sheet). 81 visuals: 80 stills plus the
user's Flow clip for Shiva's last line, reused whole; 29 lip-sync shots of one window each (119 s, against
188 s of InfiniteTalk clips in Ep1); audio reused as-is, every line and the chant placed exactly once
(the script checks it).

Each prompt is assembled from four independent parts, so a mood never leaks into a face and a reference
never brings its own expression:

| Part | Holds | Why separate |
|---|---|---|
| framing + action | shot size, what the camera sees | per shot |
| expression | brows, eyes, mouth, tears, stated physically; lip-sync stills start mouth-closed (a scream excepted) | the model must not infer the face from the mood |
| identity + wardrobe | who the person is, **no expression words**, plus the outfit for this scene state | the old bible blocks said "warm friendly smile" — that is how grief stills drifted into smiles; the bibles were cleaned the same day |
| scene mood | location, light, palette, atmosphere (per scene, or per place when a scene has two) | shared by every shot in the scene, so the look holds |

Engines suggested per row: `kling-o3` (PoYo, people with references), `qwen` (PoYo, blood), `flow` (Flow
Nano Banana, free, no named people — agy can take these), `flow-omni` (hero clips). Five lip-sync shots
are flagged as grief/fear risks with OmniHuman as the fallback.

---

## 3. Model settings that change from Ep1

| Engine | Ep1 | v2 |
|---|---|---|
| Wan 2.2 motion | LightX2V v1 4-step at 1.0/1.0, cfg 1, generic prompt, 16 fps out, lanczos to 1080p | High expert: LoRA 0.6–0.8 + **cfg 2–3.5**; low expert: LoRA 1.0, cfg 1 (8 steps total, E2 confirms); **per-shot motion prompt**; **always 81 frames**; RIFE + SeedVR2 in ep-finish |
| InfiniteTalk | 720p, `block_swap` 10 on 32 GB | 720p, **`block_swap` 0 on 48 GB** (E5: est. $0.033–0.052/window vs $0.077); speaker masks for multi-face |
| Wan 2.2 living stills | — (not used in Ep1) | Same graph, minimal-motion prompt ("still, only blinks and breathes; no head turn; no mouth movement"), 81 frames |
| Assembly | 720p clips scaled to 1080p, 16 fps conformed to 25 | Only 1080p25 clips arrive from ep-finish; assembly adds camera moves, parallax, serial zooms, J/L-cuts and inner-voice echo, but no longer scales or retimes |

---

## 4. Hosting: why serverless, and how

### 4.1 Why serverless for production

- **Retakes are where pods hurt most.** The gates send back a few shots at a time. On pods each
  retake pays a boot plus a 20–30 GB model load (~$0.11 and several minutes) and someone must remember
  to delete the pod. A serverless request just queues.
- **Parallelism is linear across workers** (measured: two jobs on one card give 1.0×, separate cards
  give 2×). An endpoint at max 4–6 workers renders an episode in about one long clip's worth of
  wall-clock time per worker, with no fleet arithmetic.
- **No pod lifecycle code in the episode path:** no stock fallback loops, no forgotten pods, no
  type-specialised `--pod a=…` shards. The endpoint is the type specialisation.
- `rpc run --endpoint <id>` already sends the same workflows to an endpoint.

### 4.2 What it costs

Serverless GPU-seconds cost more than pod time, **roughly 1.3–2.5× depending on the card** (third-party
figures, e.g. A6000 48 GB flex ≈ $1.22/h; check `runpod.io/pricing` before budgeting). Model load and
the idle timeout are billed. The self-hosted share of an episode is about $5 on pods today, so
serverless adds roughly **$3–6 per 5-min episode**. That buys automation and fast retakes.

If that premium ever matters, the same image runs as a pod: **batch the first full render on pods,
serve retakes from the endpoint.** Nothing else changes.

### 4.3 Endpoints

| Endpoint | Models | GPU (in order) | Workers | Notes |
|---|---|---|---|---|
| **ep-talk** | InfiniteTalk 720p (+ LongCat 1.5 after E3) | 48 GB (L40S / RTX 6000 Ada / A6000), then 32 GB with `block_swap` 10 | min 0, max 4 | Largest spend; 48 GB if E5 confirms |
| **ep-motion** | Wan 2.2 I2V A14B fp8 high+low, LightX2V | 48 GB, then 32 GB | min 0, max 4 | 32 GB fits (measured) |
| **ep-finish** | SeedVR2, RIFE 4.9 | 24–48 GB | min 0, max 2 | Short jobs; cheapest card that fits SeedVR2 |
| ep-repair *(later)* | LatentSync 1.6 or LTX DubIt | 24 GB+ | min 0, max 1 | Only after the InsightFace licence check |

Setup rules:
- **One built image per endpoint** (nodes installed at build time; weights on the network volume, not
  in the image). Pods keep the base64-bundle boot for R&D.
- **Network volume in one data centre that actually stocks 48 GB cards.** EU-RO-1 had none in
  October; moving means copying ~40 GB into a new volume. Check stock first (`get-capacity`).
- **Outputs to object storage** (`BUCKET_*` env, e.g. the R2 account the review board uses): serverless
  returns base64 in the response, and episode-length MP4s hit the response size limit.
- **FlashBoot on, idle timeout short** (default 5 s) during a batch; pre-warmed workers can be evicted
  when GPUs are scarce, so budget a cold start per worker per batch anyway.
- **Execution timeout per endpoint** above the slowest job (two-window InfiniteTalk ≈ 13 min on 32 GB).

---

## 5. Cost of a 5-minute episode (v2)

Priced against **Ep1's actual shot list** (measured from its files): 23 single-face lip-sync clips =
**43 InfiniteTalk windows**, 6 grief lines on OmniHuman (692 credits actual), 25 motion clips, 6 hero
shots, ~70 stills (~90 with redos), 34 spoken lines / 177 s of speech. PoYo credit = $0.005. *est.*
marks figures the pilot (§6) will measure.

| Item | Engine | Where | Cost |
|---|---|---|---|
| ~90 stills incl. redos | Kling O3 / Qwen (blood) / Nano Banana Pro (redos) | PoYo | ~450 cr ($2.25), or ~$0 on Flow's free stills |
| 23 single-face lines, 43 windows | InfiniteTalk 720p | RunPod | $3.30 on pods (measured $0.077/window); $2.30–5.20 *est.* on 48 GB serverless |
| 6 grief lines | OmniHuman 2.0 | PoYo | 692 cr ($3.46), Ep1 actual |
| 25 motion clips | Wan 2.2, fixed sampling (~2× today's compute) | RunPod | $2.15 *est.* pods; $2.50–3.75 *est.* serverless |
| 6 hero shots | Omni 1.1 Flash | Flow | ~72 Flow credits |
| Finishing, ~55 clips | RIFE → 25 fps + SeedVR2 → 1080p | RunPod | ~$1–3.50 *est.* |
| Worker cold starts | 3 endpoints | RunPod | ~$0.50–1 |
| Music + voices | PoYo + ElevenLabs | — | $0.30 + $0.23 |
| **Subtotal** | | | **≈ $11–20** |
| **With ~20% retakes** (Ep1's rework rate) | | | **≈ $13–23, ~$18 mid** |

Low end: first batch on pods, stills on Flow. High end: everything serverless at the top of the
third-party price range. Split at mid: **PoYo ~1,200–1,450 credits** (mostly OmniHuman grief, then
stills), **RunPod ~$6–13** (lip-sync, motion, finishing about a third each), **Flow ~85 credits**
(820 left ≈ 9 episodes of hero shots).

Fixed: network volume 130 → ~180 GB with LongCat + SeedVR2 ≈ **$9–13/month**. One-off: the pilot
**$10–15** plus build time. A 10-minute episode ≈ 2× everything above.

### 5.1 Against the alternatives (same Ep1 shot list)

| Option | Per episode | Flow credits | 1080p finishing included |
|---|---|---|---|
| **Hybrid v2 (this plan)** | **≈ $13–23** | ~85 | yes (RIFE + SeedVR2) |
| All-API A: stills + motion + hero on Flow, lip-sync OmniHuman | ≈ $24 (≈ 4,750 cr) | ~390 | no — 720p; PoYo upscaler price unknown |
| All-API B: everything PoYo except hero | ≈ $30 (≈ 5,900 cr) | ~85 | no |
| Ep1 as built | ≈ $13 incl. tests | ~82 | no — 16 fps judder, lanczos 1080p |

All-API is driven by lip-sync: Flow cannot lip-sync to ElevenLabs voices, so every dialogue shot is
OmniHuman at 21 cr/s (~185 billable s ≈ 3,900 cr). Kling Avatar Pro (~14 cr/s, ≈ 3,100 cr) is cheaper
but was rejected (re-frames the shot).

### 5.2 With the serial shot grammar (§2.1–§2.2)

Re-cut on paper with the line rules, Ep1's lip-sync drops from 23 InfiniteTalk clips (43 windows) + 6
OmniHuman lines to **~20–24 single-window clips and 0–2 OmniHuman lines**; stills grow from ~70 to
~110–140 incl. redos; ~30 living stills and ~10 action clips replace the 25 motion clips.

| Item | Ep1 shape (§5) | Serial grammar |
|---|---|---|
| Stills | ~450 cr ($2.25) | ~700 cr ($3.50) |
| Lip-sync, RunPod | $2.30–5.20 | $1.60–2.90 (~22 windows) |
| OmniHuman | $3.46 | $0–1.00 |
| Living stills + action | $2.15–3.75 (25 clips) | $1.70–3.60 (~40 clips) |
| Camera moves, parallax, zooms | — | free (local) |
| Finishing, cold starts, music, voices | ~$2–5 | ~$2–5 |
| **Per episode incl. ~20% retakes** | **≈ $13–23** | **≈ $10–14** |

The saving is real but not the point: most shots now use the step that rarely fails (a good still
plus subtle motion), and the spend goes where it shows most, into the images. The pilot measures it.

### 5.3 If the pilot's challengers win

| Result | Effect per episode |
|---|---|
| LongCat 1.5 holds grief | 6 OmniHuman lines → ~11 RunPod windows: ~$0.90 instead of $3.46, **−$2.50** |
| 48 GB `block_swap=0` confirmed | lip-sync toward the low end of its range |
| InfiniteTalk speaker masks work | saves OmniHuman on two-face dialogue (Ep1 had none) |
| All of the above | **≈ $11–15 per episode** |

Track **cost per accepted second** (all spend incl. retakes ÷ seconds in the cut) from the clip ledger
to replace these estimates with real numbers.

## 6. Pilot before the episode: "scene zero"

Instead of eight scattered experiments, take **one Ep1 scene** that has a single-face line, a grief
line, a two-person exchange, an inner thought, a vehicle or walking shot and one hero beat. Cut it
**twice**:
- **A: Ep1 shape** (every line lip-synced, motion clips) on the v2 engines;
- **B: serial grammar** (§2.1–§2.2), ~12–15 shots from the same lines.

The user compares A, B and the Ep1 original blind, as whole scenes and per shot. The challengers ride
along on the same shots:

| In the pilot | Decides |
|---|---|
| Cut A vs cut B vs Ep1 | **Whether the serial grammar becomes the default** |
| Living stills vs camera-only moves on two face shots | Confirms the "no camera-only faces" rule |
| Wan fixed sampling vs Ep1 Wan vs `veo-3-1-official` (E2) | Motion engine |
| InfiniteTalk masks on the two-face shot (E4) | Whether the two-face fallback leaves OmniHuman |
| LongCat 1.5 on the single-face and grief lines (E3) | Whether ep-talk switches engine |
| 48 GB `block_swap` 0 (E5) | ep-talk GPU tier |
| RIFE + SeedVR2 on everything (E1) | Finishing settings |
| Wan Animate on the grief beat (E8, optional) | Whether the perform route is worth a phone shoot |

Estimated pilot spend: **~$12–18** for both cuts plus the engineering below. Pass bar: the user prefers
a v2 cut on most shots and none is worse; the preferred cut (A or B) sets the episode structure.

---

## 7. Build order

1. **Shot types + line plan + router** in the episode scripts (§2.1–§2.2); keyframe checklist on the
   image gate; assembly support for camera moves, parallax, serial zooms, J/L-cuts and inner-voice echo
   (all local).
2. **ep-motion and ep-finish** images and endpoints (Wan settings, living-still prompt, RIFE, SeedVR2);
   outputs to R2.
3. **ep-talk** on 48 GB (InfiniteTalk + mask workflow); add LongCat nodes (check the WanVideoWrapper pin
   `088128b2`).
4. **Pilot scene zero**, blind review, lock the routes.
5. **Re-render Ep1** on v2 where the pilot shows a gain (redo stills that break the keyframe rules first).
6. Later, each behind its own test: LatentSync/DubIt repair (after the licence check), LTX-2.5 for
   talking-with-camera-motion, per-character face LoRAs if reference consistency falls short.

## 8. Explicitly not doing

- 480p generation + image upscalers (Real-ESRGAN, Ultimate SD Upscale) for faces: tested and rejected,
  and per-frame upscalers flicker.
- Self-hosting MiniMax H3 (licence excludes EU deployment) or Wan 2.7/3.0 (no open weights).
- Qwen-Image-Edit / FLUX for character continuity (lost the Ep1 blind test; FLUX.1 Dev weights are
  non-commercial).
- An always-on pod, or several ComfyUI servers per card (1.0× measured).
- MultiTalk (InfiniteTalk supersedes it), Wan S2V (16 fps, no edge), Kling Avatar (rejected, re-frames).

## Sources

RunPod: [serverless pricing docs](https://docs.runpod.io/serverless/pricing) ·
[pricing page](https://www.runpod.io/pricing) ·
[serverless vs pods](https://ice-ice-bear.github.io/posts/2026-04-13-runpod-serverless-vs-pods) ·
[volume cache](https://docs.runpod.io/serverless/development/volume-cache.md) ·
[FlashBoot](https://www.runpod.io/blog/serverless-gpu-cold-starts-flashboot) ·
[cold-start case study](https://www.runpod.io/blog/cut-vllm-cold-starts-runpod-serverless).
Models: see the research doc's source list.
