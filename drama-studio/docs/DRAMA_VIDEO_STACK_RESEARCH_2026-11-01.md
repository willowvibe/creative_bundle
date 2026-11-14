# Drama video stack: quality and economics review (2026-11-01)

**Outcome:** the recommendation built from this research is `DRAMA_PIPELINE_V2_2026-11-01.md`. This
doc keeps the evidence: the defects found (§3), the model survey (§4), the three outside proposals
reviewed (§3.5, §4.3, §4.4), the cost scenarios (§5) and the experiment list (§6), which v2 folds into
one pilot scene.

Scope: the GPU half of the drama pipeline — **lip-sync, motion, video generation**. The front half
(script → dialogue → ElevenLabs audio → PoYo music/SFX) is fixed and out of scope.

Inputs: Mantra Rahasyam Ep1 restart (`videos/2026-10-30/mantra-rahasyam-ep1/`), the measured cost
model in `runpod-comfyui/docs/GPU_ECONOMICS.md`, the shipped workflows in `runpod-comfyui/workflows/`,
`docs/POYO_ROUTING.md` (2026-10-22 price refresh), the PoYo catalog snapshot `v1-5bc1c3f780a1`, and web
research (sources at the end). Anything marked *est.* or *unverified* has not been run by us.

---

## 1. Summary

1. **Part of the quality gap is in our pipeline, not the models.** Three defects are measurable in the
   Ep1 files today and are cheap to fix:
   - **Motion clips are 16 fps cut into a 25 fps master.** `wan22_i2v.json` node 16 is `CreateVideo fps=16`;
     `edit/assemble.py` conforms with `fps=25`, which repeats frames unevenly (about every other frame
     shows twice, in an irregular pattern). That reads as **judder** on every camera move and every walk.
     The RIFE workflow (`workflows/interpolate.json`, ~$0.004/clip) exists and is not in the episode path.
   - **720p clips are lanczos-scaled to a 1920×1080 master.** Every Wan and InfiniteTalk clip is 1280×720;
     a resize adds no detail, so the master looks soft at 1080p. A video super-resolution pass (SeedVR2 or
     FlashVSR) fixes this.
   - **Wan runs the LightX2V v1 4-step LoRA at full strength on both experts, cfg 1.0.** This is the setup
     the community most often blames for **slow-motion, damped movement** and flat detail. The prompt is
     also generic (`"Photoreal, natural motion."` in the graph).
2. **InfiniteTalk's remaining failures are known and partly fixable without changing models:** it animates
   every face (try its own multi-person mode with per-speaker masks) and it grins on grief (keep routing
   those lines to OmniHuman).
3. **The one new model worth a blind test for lip-sync is LongCat-Video-Avatar 1.5** (Meituan, MIT,
   May 2026): Whisper audio encoder, 8-step DMD2 distillation, 480p/720p, **two-person dual-audio mode**,
   ComfyUI node pack and GGUF quants. Its authors report it level with InfiniteTalk on single-person shots;
   it is the only open candidate that could take InfiniteTalk's place *and* some of OmniHuman's
   two-person work.
4. **For motion, the cheapest real quality upgrade may be hosted, not self-hosted.** PoYo's
   `veo-3-1-official` is $0.018/s at 720p silent ($0.09 per 5 s clip, native 24 fps). Once Wan is fixed
   (better sampling + RIFE + upscale) it costs about the same per clip, and moving motion off RunPod
   removes half the pod fleet. The user already rated Veo 3.1 Lite equal to Wan in a blind test, so this
   is a cost/ops decision, and Veo needs no interpolation.
5. **Economics stay in the same range.** Today's stack is about **$8 per 5-minute episode** for
   lip-sync + motion; the fixed stack is about **$10–12**; all-PoYo would be about **$21**. Details in §5.

---

## 2. What we run today (measured)

| Stage | Engine | Output | Unit cost | Notes |
|---|---|---|---|---|
| Lip-sync, one face | InfiniteTalk (Wan 2.1 base) on RunPod, 720p, LightX2V 4-step | 1280×720, **25 fps** | $0.072/window (PRO 4500, 361 s) | 81-frame windows; a 4 s line → 6.12 s clip |
| Lip-sync, 2+ faces or grief | PoYo `omnihuman-2.0` | 1276×720, 24 fps | 21 cr/s ≈ **$0.105/s** (~$0.45/line) | Needs ≥4 s audio (padding) |
| Motion | Wan 2.2 I2V 14B fp8 on RunPod, LightX2V v1 4-step, cfg 1 | 1280×720, **16 fps** | $0.043 per 81-frame clip | Crossing 81 frames doubles the cost |
| Hero shots | Google Flow Omni 1.1 Flash | 720p | 7–12 Flow credits | Short list per episode |
| Master | `edit/assemble.py` | **1920×1080, 25 fps** | — | `fps=25, scale=1920:1080:flags=lanczos` |

Ep1 restart actuals: RunPod batch ≈ $2.60 (6 lip-sync + 25 motion clips), six grief lines on OmniHuman
692 credits ≈ $3.46.

---

## 3. Quality fixes that need no model change

### 3.1 Frame rate (highest impact, near-zero cost)

A 16 fps clip conformed to 25 fps by `fps=25` shows frames for 1 or 2 output frames in an irregular
pattern; the eye reads it as a stutter on any steady motion. Options, best first:

| Fix | How | Cost | Notes |
|---|---|---|---|
| **RIFE ×3 → 48 fps, then `fps=25`** | existing `interpolate.json` (RIFE 4.9) with multiplier 3 | ~$0.006/clip *est.* | Dropping from 48 to 25 is close to even (≈ every second frame), so the residual judder is small |
| RIFE to an exact 25 fps | arbitrary-timestep RIFE (Practical-RIFE `--fps 25`) | similar | Cleanest; needs a different node or a small script on the pod |
| Master at 24 fps | conform InfiniteTalk 25→24 instead | 0 | Shifts the problem to the lip-sync clips; not recommended |

Do this for every Wan clip. InfiniteTalk (25 fps) already matches the master; OmniHuman (24 fps) gets a
light 24→25 conform, which is barely visible.

### 3.2 Resolution

| Upscaler | Strength | Speed | Where |
|---|---|---|---|
| **SeedVR2** (ByteDance, ComfyUI node by numz) | most accurate, temporally stable; can over-sharpen/contrast | slower | RunPod, same pods |
| **FlashVSR v1.1** | fastest; ComfyUI docs time a 10 s 720p→1080p clip at ~41 s | fast | RunPod |
| PoYo `poyo-ai/video-upscaler` | no ops | price not in our snapshot | hosted |

*Est.* cost on a PRO 4500: $0.01–0.04 per clip, **~$1–2 per episode** for ~55 clips. Upscale the
16 fps clip *before* RIFE: it has a third of the frames a 48 fps clip has, so the upscaler does a third
of the work. Order: **Wan 16 fps → upscale → RIFE → assemble**.

### 3.3 Wan 2.2 sampling

The shipped graph runs both experts through the v1 4-step LoRA at strength 1.0, cfg 1.0, 2+2 steps.
Community reports say this kills motion; the usual fixes:

| Variant | Recipe | Cost vs today *est.* |
|---|---|---|
| A. today | high 2 + low 2, LoRA 1.0/1.0, cfg 1 | 1.0× ($0.043) |
| **B. CFG on the high expert** | high: LoRA 0.6–0.8, **cfg 2–3.5**, 4 steps; low: LoRA 1.0, cfg 1, 4 steps | ~2× (cfg doubles the high passes) ≈ $0.08 |
| C. no LoRA on high | high: base model, cfg 3.5, 3–4 steps; low: LoRA, 3–4 steps | ~2–2.5× |
| D. newer distills | Wan2.2-Lightning 250928 or community "dynamic" LoRAs | 1.0× | quality claims unverified |

Also send a **per-shot motion prompt** (what moves, camera move, pace) instead of the graph's generic
text, and keep `--steps` aligned with the LoRA in use.

### 3.4 InfiniteTalk

- **Every face moves:** InfiniteTalk/MultiTalk in Kijai's WanVideoWrapper takes `ref_target_masks` — one
  mask per speaker, with separate audio per mask. Feeding the listener's mask a silent track is the
  documented way to keep their mouth still. *Untested by us;* if it works, most 2-person shots come back
  from OmniHuman ($0.45/line) to InfiniteTalk (~$0.10/line).
- **Grin on grief:** confirmed as a model trait (stills and negatives did not fix it). Keep the
  emotion-based routing to OmniHuman. LongCat's `audio_cfg` (recommended 3–5) is a knob InfiniteTalk does
  not expose the same way — part of why it is worth testing on grief lines.
- **Static camera:** InfiniteTalk cannot move the camera. If a line needs a push-in, add it in assembly
  (slow zoom on the clip) or test LTX-2.5 (§4.1).

### 3.5 Render 480p and upscale to 720p? (reviewed 2026-11-01)

A proposal: generate at 480p, then upscale ×1.5 with Real-ESRGAN or ComfyUI Ultimate SD Upscale
(tile, denoise 0.15–0.25), claiming 40–50% less GPU time at near-720p quality.

- **We already tested this for dialogue and the user rejected it:** "720p dialogue is clearly better than
  480p upscaled" (`DRAMA_FEASIBILITY_2026-10-29.md` rev 5). An upscaler sharpens the pixels it gets; it
  cannot add the mouth shapes, teeth and eye detail the model never drew at 480p. Lip-sync lives in
  exactly those details.
- **Both proposed upscalers work on single images, not video.** Real-ESRGAN treats each frame on its own,
  so fine texture (skin, hair, foliage) shimmers from frame to frame. Ultimate SD Upscale is worse: it runs
  a separate diffusion pass on every tile of every frame, so faces shift between frames even at denoise
  0.2. `RealESRGAN_x4plus_anime_6B` is an anime model and the wrong choice for photoreal footage. Upscalers
  built for video (SeedVR2, FlashVSR, §3.2) exist because of this flicker.
- **The saving is real but smaller than the user's quality bar allows.** Measured: InfiniteTalk 480p
  $0.027/window against 720p $0.072 (2.7× cheaper, more than the claimed 40–50%). Per episode that is
  ~$2.3 on lip-sync and ~$0.5 on motion, about $3 out of ~$8–11.
- **Where 480p does make sense:**
  - *Draft renders for the video/lip-sync gate.* Drafts at 480p, finals at 720p only for approved shots.
    Each approved shot then costs $0.027 + $0.072 if it passes first time, so drafts pay off only when
    more than ~37% of first renders get rejected.
  - *Wide motion shots with no faces* (landscapes, vehicles, establishing shots): Wan 480p plus SeedVR2 to
    720p is worth one blind cell in E2. Faces and dialogue stay native 720p.

---

## 4. Model options (as of November 2026)

### 4.1 Lip-sync from a still + audio

| Model | Open? | Strengths | Risks | Verdict |
|---|---|---|---|---|
| **InfiniteTalk** (current) | Apache-2.0 | Best-scoring single-person open model in two third-party tables (OHBench, LongCat report) | Wan 2.1 base, static camera, grin on grief, all faces move | Keep as baseline |
| **LongCat-Video-Avatar 1.5** | **MIT**, May 2026 | Whisper-large-v3 audio encoder ("smoother lip dynamics"), 8-step DMD2, 480/720p, 2-person dual audio (`para`/`add`), ComfyUI pack + GGUF | VRAM not published on the card (official scripts use 2 GPUs with context parallel; INT8 flag exists); our 2026-10-29 test of LongCat **1.0** on fal failed one of two shots | **Test first** |
| LTX-2.5 (Lightricks) | open weights, free under $10M revenue | Audio-to-video with camera motion; fast | Custom-audio lip-sync is fiddly per the HF threads; ~30–35 GB quantised, 66 GB full | Test only if camera motion on dialogue matters |
| MiniMax H3 (open checkpoints) | community licence | Ref2VA takes audio references; ComfyUI day-0 | **Licence reportedly excludes local deployment in the US, EU, UK, South Korea** — RunPod EU-RO-1 is in the EU. Use hosted `h3-max` instead if wanted | Do not self-host |
| Wan 2.2 S2V 14B | Apache-2.0 | Native Wan 2.2 | 16 fps; no edge over InfiniteTalk in reviews | Skip |
| LatentSync 1.6 / MuseTalk | open | Mouth-only re-sync of an existing clip, keeps the rest of the face — a possible fix for grief (Wan motion clip with crying face → re-sync the mouth) | Lower fidelity at 720p; gated weights (LatentSync) | Optional grief experiment |
| OmniHuman 2.0 (PoYo) | hosted | Holds framing, won 2-person and grief blind tests | $0.105/s | Keep as fallback |
| Kling Avatar 2.0 (PoYo) | hosted | Expressive | Re-frames wider; rejected by user; bills ~70 cr/clip | No |

### 4.2 Motion (image-to-video)

| Model | Where | 5 s clip cost | fps | Notes |
|---|---|---|---|---|
| Wan 2.2 I2V, today | RunPod | $0.043 (+ cold start) | 16 | Rated ≥ PoYo Wan fast, = Veo 3.1 Lite (blind) |
| Wan 2.2 I2V, fixed (§3) | RunPod | ~$0.09–0.12 incl. RIFE + upscale *est.* | 25 after RIFE | Same model, better delivery |
| **`veo-3-1-official`** | PoYo | **$0.09** (720p silent); $0.15 at 1080p | 24 | No pods, no interpolation; user rated Veo 3.1 Lite = Wan |
| `h3-max-turbo` | PoYo | $0.20 (768p) | — | First/last frame only |
| `gemini-omni-1-1-flash` | PoYo | ~$0.28 | — | Top of an Oct-2026 eight-model benchmark; our hero-shot model on Flow |
| `wan3.0-image-to-video` | PoYo | ~$0.50 (720p) | — | Hosted only; Wan 3.0 weights are not open |
| LTX-2.5 | RunPod | *unmeasured*, fast | 24+ | Open; worth a side test with Veo |
| Kandinsky 6.0 Video (MIT, Oct 2026) | — | — | — | ComfyUI support and I2V quality unverified |

Wan 2.2 is still the newest *open* Wan; 2.7 and 3.0 are API-only.

### 4.3 Review of an outside proposal (2026-11-01): LongCat / LTX / MultiTalk / LatentSync

The proposal: replace OmniHuman with open models (LongCat 1.5, LTX LipDub/2.3, MultiTalk) for
still + audio shots; for motion shots, use Wan 2.2 at 480p → LatentSync → 1.5× upscale.

| Claim | Verdict |
|---|---|
| LongCat 1.5 is the closest open model to OmniHuman | **Agree**; it was already our first test (E3). Corrections: `--use_distill` is *required* for v1.5, not optional. `--use_int8` saves memory but does not buy parallel throughput: one card measured 1.0× with two jobs (`GPU_ECONOMICS.md` §2), so scale by pods. VRAM is not published; the official scripts run on 2 GPUs. |
| LTX LipDub / 2.3 "speaks the lines perfectly" | **Overstated.** The tutorial below shows retries are part of the method. The current version is LTX-2.5, which needs the same frozen-audio trick. Its value is camera and body motion *while* talking. |
| MultiTalk for multi-person scenes | **We already have the better version.** InfiniteTalk comes from the same team, succeeds MultiTalk, and is what our pods run. "Every face moves" is the failure we hit; the fix is per-speaker masks (§3.4, E4), not a different model. |
| LatentSync on Wan clips for realistic mouths | **Worth testing, mainly for grief lines.** Wan draws the crying face and body; LatentSync repaints only the mouth, so the emotion should survive. This is the one path that could replace OmniHuman on grief. Limits: it works on a ~512 px face crop (wide shots with small faces suffer), strong head turns break it, and the weights are gated on Hugging Face. |
| Wan 480p → LatentSync → 1.5× upscale = "cinema grade for pennies" | **No.** Same flaw as §3.5: at 480p the face crop LatentSync works on is tiny, and per-frame upscaling flickers. **The order is also wrong for our clips**: Wan renders at 16 fps, too slow for speech mouth shapes, and interpolating *after* lip-sync blurs them. Correct order: **Wan 720p (mouth closed in the prompt) → RIFE to 25 fps → LatentSync → video upscaler**. |
| "Your RTX 6000 Ada / 4090" | Not our fleet. EU-RO-1 offers PRO 4500 32 GB, PRO 6000 96 GB, and sometimes L40S 48 GB. |

**The tutorial** — SOTAI, *LTX 2.3 Custom Audio Workflow* (2026-05-06, 17 min,
https://youtu.be/wZfUchcM6h4). Method and tips:
- **Method:** encode the real audio with the audio VAE and freeze it with an all-zero noise mask, instead
  of the empty audio latent the stock image+audio→video template starts from.
- **Settings:** distill LoRA v1.1 over v1.0; **15 steps** instead of the template's 8 (soft faces, unstable
  lips at 8); 30 steps only for hard cases; keep width, height and frame count inside LTX's shape
  constraints; no face-enhancing LoRAs (they hurt lip-sync).
- **Audio prep:** the stock workflow separates vocals with Mel-Band RoFormer. Our ElevenLabs lines are
  already clean, so we can skip it.
- **The admitted failure mode:** the mouth sometimes barely moves. The suggested fixes are trial and
  error: change the resolution, add steps, or swap the audio clip.
- **The demo:** one English singing clip, nothing in an Indian language.

**What this means for us:** one line that needs retries on an unknown share of 35 lines, each behind a
review gate, is a cost and schedule risk. LTX is a test candidate only for lines that need camera motion,
not a default.

### 4.4 Review of a second outside proposal (2026-11-01): "final verdict" stack

The proposal: Qwen-Image-Edit-2511 + Wan 2.2 I2V-A14B + InfiniteTalk + LatentSync (repair only), on one
RTX 6000 Ada 48 GB development pod, with LTX-2.5 and LongCat as later challengers.

**Most of it is where we already are, or behind it.** We measured these points ourselves on Ep1:

| Proposal | Our evidence |
|---|---|
| Qwen-Image-Edit-2511 for character continuity | Tested 2026-10-29, blind on 24 Ep1 shots: PoYo 8, Qwen 3, equal 13; faces shift. **Kling O3 is the default** (user rule). Images are not in this review's scope anyway. |
| Wan A14B "needs 80 GB" | That is the official PyTorch path. Our ComfyUI fp8 graph runs on a 32 GB card: 214 s per 81 frames on 25 Ep1 clips. |
| One always-on 48 GB dev pod; no serverless yet | We already create a pod per batch and delete it as each stage lands, with no serverless. The 48 GB card is E5. |
| Network volume is Secure Cloud and tied to one data centre | Known; volume `g4blvbmrg0` in EU-RO-1. Global volumes were reviewed and not adopted (no atomic rename). |
| "$22 per 10-min episode is unrealistic; assume 5–20 min per attempt, 3 attempts" | Our measured attempts are 3.6–6 min (InfiniteTalk 361 s/window, Wan 214 s). The Ep1 restart rendered 31/31 first time; quality rework was ~20% (6 grief lines + 1 retake). A 10-min episode ≈ 2× §5's 5-min figures ≈ **$16–22 plus rework**, so $22 is tight but not fantasy. |
| Wan TI2V-5B as an economy option | Native 24 fps 1280×704, so no judder. But it is a smaller model than A14B, and RIFE fixes A14B's frame rate for ~$0.006. Not worth a test cell. |
| Native-speaker review | Already a gate (the family reviews the Telugu dialogue on the board). |

**Four points are new and worth adopting:**

1. **Wan 2.2 Animate for performance control.** It takes a reference performance video plus the character
   still and transfers movement and expression. For crying, confrontation or a precisely timed reaction,
   someone records the beat on a phone and Animate puts it on the character. This is a third route for
   grief, next to OmniHuman and LongCat/LatentSync, and the only one where *we* direct the acting.
   PoYo hosts it (`wan-animate-move`, `wan-animate-replace`), so a test needs no pod work. A spoken line
   would still need lip-sync on top: Animate → RIFE → LatentSync. Price and output fps are not in our
   catalog snapshot; check with `describe` before the test.
2. **Licence audit on auxiliary models.** The proposal says LatentSync's face processing uses InsightFace,
   whose pretrained models are licensed for non-commercial research only, even though the library code
   is MIT. The channel is monetised, so **check this before LatentSync goes into production**. Options if
   it holds: run LatentSync through a hosted provider, swap the face detector, or use fal's Sync lipsync-2
   (rated "acceptable" on 2026-10-29). LTX-2.x is free under $10M revenue but bars products that compete
   with Lightricks; that matters only if the engine is ever sold as a service.
3. **LTX-2.5's official DubIt pipeline** is a second mouth-repair option next to LatentSync, possibly
   without the InsightFace question. Add it to E7.
4. **Cost per accepted second** as the episode metric: (all generation and retry spend) ÷ (seconds used
   in the cut). The clip ledger (`clips/_runs.json`) and the review gates already hold both numbers, so
   `edit/clips.py` can report it per stage. It also settles the 480p-draft question (§3.5) with data
   rather than the 37% guess.

---

## 5. Economics per episode

Assumed episode (Ep1-restart scale, ~5 min): **35 single-face lip-sync lines** (~50 windows),
**6 grief/2-person lines** on OmniHuman (~5 s each), **25 motion clips**, ~55 clips total to finish.
Flow hero shots are excluded (separate credit pool). Prices: measured RunPod units, PoYo 2026-10-22 list.

| Scenario | Lip-sync | Motion | Finishing | Cold starts | **Total** |
|---|---|---|---|---|---|
| **Today** (InfiniteTalk + Wan, 16 fps, lanczos) | $3.60 + OmniHuman $3.15 | $1.08 | — | ~$0.45 (4 pods) | **≈ $8.3** |
| **Fixed RunPod** (Wan variant B + upscale + RIFE) | $3.60 + $3.15 | ~$2.00 | ~$1.9 | ~$0.45 | **≈ $11** |
| **Hybrid** (lip-sync RunPod, motion on Veo 3.1 PoYo) | $3.60 + $3.15 | $2.25 | ~$1.6 (lip-sync upscale only) | ~$0.22 (2 pods) | **≈ $10.8** |
| Hybrid + **48 GB `block_swap=0`** (est. $0.033–0.052/window) | $1.7–2.6 + $3.15 | $2.25 | ~$1.6 | ~$0.22 | **≈ $9–10** |
| Hybrid + InfiniteTalk masks recover 2-person lines | — | — | — | — | **−$1 to −2** vs the row above |
| All-PoYo (OmniHuman all lines + Veo) | ~$21.5 | $2.25 | ~$1.6 | 0 | **≈ $25** |

Readings:
- **Lip-sync is the cost driver**, and OmniHuman is ~4.4× InfiniteTalk per second of dialogue. Every line
  that can stay on a self-hosted model is worth keeping there; that is why the multi-person mask test and
  LongCat matter more than the motion model choice.
- **Motion is small money either way.** Fixed Wan and Veo 3.1 Lite are within ~$0.25 per episode of each
  other; choose by quality in a blind test and by ops simplicity (Veo removes the two Wan pods, their
  cold starts, the 81-frame window trap and the RIFE step).
- **The 48 GB experiment is still the best ~$1 to spend** on RunPod cost.
- Quality fixes add ~$2–3 per episode, which is less than one OmniHuman retake series.

---

## 6. Experiment plan (blind, per line, as the house rule requires)

| # | Test | Shots | Cost *est.* | Pass bar |
|---|---|---|---|---|
| E1 | **Delivery fixes**: current vs upscale+RIFE on existing Ep1 clips (no re-render) | 3 motion + 2 lip-sync | ~$0.30 | User prefers fixed on ≥4/5 |
| E2 | **Motion**: Wan A (today) vs Wan B (cfg on high) vs `veo-3-1-official` vs LTX-2.5, same frames, per-shot prompts; plus Wan 480p + SeedVR2 on the wide shot only (§3.5) | 4 frames (walk, vehicle, emotion close-up, wide) | ~$2.6 | Winner by shot type; tie → cheaper/simpler |
| E3 | **LongCat-Video-Avatar 1.5** vs InfiniteTalk vs OmniHuman | normal line, grief (`c05b`), 2-person (Ep1 s04), long 2-window line | ~$2 + half a day to add the node pack and weights to the volume | Matches InfiniteTalk on normal, beats it on grief or 2-person |
| E4 | **InfiniteTalk multi-person masks** with silent listener track | 2 two-person shots | ~$0.40 | Listener mouth still in both |
| E5 | **48 GB card, `block_swap=0`** (L40S / RTX 6000 Ada) | 1 known lip-sync clip | ~$1 | ≤ $0.052/window, identical output |
| E6 | **Two-pass grief**: Wan 720p crying clip, "mouth closed" → RIFE 25 fps → LatentSync 1.6 | 2 grief lines already done on OmniHuman (`v15`, `v20`) | ~$0.40 + gated-weights approval | Holds grief at least as well as OmniHuman |
| E7 | **LTX-2.5 talking with camera motion** (frozen audio latent, distill LoRA v1.1, 15 steps); plus **DubIt** re-sync on one existing clip | 2 lines, Telugu | ~$1.2 incl. retries | Lips engage on the first 1–2 tries, Telugu legible |
| E8 | **Wan 2.2 Animate (PoYo)** from a phone-recorded grief beat, then RIFE → LatentSync or DubIt | 1 grief line (`v15`) | price unknown (`describe` first) + ~$0.20 | Grief reads at least as well as OmniHuman |

Total ≈ **$9–11** plus engineering time for E3/E6/E7. **Before E6/E8 go to production**, settle the
InsightFace licence question (§4.4). Order: E1 (proves the cheap wins), E4 + E5 (one
pod session), E2, then E3 + E6 together (the grief and two-person challengers), E7 last.

**Engineering before E3/E6/E7, in our image:**
- **LongCat:** the GGUF route runs through WanVideoWrapper, which we pin at `088128b2`; check whether that
  pin has the LongCat nodes or needs a bump.
- **LTX-2.5:** needs ComfyUI ≥ 0.32; check what `worker-comfyui 5.10.0-base` ships.
- **LatentSync:** a new node pack plus gated weights. The user has to accept the terms on Hugging Face;
  we can't do that for them.

---

## 7. Recommended direction

1. **Now, before the next episode:** add upscale + RIFE to the clip path and a per-shot motion prompt;
   snap Wan to 81 frames (already noted in `GPU_ECONOMICS.md`). Re-assemble Ep1 from existing clips to
   confirm (E1).
2. **Lip-sync stays self-hosted** — it is where the money is. Run E3/E4/E5; promote LongCat if it wins,
   keep InfiniteTalk otherwise, OmniHuman only for what neither handles.
3. **Motion: decide by E2.** If Veo 3.1 (PoYo) ties or wins, move motion to PoYo and run RunPod as a
   lip-sync-only fleet of 2 pods. If Wan B wins, keep the 4-pod type-specialised fleet.
4. **Do not self-host MiniMax H3** (licence/region) and do not chase Wan 2.7/3.0 locally (not open).

### 7.1 Routing by shot type (target after the tests)

| Shot | Today | Challenger(s) to test | Keep the incumbent unless |
|---|---|---|---|
| One face, calm/happy/angry line | InfiniteTalk 720p | LongCat 1.5 (E3) | Challenger wins blind |
| One face, **grief/anguish** | OmniHuman ($0.45/line) | LongCat 1.5 with `audio_cfg` 3–4 (E3); Wan → RIFE → LatentSync (E6); Wan Animate from a recorded performance (E8) | A cheaper path ties OmniHuman |
| A precise gesture or reaction beat | prompt-only Wan, retried | Wan Animate from a phone reference (E8) | Fewer retries than prompting |
| **Two faces, one speaks** | OmniHuman | InfiniteTalk masks + silent listener (E4); LongCat 2-person | Listener's mouth stays still |
| **Two faces, both speak in one shot** | split into two shots | LongCat `add` mode (turn-taking audio) | — new capability, use if it works |
| Talking while moving / camera move | static InfiniteTalk + zoom in assembly | LTX-2.5 (E7); Wan → LatentSync (E6) | Lips engage reliably |
| Motion, no dialogue | Wan 2.2 16 fps | Wan B + RIFE + upscale; Veo 3.1 (E2) | — |
| Hero / effects | Flow Omni 1.1 Flash | — | — |

Finishing for every clip: video upscaler to 1080p, and RIFE for anything rendered below 25 fps,
**before** any lip-sync pass that runs on it.

---

## Sources

- [LongCat-Video-Avatar 1.5 model card](https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5) ·
  [tech report (arXiv 2605.26486)](https://arxiv.org/html/2605.26486v1) ·
  [ComfyUI-LongCat-Avatar releases](https://github.com/rookiestar28/ComfyUI-LongCat-Avatar/releases) ·
  [GGUF for ComfyUI](https://huggingface.co/vantagewithai/LongCat-Video-Avatar-1.5-GGUF-ComfyUI) ·
  [ComfyUI Wiki news](https://comfyui-wiki.com/en/news/2026-05-21-longcat-video-avatar-1-5)
- [OmniHuman benchmark paper (OHBench)](https://arxiv.org/html/2604.18326v2) ·
  [WaveSpeed digital-human review](https://wavespeed.ai/blog/posts/the-2026-ai-digital-human-crown/) ·
  [PiAPI OmniHuman 1.5 vs Kling Avatar](https://piapi.ai/blogs/omnihuman-1-5-vs-kling-ai-avatar)
- [WanVideoWrapper multitalk nodes (`ref_target_masks`)](https://huggingface.co/aliensmn/ComfyUI-WanVideoWrapper/blob/main/multitalk/nodes.py) ·
  [RunComfy MultiTalk workflow](https://www.runcomfy.com/comfyui-workflows/comfyui-multitalk-workflow-multi-speaker-lip-synced-video-generator) ·
  [ThinkDiffusion InfiniteTalk guide](https://learn.thinkdiffusion.com/latest-in-lipsync-infinitetalk-video2video-comfyui-guide/)
- [Wan2.2-Lightning discussion on motion loss](https://huggingface.co/lightx2v/Wan2.2-Lightning/discussions/5) ·
  [Wan2.2 Distill Models](https://huggingface.co/lightx2v/Wan2.2-Distill-Models) ·
  [wan2.2 i2v ultra dynamic](https://huggingface.co/Roman6699/wan2.2_i2v_ultra_dynamic)
- [Wan2.2 S2V model card](https://huggingface.co/Wan-AI/Wan2.2-S2V-14B) ·
  [S2V vs MultiTalk test](https://civitai.com/articles/18873)
- [MiniMax H3 open weights (ComfyUI Wiki)](https://comfyui-wiki.com/en/news/2026-08-03-minimax-h3-open-weights-comfyui) ·
  [MiniMax H3 guide (licence regions)](https://ghost.oxen.ai/minimax-h3/) ·
  [Spheron H3 deployment](https://www.spheron.network/blog/deploy-minimax-h3-gpu-cloud/)
- [LTX-2.5 in ComfyUI](https://blog.comfy.org/p/ltx-25-day-0-support-in-comfyui) ·
  [LTX-2.5 VRAM guide](https://www.mindstudio.ai/blog/ltx-2-5-comfyui-install-guide) ·
  [LTX-2.5 custom-audio lip-sync thread](https://huggingface.co/Lightricks/LTX-2.5/discussions/44)
- [ComfyUI video upscale tutorial (FlashVSR / SeedVR2)](https://docs.comfy.org/tutorials/utility/video-upscale.md) ·
  [ComfyUI-SeedVR2_VideoUpscaler](https://www.sourcepulse.org/projects/18364760)
- [Open-source lip-sync comparison 2026](https://instavar.com/research/ai-video/open-source-lip-sync-models) ·
  [ComfyUI-LatentSyncWrapper](https://github.com/ShmuelRonen/ComfyUI-LatentSyncWrapper)
- [Best open image-to-video models 2026](https://wink.ai/blog/best-open-source-image-to-video-ai-models)
