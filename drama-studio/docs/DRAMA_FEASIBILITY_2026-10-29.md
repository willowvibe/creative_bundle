# Tamil drama series — feasibility analysis (2026-10-29, rev 5)

> **2026-11-01: the video stage is being redesigned.** After Ep1 the plan for lip-sync, motion,
> finishing and hosting moved to `DRAMA_PIPELINE_V2_2026-11-01.md` (shot-type routing, RunPod
> serverless endpoints, RIFE + SeedVR2 finishing, a pilot scene before re-rendering). Evidence:
> `DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md`. The sections below remain the record of the October
> decisions and the front-half (script, voices, music) plan.

**Target:** a 12-minute Tamil family-drama episode, 2 per week (~8.7 episodes,
~104 finished minutes a month). Style follows the reference channel: photoreal
recurring characters, lip-synced dialogue, short cuts.

**Decision (rev 4): Route B with InfiniteTalk.** In a blind test the user rated
self-hosted InfiniteTalk "good" on both dialogue shots, level with OmniHuman, at
about a tenth of the cost per second (§1). The plan is in §11.

**Rev 5 (after the first full episode, §12):** dialogue renders at **720p** by
default (the user preferred it clearly over 480p). **GPU policy: more than 24 GB and at
most 48 GB, in EU-RO-1**: an **RTX 5090 (32 GB) with `block_swap=10`**, falling back to an
RTX PRO 4500 (32 GB), then an L40S (48 GB). Route B came to **~$320 a month** on the 5090 (~$505
pessimistic), ~$340 if batches land on the PRO 4500, against $860 for Route A.
**Measured 2026-10-30:** 720p with no block swap runs out of memory on the 32 GB 5090. `block_swap=10` runs at
220–260 s per window (~$0.08, not the estimated ~$0.06), so InfiniteTalk GPU cost is about a third higher than
these figures. Multi-person shots on OmniHuman add more (§12).
Every episode now passes a **dialogue and audio review** by the user before any
GPU or video work.

Monthly cost at 1.3× / 2.2× reroll rates:

| Route | What runs where | Per episode | Per month | Ready |
|---|---|---|---|---|
| **A. APIs** | OmniHuman + Wan 2.2 Fast on PoYo, Sarvam voice | $99 / $163 | **$860 / $1,420** | Now |
| **B. Hybrid (chosen)** | Dialogue: InfiniteTalk **720p** on a RunPod RTX 5090 (fallback RTX PRO 4500, then L40S), OmniHuman for failed lines and for shots with more than one person in frame (§12). Everything else on APIs | **~$36 / $57** (720p on 5090, §12) | **~$320 / $505** | +2–4 days of engine work |
| **C. Fully open** | Everything except script and voice on a RunPod secure 5090 | $18 / $30 | **$170 / $275** | +2–4 weeks |

Rev 4 replaces the earlier community-GPU prices: RunPod network volumes work
only on Secure Cloud, so community pods can't keep the models between runs.
Route B and C figures now use measured InfiniteTalk speed on a secure 5090 (§6).

Rev 2 adds: a review of an external pipeline suggestion (§3), the complete
stage-by-stage pipeline with repos and licences (§5), and the full cost map
including fixed costs (§6). Rev 3 adds §10: what ComfyUI can run, three
full-series plans (full ComfyUI, hybrid, APIs only) and a cost comparison across
RunPod, Vast.ai, AWS, Modal, fal, Replicate, Comfy Cloud and RunComfy.

## 1. What the 10 s test showed

From `videos/2026-10-29/drama-test-10s/`:

| Shot type | Tested | Verdict |
|---|---|---|
| Stills + push-in | ffmpeg | Fallback only |
| Motion, no dialogue | PoYo `wan2.2-image-to-video-fast` 720p, $0.06/clip | Good enough |
| Dialogue | Kling Avatar 2.0 std / pro; Wan 2.7 i2v + `audio_url` | **Rejected** by the user |
| Dialogue | PoYo `omnihuman-2.0`, $0.105/s, 4 s minimum | **Good: the baseline to beat** |
| Character consistency | Reference portraits → `nano-banana-2-edit` | Held across all shots |

### Blind lip-sync test (rev 4)

Five models on the same s2 and s3 lines (same start frame, same 4 s Sarvam
audio), labelled A–E in random order and rated by the user before the key was
revealed (`blind_s2.mp4`, `blind_s3.mp4`, `blind_key.json` in the test kit):

| Model | s2 | s3 | Cost per second | Notes |
|---|---|---|---|---|
| **InfiniteTalk**, self-hosted via [`runpod-comfyui`](../../runpod-comfyui), 480p | **good** | **good** | ~$0.017 (secure 5090) | 163 s per 4 s line warm. Renders whole ~3.2 s windows, so output needs trimming to the audio |
| **OmniHuman 2.0** (PoYo) | **good** | **good** | $0.105, 4 s minimum | Baseline |
| Wan 2.2 clip + Sync `lipsync-2` (fal) | acceptable | acceptable | $0.05 + $0.06 per clip | Fallback candidate |
| Wan 2.2 clip + Sync `lipsync-2-pro` (fal) | acceptable | acceptable | $0.083 + $0.06 per clip | Not better than standard |
| LongCat-Video-Avatar (fal, 720p) | acceptable | **fail**: expression, no dialogue | $0.30 | Dropped |

HeyGen Avatar IV was not run: its API needs wallet credit. The user's earlier
rejection of InfiniteTalk came from the assembled 10 s cut, where 480p
upscaling, untrimmed tails and a stray smile in s3 were mixed in. Judging the
line on its own reversed it.

## 2. Workload model

| Per 12-min episode | Assumption | Quantity |
|---|---|---|
| Dialogue screen time | 60% | 432 s, ~150 lines of ~2.9 s |
| Motion screen time | 40% | 288 s, ~130 shots (5 s generations trimmed to ~2.2 s cuts) |
| Keyframes | ~120 setups + 6 character refs, ×1.2 retries | ~151 images |
| Tamil dialogue text | ~1,400 words | ~10K characters |
| Video reroll factor | Base / pessimistic | **1.3× / 2.2×** |

The 2.2× pessimistic factor comes from the external suggestion and is a fair
upper bound for faces, hands and physics failures.

## 3. Review of the external suggestion

The suggestion proposed a fully open pipeline at "$50–180 a month in GPU compute"
for 10 × 10-minute episodes.

| Claim | Verdict | Evidence / correction |
|---|---|---|
| Orchestrator: **ArcReel** | ⚠️ Exists, but not a fit as a dependency | Story-to-video workspace built on the Claude Agent SDK, calling model APIs (Gemini, Volcengine, Grok, OpenAI-compatible); exports CapCut drafts. **AGPL-3.0** per its site. Copyleft, and it duplicates our engine. Use it as a design reference. |
| Orchestrator: **dramaclaw** | ⚠️ Exists, but not a fit | Manuscript → episodes → storyboards → voice → cut, but all inference goes through an OpenAI-compatible gateway; nothing runs models locally. **Elastic License 2.0** (source-available, not OSI open source). |
| Script: Qwen 2.5 72B / Llama 3.3 70B | ⚠️ Right idea, wrong hosting | A 70B model needs an 80 GB GPU. Self-hosting it for ~9 scripts a month costs more than API calls. Keep the engine's LLM config. Advice to force colloquial (Madras, Kongu, Madurai) Tamil via system rules is correct and adopted. |
| Keyframes: FLUX.1-dev + PuLID / PhotoMaker | ❌ Licence risk | The FLUX.1 [dev] model licence is non-commercial, and whether its outputs may be used commercially is disputed. Too risky for a monetized channel. Use the proven `nano-banana-2-edit` API, or open **Qwen-Image-Edit** (Apache-2.0 for releases through 2511; Qwen-Image 2.1 moved to a research-only licence) or **FLUX.1 [schnell]** (Apache-2.0). |
| Motion: Wan 2.1 / 2.2, HunyuanVideo, LTX-Video | ✅ | Wan 2.2 is the newest open Wan; 2.5–3.0 are API-only. |
| TTS: AI4Bharat IndicF5 / Indic-Parler-TTS | ⚠️ Not worth switching | IndicF5's licence is unclear: MIT is claimed, but derivative cards say its training data carries non-commercial terms. Indic-Parler-TTS (Apache-2.0) has no voice cloning. Sarvam costs ~$0.35 an episode and already holds your cloned voice. |
| Lip-sync: LivePortrait or Wav2Lip | ❌ Wrong tools | **Wav2Lip** is restricted to personal, research and non-commercial use, and renders the mouth at low resolution. **LivePortrait** animates a face from a *driving video*, not from audio. Replacements below. |
| Lip-sync: "2.5 GPU-hours for 100 minutes" | ❌ Only for mouth-swap models | Fast mouth-replacement (MuseTalk-class) can manage that. Full talking-video models, the class closest to OmniHuman (InfiniteTalk, LongCat), need ~60+ GPU-hours a month at this volume. |
| Video: 75 s per 5 s clip on A100/H100, ~55 GPU-h a month | ⚠️ Only with a distilled model | Plausible with LightX2V's 4-step Wan 2.2; the base 40-step model takes many minutes per clip. |
| GPU prices | ⚠️ Partly off | Live RunPod read today: A100 SXM community **$1.39/h** (they said $1.49–1.89); H100 SXM secure **$3.99/h** (they said $2.49–3.10; community is $2.69). |
| 100 GB storage at ~$5–10 a month | ✅ | RunPod bills network volumes hourly per GB from creation to deletion. |
| ~2.2× rerolls, ~5 s shots | ✅ | Adopted as the pessimistic case. The reference channel cuts every ~1.7 s, so 5 s generations get trimmed. |
| Total "$50–180 a month" | ⚠️ Compute only | Matches Route C's compute (§6) but leaves out storage, model-load overhead, script LLM, engineering time and any QA reruns. |

## 4. RunPod catalog (live reads, 2026-10-29)

### Public endpoints (pay per use)

| Endpoint id | Model | Listed price | Fit |
|---|---|---|---|
| `infinitetalk` | InfiniteTalk, image + audio → talking video | 480p $0.25, 720p $0.50 "per video". The unit isn't stated | Test it; the response returns `output.cost` |
| `wan-2-2-i2v-720` | Wan 2.2 I2V | 5 s $0.30 | 5× PoYo |
| `seedance-v1-5-pro-i2v` | Seedance 1.5 Pro | 720p $0.052/s | Motion alternative |
| `google-veo3-1-fast-i2v` | Veo 3.1 Fast | $0.10/s without audio | Motion alternative |
| `p-video` | Pruna P-Video | 720p $0.02/s | Cheapest motion; quality unknown |

### Hub workers (deploy as your own endpoint or pod)

| Repo | Model | Deploys |
|---|---|---|
| `wlsdml1114/generate_video` | Wan 2.2 I2V + LoRA | 5,374 |
| `wlsdml1114/Infinitetalk_Runpod_hub` | InfiniteTalk | 1,755 |
| `wlsdml1114/Wan_Animate_Runpod_hub` | Wan Animate (performance transfer from a driving video) | 1,027 |
| `wlsdml1114/Multitalk_Runpod_hub` | MultiTalk (two speakers in one frame) | 388 |
| `wlsdml1114/upscale_interpolation_runpod_hub` | Upscale + frame interpolation | 316 |
| `huchukato/runpod-minimax-h3` | MiniMax H3 (open weights, audio references) | 228 |
| `poiucoo/HunyuanVideo-Avatar` | HunyuanVideo-Avatar | 58 |

### GPU prices ($/h, `list-gpu-types`, serverless availability)

| GPU | VRAM | Community | Secure | Serverless | Stock |
|---|---|---|---|---|---|
| RTX 4090 | 24 GB | 0.34 | 0.89 | 1.34 | HIGH |
| **RTX 5090** | 32 GB | **0.69** | 1.19 | 1.79 | HIGH |
| L40S | 48 GB | 0.79 | 1.09 | 1.75 | LOW |
| A100 SXM | 80 GB | 1.39 | 1.79 | 2.72 | MEDIUM |
| RTX PRO 6000 | 96 GB | 1.69 | 2.49 | 3.49 | HIGH |
| H100 SXM | 80 GB | 2.69 | 3.99 | 5.39 | HIGH |

## 5. The complete pipeline

Each stage lists the current choice (Route A) and its open-source replacement
(Route C). **Engine** means already built in this repo.

| # | Stage | Route A (APIs) | Open-source option (Route C) | Licence | Runs on |
|---|---|---|---|---|---|
| 1 | Story + Tamil screenplay (colloquial rules, beats, "Part 2" hook) | Engine LLM config (`config.yaml`) | Qwen / Llama via vLLM (not worth self-hosting) | — | API |
| 2 | Screenplay → shot list (speaker, framing, length, motion prompt) | **To build** in the engine (ArcReel / dramaclaw as design references) | same | — | Local |
| 3 | Character reference sheets | PoYo `nano-banana-2` 2K | Qwen-Image (≤2511) or FLUX.1 [schnell] | Apache-2.0 | 5090 pod |
| 4 | Shot keyframes with locked faces | PoYo `nano-banana-2-edit` + refs (engine #318) | Qwen-Image-Edit 2511 multi-reference | Apache-2.0 | 5090 pod |
| 5 | Tamil voices per character | Sarvam bulbul:v3 + your clone (engine provider) | IndicF5 (licence unclear) | ⚠️ | API |
| 6 | Motion shots (no dialogue) | PoYo `wan2.2-image-to-video-fast` | Wan 2.2 I2V A14B + LightX2V 4-step (`wlsdml1114/generate_video`) | Apache-2.0 | 5090 pod |
| 7a | Dialogue shots, talking video | PoYo `omnihuman-2.0` | **InfiniteTalk** (`wlsdml1114/Infinitetalk_Runpod_hub`) or **LongCat-Video-Avatar 1.5** | Apache-2.0 / open weights | 5090 pod |
| 7b | Dialogue, mouth-swap fallback | — | Silent Wan 2.2 clip + **LatentSync 1.6** or **MuseTalk** | Apache-2.0 (secondary source) / MIT | 4090 / 5090 |
| 7c | Two speakers in one frame | — | MultiTalk | Apache-2.0 | 5090 pod |
| 8 | Upscale / frame interpolation (optional) | — | `wlsdml1114/upscale_interpolation_runpod_hub` | — | pod |
| 9 | Music bed + SFX | Engine: ACE-Step (local, MIT), earshot SFX, ducking | same | MIT | Local |
| 10 | Edit, transitions, captions, `.srt`, loudness | Engine: Composition IR, `ve longform` chapter join, karaoke captions, faster-whisper | same | — | Local |
| 11 | QA gates (safe area, glyphs, loudness, cue sync) | Engine | same | — | Local |
| 12 | Thumbnail with Tamil text | `nano-banana-2` + engine thumbnail tool | Qwen-Image | Apache-2.0 | API / pod |

Route B is Route A with step 7a moved to the pod.

## 6. Complete cost map

### Per episode, 1.3× rerolls (2.2× in brackets)

Rev 4: Routes B and C run on a **secure** RTX 5090 at $1.19/h (a network volume
needs Secure Cloud), with InfiniteTalk at the measured speed, 480p.

| Element | Route A (APIs) | Route B (hybrid, chosen) | Route C (open, secure 5090) |
|---|---|---|---|
| Script LLM | $0.50 | $0.50 | $0.50 |
| Keyframes (151) | $6.00 | $6.00 | $1.00 (0.84 GPU-h) |
| Voice (Sarvam) | $0.35 | $0.35 | $0.35 |
| Motion (130 shots) | $10 ($17) | $10 ($17) | $5.00 ($8.60), 4.2 (7.2) GPU-h |
| Dialogue (432 s) | **$82 ($139)**: OmniHuman, 150 × 4 s billed | **$7.60 ($12.60)**: InfiniteTalk, 6.4 (10.6) GPU-h incl. boot | $7.60 ($12.60) |
| Dialogue fallback to OmniHuman (5% of lines, 10% pessimistic) | — | $3.15 ($6.30) | $3.15 ($6.30) |
| Model-load / idle overhead | — | in dialogue hours | ~$0.80 |
| Music, edit, captions, QA | $0 (local) | $0 | $0 |
| **Episode total** | **$99 ($163)** | **$28 ($43)** | **$18 ($30)** |

### Per month (×8.7 episodes)

| | Route A | Route B | Route C |
|---|---|---|---|
| Variable | $860 ($1,420) | $240 ($370) | $160 ($265) |
| Network volume | — | 80 GB ≈ $5.60 (exists: `g4blvbmrg0`, EU-RO-1) | ~150 GB ≈ $10.50 |
| **Monthly, secure pod per batch** | **$860 ($1,420)** | **$245 ($375)** | **$170 ($275)** |
| …same at 720p (≈2× GPU time) | — | $310 ($485) | — |
| …on serverless 5090 ($1.79/h), 480p | — | $275 ($430) | — |
| GPU hours a month | 0 | 56 (92) | 105 (168) |
| One-time engineering | ~1–2 weeks (shared engine work) | + 2–4 days | + 2–4 weeks |

Community-cloud prices from rev 2–3 ($205 / $105) are withdrawn: a community pod
can't mount the network volume, so it would re-download ~67 GB of models on
every new pod.

### GPU-time assumptions

| Model | GPU seconds | Basis |
|---|---|---|
| **InfiniteTalk 480p, LightX2V 6-step, RTX 5090 (measured)** | **~80 s per 81-frame window; ~1.4 windows per 2.9 s line ≈ 112 s per line** | Warm run: 163 s for 2 windows (s3); 247 s cold incl. model load (s2). 720p assumed ≈2× |
| **Wan 2.2 I2V A14B, 4-step, 720p, 81 frames, RTX 5090 (measured)** | **135 s per clip warm** (197 s cold) | s4 warm, s1 cold, 2026-10-29. Route C motion figures above still use the earlier 90 s guess; at 135 s, Route C motion is ~$7.50 ($12.80) an episode |
| Keyframe image, 2K | 20 s per image | Typical for 20B-class image-edit models on a 5090 |

### Cost drivers, ranked

1. **Dialogue lip-sync**: 83% of Route A. OmniHuman's 4 s minimum alone adds ~38% to every 2.9 s line. Batch each character's lines per scene into one call (up to 300 s) to save ~25% on Route A today.
2. **Reroll rate**: moving from 1.3× to 2.2× adds 65% to Route A and ~50% to B and C.
3. **Resolution**: InfiniteTalk at 720p roughly doubles Route B's GPU time (+$65 a month).
4. **GPU tier**: on the same GPU, serverless costs 1.5× a secure pod.
5. **Engineering time**: not priced above, and Route C's biggest real cost.

## 7. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| InfiniteTalk was judged on only 2 lines, at 480p | Weaker lines in a full episode | Per-line QA with automatic OmniHuman fallback (§11); review the first episode's lines closely |
| Wrong emotion on a line (s3 smiled) | Scene reads wrong | Emotion in the per-line prompt; reroll, then fallback |
| RTX 5090 stock in EU-RO-1 is LOW, and the volume is tied to that data center | A batch can't start | Allow other GPUs in the same data center (RTX PRO 6000, A100); serverless with several GPU pools later |
| Licences: FLUX.1-dev, Wav2Lip, IndicF5, Qwen-Image 2.1, MiniMax H3 (reported regional block) | Monetization or takedown risk | Use only the Apache / MIT models listed in §5 |
| Character drift over 12 minutes | Viewers notice | Locked reference sheets; engine #318 |
| AGPL code (ArcReel) mixed into the engine | Copyleft obligations | Borrow ideas only, no code |

## 8. Recommended plan

1. **Engine work needed by every route** (~1–2 weeks):
   - Per-line speakers in content packs
   - Reference-image support (#318)
   - A PoYo OmniHuman provider with audio upload
   - The screenplay → shot-list planner
2. ~~Bake-off~~ **Done (rev 4):** InfiniteTalk won the blind test (§1).
3. **Route B with InfiniteTalk**: see §11 for the build and rollout plan. It saves ~$615–1,045 a month against Route A.
4. **Route C only if volume grows:** moving keyframes and motion to the pod saves only ~$75–100 a month more than B, for 2–4 extra weeks of work.

## 9. ComfyUI: Desktop, Comfy Cloud, or on RunPod

ComfyUI is the *runtime* that would run the open models in Routes B and C. The
question is where it runs. (ComfyUI is unrelated to ComfyStudio, the timeline
editor the engine already bridges to.)

### Why ComfyUI at all

- **Model coverage:** Kijai's `ComfyUI-WanVideoWrapper` ships InfiniteTalk and MultiTalk nodes alongside Wan 2.2 + LightX2V. Upscale, RIFE interpolation and image-edit nodes are all off the shelf, so no custom Python per model.
- **One provider for every location:** a workflow saved in API format runs unchanged on Desktop, a RunPod pod, RunPod's serverless `runpod-workers/worker-comfyui` (14,902 deploys, release 5.11.0), or the Comfy Cloud API. One engine provider (submit workflow JSON, poll, download) covers them all.
- **Costs:** nodes break across versions, so pin the ComfyUI and node versions in the image. Third-party guides put InfiniteTalk at **≥ 24 GB VRAM**, with 48 GB recommended.

### The three scenarios

| | ComfyUI Desktop (local) | Comfy Cloud | ComfyUI on RunPod |
|---|---|---|---|
| Hardware | Your RTX 3050 Laptop, **4 GB** | RTX PRO 6000 96 GB **$3.49/h**, PRO 6000 MIG 48 GB $1.75/h (about half speed), H100 $6.23/h | Any GPU: RTX 5090 32 GB **$0.69/h community**, $1.79/h serverless |
| Plans | Free | Standard $20/mo (4,200 credits, 30-min max run, 1 API job at a time); Creator $35 (7,400 credits, own models, 3 jobs); Pro $100 (21,100 credits, 1-h max run, 5 jobs). ~211 credits per $1 | Pay per second / per hour |
| Storage | Local disk | **$0.20/GB-month** | ~$0.07/GB-month network volume |
| Custom nodes | Anything | "Most-used" nodes only. **InfiniteTalk support unconfirmed**. Own models need Creator or above | Anything you put in the image |
| Can it run the drama models? | **No.** Wan 14B and InfiniteTalk need ≥ 24 GB | Wan 2.2: yes (Comfy quotes ~11 credits, ≈ $0.052, per 5 s clip). InfiniteTalk: to be tested | Yes |
| Setup effort | Low | Lowest | Medium: template or Docker image + volume |

### Monthly cost of the drama workload on each

Same workload as §6, 1.3× rerolls (2.2× in brackets). GPU speed is assumed equal
on a 5090 and an RTX PRO 6000.

| Scenario | Route B (dialogue only on ComfyUI) | Route C (all open models on ComfyUI) |
|---|---|---|
| Desktop (local RTX 3050) | Not possible | Not possible |
| **Comfy Cloud**, PRO 6000 | Dialogue 61 h × $3.49 = $212 + APIs $147 + 60 GB storage $12 ≈ **$370 ($580)** | ~107 h × $3.49 = $373 + 150 GB storage $30 + script/voice $7.5 ≈ **$410 ($665)** |
| **RunPod pod** (ComfyUI), secure 5090 with volume (rev 4) | **$245 ($375)** | **$170 ($275)** |
| RunPod serverless (`worker-comfyui`), 5090 | $285 ($420) | $240 ($360) |
| For reference: Route A, all APIs | $860 ($1,420) | — |

### Verdict

- **ComfyUI as the runtime: worth it.** It gives the fastest route to the
  bake-off, and to Route B or C, without writing a worker per model.
- **ComfyUI Desktop: not a production path.** The 4 GB laptop GPU can't load the
  models. It's free and useful only for editing workflow graphs before running
  them elsewhere.
- **Comfy Cloud: worth $20 for a month of experiments, not for production.**
  - For production it's **1.5–2.4× the cost** of a RunPod secure pod for the same work. Storage costs ~3× more, and it can run only 1–5 API jobs at a time.
  - Whether InfiniteTalk runs there is unconfirmed. The free tier's 5 runs (10-minute cap) can answer that.
  - It's still ~2.3× cheaper than Route A, so it's an acceptable stopgap if you want zero infrastructure.
- **ComfyUI on RunPod: the production choice.** Use a secure RTX 5090 pod (rev 4: a community pod can't mount the model volume)
  with the ComfyUI template for the bake-off and for weekly batch rendering. Move
  to `worker-comfyui` serverless only if episodes need on-demand, parallel renders.

### Google Colab

| | Free | Pro ($9.99/mo, 100 compute units) | Pro+ ($49.99/mo, 600 CU per current third-party sources; one older page says 500) |
|---|---|---|---|
| GPU | T4 16 GB: too small for Wan 14B / InfiniteTalk | L4 24 GB (~1.7–4.8 CU/h ≈ $0.17–0.48/h), A100 (~5.4–15 CU/h ≈ $0.54–1.50/h). Not guaranteed | Same, with better access |
| Session | ≤ 12 h; idle timeout | ≤ 12 h | ≤ 24 h continuous with CU balance |
| ComfyUI web UI | **Disallowed** ("working mainly through a web UI instead of the notebook UI") | Allowed while CU balance > 0 | Same |
| Tunnels (ngrok, Pinggy, LocalTunnel) | "Connecting to remote proxies" is disallowed on **all** Colab runtimes, so this is a grey area | Same | Same |

Extra CU costs $9.99 per 100. Colab publishes no official per-GPU burn rates; the
figures above are third-party measurements that disagree.

**Cost of the drama workload on Colab.** Assume an A100 is ~1.2× slower than a
5090 here (no fp8 on A100):

| | Route B | Route C |
|---|---|---|
| A100 hours a month | ~73 (124) | ~128 (216) |
| At $0.54–1.50/h | $40–110 ($67–186) | $69–192 ($117–324) |
| Plus APIs | +$147 ($208) | +$7.5 |
| **Monthly** | **~$190–260 ($275–395)** | **~$75–200 ($125–330)** |

That is a little cheaper than a RunPod secure pod. The friction is what rules it out:
- **No guaranteed GPU:** an A100 may not be available on the day you render.
- **Session limits:** 12 h (24 h on Pro+), with idle timeouts.
- **No persistent model store:** 60–150 GB of weights must be re-downloaded each session, burning paid GPU time, or loaded slowly from Google Drive.
- **No way to automate:** there's no API to start a Colab runtime from the engine. Colab Enterprise is billed as ordinary GCP VMs.

**Verdict:** feasible for a one-off experiment such as the bake-off on Pro or
Pro+. Run ComfyUI headless from notebook cells, with no tunnel. Not suitable for
weekly production; a RunPod pod with a network volume costs about the same and
can be automated.

## 10. Full-series pipeline plans and hosting comparison

### What ComfyUI can run (and what stays outside it)

| # | Stage (§5) | In ComfyUI? | Node pack / model | Status |
|---|---|---|---|---|
| 1–2 | Script, shot list | No | Engine + LLM API | — |
| 3 | Character sheets | **Yes** | Native Qwen-Image (≤2511) or FLUX.1 [schnell] | Native support |
| 4 | Keyframes with locked faces | **Yes** | Native Qwen-Image-Edit 2511, multi-image references | Native support |
| 5 | Tamil voices | No (practically) | Sarvam API; no commercial-safe Tamil clone model in ComfyUI | Keep API |
| 6 | Motion shots | **Yes** | Native Wan 2.2 I2V A14B + LightX2V 4-step LoRA, or Kijai `ComfyUI-WanVideoWrapper` | Mature |
| 7a | Talking dialogue | **Yes** | InfiniteTalk via `ComfyUI-WanVideoWrapper`; LongCat-Video-Avatar node support **unverified** | Bake-off |
| 7b | Mouth-swap fallback | **Yes** | LatentSync wrapper, MuseTalk node, over a silent Wan clip | Bake-off |
| 7c | Two speakers in frame | **Yes** | MultiTalk via `ComfyUI-WanVideoWrapper` | Bake-off |
| 8 | Upscale / interpolation | **Yes** | SeedVR2 or ESRGAN upscalers; RIFE via `ComfyUI-Frame-Interpolation` | Mature, optional |
| 9 | Music, SFX | Possible, not useful | ACE-Step already runs locally in the engine | Keep engine |
| 10–11 | Edit, captions, QA | No | Engine (Composition IR, captions, gates) | Keep engine |
| 12 | Thumbnail | **Yes** | Qwen-Image; Tamil text added by the engine, not the model | Optional |

So ComfyUI covers **stages 3, 4, 6, 7 and 8**, which is 99% of the GPU spend.
The engine keeps orchestration, voice, edit and QA, and calls ComfyUI the same
way wherever it is hosted: submit an API-format workflow, poll, download.

### The three plans

| | Plan 1: full ComfyUI | Plan 2: hybrid | Plan 3: APIs only (no GPU hosting) |
|---|---|---|---|
| Keyframes | ComfyUI (Qwen-Image-Edit) | PoYo `nano-banana-2-edit` | PoYo `nano-banana-2-edit` |
| Motion | ComfyUI (Wan 2.2 + LightX2V) | PoYo `wan2.2-image-to-video-fast` | PoYo `wan2.2-image-to-video-fast` |
| Dialogue | ComfyUI (InfiniteTalk / LatentSync) | ComfyUI (bake-off winner), OmniHuman as fallback | Hosted InfiniteTalk API, or OmniHuman batched |
| Voice, script | APIs | APIs | APIs |
| Infra to run | GPU host + volume + workflows | GPU host for one workflow | None |
| Engineering beyond shared work | 2–4 weeks | 2–4 days | ~1 day per new provider |
| Main risk | Open models below OmniHuman quality | Only the dialogue model | Cost; hosted InfiniteTalk quality untested |

### Plans 1 and 2 across hosts: monthly cost, 1.3× rerolls (2.2× in brackets)

Billed GPU hours a month, in RTX 5090 equivalents, including model-load and idle
overhead: **Plan 1 ≈ 127 (195), Plan 2 ≈ 74 (116)**. Each host's hours are
scaled by an assumed speed relative to a 5090: H100 1.6×, PRO 6000 1.0×, L40S
0.75×. These factors are estimates; the bake-off replaces them. Plan 2 adds
$147 ($208) of APIs a month, Plan 1 adds $7 (script and voice).

| Host | Type | GPU, $/h | Plan 1 | Plan 2 | Stability | Scales to zero |
|---|---|---|---|---|---|---|
| **Vast.ai**, verified host | Marketplace pod | 5090 ~$0.35–0.50 | **$85 ($115)** | **$190 ($270)** | Varies by host; can be reclaimed | Stop the pod; disk still billed |
| ~~RunPod community pod~~ | Pod | 5090 $0.69 | ~~$105 ($150)~~ | ~~$205 ($295)~~ | **Not workable with a volume (rev 4):** network volumes need Secure Cloud | — |
| **fal serverless** custom app | Serverless | H100 from $1.89 | $155 ($240) | $235 ($345) | High | Yes, but "starting at" price and onboarding needed |
| RunPod secure pod | Pod | 5090 $1.19 | $170 ($250) | $240 ($350) | High | Stop the pod |
| AWS `g6e.xlarge` spot | VM | L40S ~$0.87–1.40 | ~$205 ($305) | ~$260 ($385) | 10–20%+ interruptions | Yes, manually |
| **RunPod serverless** `worker-comfyui` | Serverless | 5090 $1.79 (flex) | $245 ($365) | $285 ($420) | High | **Yes** |
| **Modal** (H100) | Serverless | H100 $3.95 | ~$290 ($460) after the $30 monthly credit | ~$300 ($465) | High; best developer tooling | **Yes** |
| AWS `g6e.xlarge` on-demand | VM | L40S $1.86 | $335 ($505) | $335 ($500) | High; GPU quota request needed | Yes, manually |
| Comfy Cloud | Managed ComfyUI | PRO 6000 $3.49 | $480 ($720) | $415 ($625) | High; 30–60 min run cap | Yes |
| Replicate private deployment | Serverless | H100 $5.49 | $445 ($675) | $400 ($605) | High; bills setup and idle | Yes |
| RunComfy serverless | Managed ComfyUI | H100 $7.49 | $600 ($920) | $495 ($750) | High | Yes |

Notes:
- **AWS has no serverless GPU.** Lambda has no GPUs; SageMaker async inference can scale to zero but takes days of setup. AWS only wins if you have startup credits (AWS Activate), which would make Plans 1–2 nearly free for a year.
- **Comfy Cloud** figures differ from §9, which assumed no overhead hours.
- Lambda, Salad and similar were left out. Lambda has no serverless option, and Salad's consumer GPUs top out at 24–32 GB with frequent node churn.

### Plan 3: APIs only, per episode

| Element | Option 3a: hosted InfiniteTalk | Option 3b: Wan clip + mouth-swap | Option 3c: OmniHuman, lines batched |
|---|---|---|---|
| Script, voice, keyframes | $6.85 | $6.85 | $6.85 |
| Motion (130 shots) | $10 ($17) | $10 ($17) | $10 ($17) |
| Dialogue (432 s) | WaveSpeed InfiniteTalk 720p, $0.06/s: **$34 ($57)** | 150 silent Wan clips $9 + fal `sync-lipsync/v2` $3/min: **$40 ($68)** | PoYo `omnihuman-2.0`, ~25% saved by batching: **$62 ($104)** |
| **Episode** | **$51 ($81)** | **$57 ($92)** | **$79 ($128)** |
| **Month** | **$445 ($705)** | **$495 ($800)** | **$690 ($1,115)** |
| Quality | Untested | Untested | **Approved** |

3a and 3b need the same quality test as the bake-off. They cost ~$2 to try on
the s2/s3 assets. If both fail, Plan 3 means 3c: Route A with batching.

### Recommendation for the initial stage (stable + serverless)

> Superseded by §11 (rev 4). Kept for the record; the community-pod steps below
> assumed a network volume works on community GPUs, which it doesn't.

1. **Week 1: bake-off (~$12).** One community 5090 pod running ComfyUI:
   InfiniteTalk, LatentSync and MultiTalk on s2/s3. Alongside, the WaveSpeed
   InfiniteTalk and fal `sync-lipsync/v2` APIs (3a/3b). Judge all against OmniHuman.
2. **Episodes 1–8 (first month): Plan 2 on RunPod serverless.**
   - Keyframes and motion on PoYo APIs; dialogue as one `worker-comfyui` endpoint with the winning workflow baked into the image and weights on a network volume.
   - Max workers 1–2, idle timeout ~60 s. Nothing runs between episodes.
   - Fall back automatically to OmniHuman when a job fails, so an episode never stalls.
   - **~$285 ($420) a month**, against $690+ for Plan 3c.
   - If no open model passes the bake-off, start on Plan 3a or 3b if one of those passed, otherwise 3c.
3. **Month 2 onwards, once the workflow is stable: move the batch to a pod.**
   - A RunPod community pod, started by the engine per batch, cuts Plan 2 to ~$205.
   - Add keyframes and motion to the same pod (Plan 1) for ~$105.
   - **Vast.ai** saves a further ~$20 a month but needs a verified host and tolerance for interruptions. Use it as a second source, not the primary.
4. **Modal** is the alternative if RunPod serverless proves flaky. It costs ~5–10% more but has the best cold-start tooling, and its Python-defined deployments avoid Docker builds.

## 11. Route B with InfiniteTalk: the plan (rev 4)

Keyframes, motion shots, script and voice stay on the APIs already in the engine.
Only dialogue moves to our own GPU, through the
[`runpod-comfyui`](../../runpod-comfyui) repo (`rpc` CLI, `rpcomfy` client).

### What already exists

| Piece | State |
|---|---|
| `runpod-comfyui` repo: no-build pod boot, pinned WanVideoWrapper + transformers-v5 patch, model manifest, `infinitetalk` workflow, `rpc validate` / `rpc run` | Working; ran s2/s3 on 2026-10-29 |
| Network volume `g4blvbmrg0` (80 GB, EU-RO-1) with all InfiniteTalk and Wan 2.2 models | Exists, ~$5.60 a month |
| Test pod `vzxyw50lvnmu40` (secure 5090) | Stopped; delete once the engine creates pods itself |
| OmniHuman via PoYo | Tested; becomes the fallback |

### How an episode's dialogue gets made

> Rev 5: two user checks gate production: the dialogue before voices, and the
> voice emotion before any GPU work (§12, "Production gate"). Lip-sync renders at 720p on an RTX PRO 6000.

1. **Plan:** the shot planner gives each dialogue line its keyframe, its Sarvam
   audio (with a 0.3 s lead-in) and a prompt that names the speaker and the emotion.
2. **Start a batch:** the engine creates a secure 5090 pod on the volume (no image
   build; boot plus node install takes ~3–5 min, as the models are already on the
   volume), waits for ComfyUI, and runs `validate` on the workflow.
3. **Render:** every line in sequence, ~112 s each (~4.7 h for 150 lines,
   ~6.4 h a batch with rerolls and boot).
4. **Check each clip:**
   - Trim it to the audio length (InfiniteTalk renders whole ~3.2 s windows).
   - Confirm the duration and that a face is present and moving.
   - Add it to a per-episode review sheet.
5. **Fix failures:** a failed or rejected line gets one reroll with a new seed,
   then goes to OmniHuman on PoYo. Lines whose keyframe shows more than one person
   go to OmniHuman from the start (§12). Every fallback is logged with its cost.
6. **Finish:** terminate the pod. Models stay on the volume; GPU billing stops.

### Engine work (2–4 days, on top of the shared work in §8)

| Piece | Size |
|---|---|
| RunPod lip-sync provider wrapping `rpcomfy`: create pod, wait, run, download, terminate | 1 day |
| Trim to audio length; set `num_frames` from the audio so no extra window renders | ½ day |
| Per-line prompt with emotion, from the screenplay | ½ day |
| Fallback chain: InfiniteTalk → one reroll → OmniHuman, with ledger entries | ½ day |
| Per-episode review sheet (contact sheet per line + play list) | ½ day |
| Two review checks (dialogue before TTS, voice emotion before GPU): port `scripts/drama_review.py` into the engine pipeline | ½ day |
| Pricing: secure 5090 at $1.19/h in `pricing.py`; fix the `wan2.2-image-to-video-fast` model id there | small |

### Rollout

1. **Episode 1 at 480p.** Review every dialogue line. Record the reroll rate,
   the fallback rate and GPU hours; they replace the estimates in §6.
2. **Decide on 720p after episode 1.** It needs the 720P base model (+17 GB on the
   volume, ~$1.20 a month) and roughly doubles GPU time: ~$310 instead of ~$245 a month.
3. **If 5090 stock blocks a batch:** allow other GPUs in EU-RO-1 (RTX PRO 6000,
   A100). If that keeps happening, move to serverless: build the image from
   `runpod-comfyui/image/Dockerfile`, push it, and attach the same volume to an
   endpoint (~$30 a month more).
4. **Kill switch:** if more than ~25% of an episode's lines fall back, run
   that episode's dialogue on OmniHuman (Route A cost) and investigate.

### Phase 2: motion shots on the same pod

A blind test (2026-10-29) rated self-hosted Wan 2.2 (`wan22_i2v`) at least as
good as PoYo on both motion shots: PoYo's s1 went slightly blurry; s4 preferred
self-hosted. At 135 s a clip ($0.045 vs PoYo's $0.06) it saves ~$22 ($38) a
month, taking Route B from ~$245 to ~$223. Output is 16 fps against PoYo's
interpolated 32 fps; adding RIFE interpolation costs ~10–20 s a clip and cuts
the saving to ~$15. Do it after the dialogue batch is running, in the same pod
session: render all motion clips, then all dialogue lines, with PoYo as the
fallback for motion.

### Still open

- **HeyGen Avatar IV** is untested; its API needs ~$5 of wallet credit. At ~$0.05/s it would be a cheaper fallback than OmniHuman if it rates well.
- **Two speakers in one frame** (MultiTalk) has no workflow yet; such shots go to OmniHuman for now.

## 12. Episode 1 in production (rev 5, 2026-10-29)

The first full episode, *మంత్ర రహస్యం* Ep 1 (Telugu, for the user's Mantra Shakti
channel), was built end to end with Route B tools plus self-hosted motion:
`videos/2026-10-29/mantra-rahasyam-ep1/` (README has every step and cost).

### What it took

| | v1 (180 s) | v2 (143 s, final) |
|---|---|---|
| Shots | 25: 6 lip-sync, 7 motion, 11 stills, 1 title | same, 11 keyframes retaken |
| Dialogue | InfiniteTalk 480p on RTX PRO 4500 | InfiniteTalk **720p** on RTX PRO 6000 (96 GB) |
| Motion | Wan 2.2, 16 fps | + RIFE to 32 fps |
| Pacing | holds up to 1.5 s | **0.5 s between lines** (user's rule) |
| Cost | $2.07 | +$2.59 → **~$4.66 for the episode** |

Breakdown of the $4.66: PoYo images $1.68 (42 incl. retakes), Sarvam ~$0.07,
RunPod $2.91 (RTX PRO 4500 134 min, RTX PRO 6000 31 min). Volume ~$9.10 a month on top.

### Measured GPU speeds and pod sizing

| Workload | Best pod | Measured | Unit cost |
|---|---|---|---|
| InfiniteTalk 720p | RTX PRO 6000 96 GB, $2.49/h, no block swap | ~150–174 s per 3.2 s window | ~$0.10–0.12 per window |
| InfiniteTalk 720p on 32 GB | (avoid) PRO 4500 with block swap | 458 s per window | $0.09, 3× slower |
| InfiniteTalk 480p | RTX PRO 4500 32 GB, $0.72/h | 136 s per window | $0.027 per window |
| Wan 2.2 motion clip | RTX PRO 4500 (213 s) or 5090 (135 s) | | ~$0.043–0.045 per clip |
| RIFE 2× | RTX PRO 4500 | ~20 s per clip | ~$0.004 |
| Qwen-Image-Edit keyframe | RTX PRO 4500 | 23–84 s | $0.005–0.017 |

The RTX 5090 was sold out in EU-RO-1 most of the day; the volume is tied to that
data center. **GPU policy (user decision): more than 24 GB, at most 48 GB, in EU-RO-1.**
Default: an RTX 5090 for everything, 720p dialogue with `block_swap=10`, falling back
automatically to an RTX PRO 4500 (32 GB, $0.72/h, slower, same value per unit of work)
and then an L40S (48 GB, $1.09/h, ~0.7× the 5090's speed) when 5090s are sold out
(`RPC_GPU` lists all three). A40/A6000 (no fp8) and PRO 6000 MIG 48GB were left out
as poor value. The 720p run on the PRO 6000 showed ~23 GB in use, but on the
32 GB 5090 `block_swap=0` ran out of memory (2026-10-30). With `block_swap=10` (now the default) it measured
220–260 s per window, ~$0.08. A fresh pod per batch takes ~2–4 min to boot.

### Route B at 720p, per 12-minute episode

150 lines × ~1.4 windows × 1.3 rerolls ≈ 273 windows (2.2×: 462).

| Element | 1.3× | 2.2× |
|---|---|---|
| Script, keyframes, voice (APIs) | $6.85 | $6.85 |
| Motion, PoYo | $10 | $17 |
| **Dialogue, InfiniteTalk 720p on RTX 5090 (default, est. ~13 GPU-h)** | **~$16** | **~$27** |
| OmniHuman fallback (5% / 10% of lines) | $3.15 | $6.30 |
| **Episode** | **~$36** | **~$57** |
| **Month (×8.7) + volume** | **~$320** | **~$505** |
| Same with dialogue on the PRO 4500 fallback (est. ~$19 / $32) | ~$38 an episode, ~$340 a month | ~$62, ~$545 |
| Motion moved to a PRO 4500 pod (phase 2) | saves ~$2.70 an episode (~$24 a month) | saves ~$4.70 (~$41) |

Dialogue at ~12–13 GPU-hours an episode is the long pole: split it across two
pods to finish a batch in ~6 hours. A 480p draft pass costs a quarter of
that and can be used for review before the 720p final, if review rounds get long.

### Observations

- **720p dialogue is clearly better** than 480p upscaled; it is the default now.
- **Some dialogue was "a bit off"** (user, after watching v1/v2). Exact lines not yet
  identified. Hence the review gate below.
- **Style leak:** the words "spiritual mysticism" put deity murals on hospital
  walls; per-shot style text that explicitly forbids pictures fixed it.
- **Two-person reference sheets** make Qwen-Image-Edit draw an extra person; keep
  one person per reference.
- **ffmpeg `drawtext` cannot shape Telugu** (broken conjuncts, even with
  `text_shaping=1`); the title card was rendered in headless Chrome.
- **InfiniteTalk renders whole windows** (81 frames, then +72): clips run longer
  than the line and must be trimmed to the audio.
- **One stopped pod could not restart** (host GPU taken); fresh pods per batch work.

### Keyframes: self-hosted Qwen vs PoYo (blind, 24 shots)

Qwen-Image-Edit 2511 (8-step, on RunPod) re-made all 24 Episode 1 keyframes from the
same PoYo character portraits: ~31 s and ~$0.017 an image (~$0.006 in a warm batch)
against PoYo's $0.04. The user rated 13 equal, 8 PoYo better, 3 Qwen better. PoYo won
on multi-character shots (2 of 3) and lip-sync start frames (3 of 6, the rest equal);
Qwen tied on shots without characters (5 of 7). **Keep PoYo for shots with faces; Qwen
is fine for character-free shots** (~$13 a month saved, not much). Per-character face
LoRAs are the route to moving all keyframes to the pod.

### Lip-sync with several people on screen: OmniHuman vs InfiniteTalk (blind, 3 shots)

In Episode 1, InfiniteTalk made **everyone in frame talk** on shots with more than one
person, even with "only the speaker talks" in the prompt. OmniHuman 2.0 (PoYo) re-rendered
those shots from the same keyframe and drive audio, with a prompt that names the speaker by
position and tells the others to keep their mouths closed (PoYo exposes no speaker mask).

| Shot | Speaker | Others in frame | Blind pick |
|---|---|---|---|
| s04 | Doctor | Lakshmi and Raghava, from behind | **OmniHuman** |
| s15 | Doctor | Nurse in the background | **OmniHuman** |
| s16 | Lakshmi | Abhinav in the bed | **OmniHuman** |

The user picked OmniHuman on all three. It cost 306.7 credits ($1.53) for 13 s of lines,
because lines under 4 s are billed at the 4 s minimum.

**Routing rule (2026-10-29):** a dialogue shot whose keyframe shows **more than one person**
goes to OmniHuman. A **single-person** shot stays on InfiniteTalk 720p.

**Cost impact (estimate).** OmniHuman costs ~$0.42 for a typical 2.9 s line (4 s billed),
against ~$0.04 on InfiniteTalk.
- If a third of the 150 lines in an episode have others in frame, dialogue rises by
  ~$20–27 an episode, so the month comes to ~$490–555 instead of ~$320.
- If half do, as in Episode 1, it is ~$600 a month.

Ways to keep the share down:
- Frame dialogue so the listeners are off-screen, turned away or out of focus. A
  single-person reverse shot stays on InfiniteTalk.
- Batch one character's lines in a scene into one OmniHuman call (up to 300 s) to avoid the
  4 s minimum on every line.

Script: `videos/2026-10-29/mantra-rahasyam-ep1/edit/omnihuman_trial.py`.

### Voices: Sarvam vs Smallest.ai (blind, 2 rounds, 6 lines)

- **Round 1** used the same speed setting on both. Smallest.ai came out ~2× faster and ran through
  "..." pauses, so pace decided it.
- **Round 2** matched each Smallest.ai take to the Sarvam length, with pauses. Scores (good 2, ok 1,
  bad 0): Sarvam 8, Smallest.ai Lightning v3.1 **Pro** 8, v3.1 2.
- Sarvam was good on the narrator, the doctor and Abhinav. Pro `sravani` was good on both of
  Lakshmi's emotional lines, where Sarvam was ok and bad.

- **Round 3** added ElevenLabs `eleven_v4` with native Telugu library voices and one inline emotion
  tag per line, e.g. `[sobbing, desperate]`. It was rated **good on all 6 lines**: 12, against
  Smallest.ai Pro 5 and Sarvam 4.

**Decision (2026-10-30):** ElevenLabs v4 voices every character, and it is now the engine's default voice-over
(`tts_router`). ElevenLabs is used for voice-over only, never for music or SFX. Its tags are the only direct emotion
control among the three, and they suit check 2 (voice emotion). This supersedes the round 2 split.

Smallest.ai notes, kept for reference:

Smallest.ai needs three extra steps:
- calibrate speed per line to a target length (its speed setting is uneven);
- join pieces under 12 characters to the next (alone they come back as babble or silence);
- run a speech-to-text check on every take.

Smallest.ai's TTS price is unpublished. Details:
`videos/2026-10-29/mantra-rahasyam-ep1/README.md`.

### Production gate: review dialogue and audio first

From the Episode 1 restart on (user rule, 2026-10-30), every stage is shown to the user and
approved before the next one is paid for:

1. **Script** (`script.md`): story beats, characters, scene plan.
2. **Dialogues**: every line (words, speaker, meaning, intended emotion) from `review/dialogue.md`.
3. **Audio**: every ElevenLabs take, checked for emotion in `review/voices.html` or the review reel.
4. **Images** (`refs/`, `frames/`): faces picked first, then frames on Kling O3 (Qwen for blood, Nano Banana Pro
   for pinned redos), bright and natural (§13).
5. **Videos** (`clips/motion/`): Wan 2.2 on RunPod for ordinary motion; Flow Omni 1.1 Flash for extraordinary shots (§13).
6. **Lip-sync + SFX + light instruments** (`clips/lipsync/`, `audio/sfx/`, `audio/music/`).
7. Final stitching: edit, mix, subtitles.

All six gates are enforced by `scripts/drama_review.py` (stages `script, dialogue, voices, images, videos,
lipsync`). An approval stores a hash of the script text, line text, audio or media bytes, so a change reopens
that item's gate and every gate after it. Each stage's script calls `require(P, <previous stage>)`. Catching a
problem early is cheap: a wrong line costs ~$0.0003 to fix as audio, and ~$0.25–0.50 after lip-sync.

## 13. Episode 1 restart: review board, Flow, image models (rev 6, 2026-10-30/31)

Episode 1 was restarted from a new, longer script (`videos/2026-10-30/mantra-rahasyam-ep1/`, ~5 min, 54 shots).
Gates passed so far: script, dialogue, audio. The images gate is in review.

### Remote review board (`apps/drama-review`)

- A Cloudflare Worker (R2 + D1, free tier, `drama-review.ngharish-conti.workers.dev`, no domain). Each reviewer opens
  a **personal link**; no account is needed. Wrangler's OAuth login can't set up Cloudflare Access, which is why the
  board uses links instead.
- Every line, take, frame or clip is a card with *Looks good*, *Needs change* (comment required) or *Comment*.
  Comments are tied to the item's hash, so a redo shows old comments as "on an earlier version". The owner's
  *Approve the whole stage* button feeds the local gate through `drama_board.py pull`, and only if the files are
  unchanged since publishing.
- `scripts/drama_board.py publish|pull|invite`. Images and clips carry captions from `review/captions.json`.
  Uploads retry dropped connections, and a cache records each finished file so a rerun skips it.
- In practice the family reviewed the dialogue twice (Daddy's Telugu edits, 16 lines) and picked every face on the
  board. Reviewers tended to use *Comment* for edits, so the stage note now asks for *Needs change*.

### Script and voices

- 16 scenes, 39 lines plus the chant (3 cycles, ~35 s). Hero rewritten as the kind, devoted son; the accident is him
  saving a little girl, which is why Shiva sends him back.
- New ElevenLabs cast picked by the user. **No two characters share a voice:** Shiva first shared one with Raghava,
  then with Tatayya, then with the doctor, and each time the other character was recast. Final cast is in each
  series bible and `edit/tts.py`.
- Every take is checked with Sarvam speech-to-text against the script. Low scores were only names written in
  English letters.
- Changing an emotion label reopens the dialogue gate, because emotions are part of the dialogue hash.

### Series assets and characters

- `videos/mantra-rahasyam/`: series README, `assets/characters/<name>/bible.md` (look, wardrobe per setting, voice
  id, continuity rules, an image prompt block used word for word) with approved `refs/`, and the shared chant audio.
- **Lord Shiva** matches the user's own Google Flow clip (TV-serial style: blue-grey skin, dark locks, tiger skin,
  orange sash). Two frames of the clip are his references, after the user tried two luminous golden versions first.
- **User-made Flow clips are used whole** (picture, lip-sync, voice): Shiva's last line is one. Flow clips carry a
  Gemini star logo bottom-right, removed with ffmpeg `delogo`; originals are kept in `source/`.

### Image models (PoYo, 1 credit = $0.005)

- **References:** main characters on Nano Banana Pro (18 cr), minor characters on Qwen Image 3. Faces picked by the
  family on the board.
- **Consistency test** (4 Abhinav shots on Qwen Image 3): face held, but **clothing and pose leak from the
  reference** (office shirt in the hospital bed). Fixed with per-shot wardrobe from the bibles, a face-crop
  reference, and "do not copy the reference's clothes or pose".
- **Limits met:** Qwen Image 3 takes at most **3 references**; Kling's `elements` input **fails for two people** on
  PoYo (plain references work); an empty `reference_image_urls` list also fails.
- **Blood:** only **Qwen** allowed the accident's blood. Nano Banana Pro and GPT Image 1.5 refused it, and GPT Image 2.5
  Flare turned the long prompt into a storyboard collage. GPT Image 1 errored.
- **Bake-off** of models cheaper than Qwen, on one-person, two-person and no-people shots (`edit/bakeoff/sheet.jpg`):

| Model (credits at 2K) | Faces | Mood | No-people shot |
|---|---|---|---|
| **Kling O3** (3.5) | strong | best | weak (dusk, empty road) |
| Flux 3 Klein Edit (2.8) | strong | smiled in a worried shot | best ambulance |
| Wan 2.7 Image (4.2) | good | neutral | good, misspelled sign |
| Seedream 5.0 Flash Edit (4) | ok | glows from the style text | a temple in the sky |
| Kling O1 Edit (3.5) | subject too far | laughing | no text-only model |
| Qwen Image 3 (4.8 + 0.5 per ref) | good | good | clean but empty road |

**Decision (user, 2026-10-31):** frames default to **Kling O3**; **Qwen Image 3 for blood or dark beats**; **Nano
Banana Pro for pinned redos**. The style line is split into everyday and divine wording, because "divine scenes
glowing" leaked glows into ordinary shots. Routing lives in the episode's `edit/frames.py`.

Self-hosted Qwen on RunPod costs about $0.45–0.90 for 53 frames against ~$1.55 on PoYo, but it's an older edit
model that lost to PoYo on faces (§12). The only bigger consistency step is per-character face LoRAs, which need
self-hosting.

### Google Flow (Veo, Omni, Nano Banana) through `flow-mcp`

- `GTAI-1/flow-mcp`, reviewed (network only to Google and Hugging Face, no telemetry; Chrome's debugging port 9333
  is open without auth while the window runs) and installed at `~/flow-mcp`, pinned to `84b548f`. It drives Flow in
  its own Chrome profile; the user signs in by hand. `scripts/flow_call.py` calls it from a running session.
- **Credits:** Nano Banana images 0 (Google publishes no limit); Omni 1.1 Flash 7 for 4 s, 12 for 8 s at 720p; Veo 3.1
  Lite 10, Fast 20, Quality 100 (fixed 8 s). User's Pro plan: 830 credits at the start.
- **Blind test, one ambulance shot from the same frame:** the user rated Veo 3.1 Lite and Wan 2.2 **equal**.
- **Decisions (user):** ordinary motion stays on **Wan 2.2 on RunPod**, to keep Flow credits for later episodes.
  **Extraordinary shots** (effects, divine moments, crucial beats) go to **Flow Omni 1.1 Flash**: six shots in Ep1,
  ~72 credits. Frames stay on PoYo, not free Flow stills.

### Lessons and bugs fixed

- **Save every paid task ID the moment it's accepted.** Two scripts lost IDs (a rate-limit crash, a manual stop),
  and PoYo has no way to list past tasks, so those runs were paid for but never collected.
- Space PoYo submissions (it returns 429) and remove finished or failed tasks from the resume list, or a rerun
  re-downloads frames that were moved aside.
- Frames for one-off or user-picked faces go in `refs/`; face crops go in `refs/_faces/` so the gate and board skip
  them.

### Spend so far on the restart

ElevenLabs ~2,900 characters (~$0.23); PoYo images ~870 credits (~$4.35), which includes the reference options,
53 frames, redos, the consistency test, the bake-off and the GPT test; Flow 10 credits; RunPod ~$0.24 (Wan test).
Cloudflare is free.

## Sources

- [Colab FAQ](https://research.google.com/colaboratory/faq.html), [Colab GPU features & pricing](http://mccormickml.com/2024/04/23/colab-gpus-features-and-pricing/), [Colab alternatives 2026](https://www.spheron.network/blog/google-colab-alternatives-8-gpu-clouds-compared-2026/)
- [Comfy Cloud pricing](https://www.comfy.org/cloud/pricing), [Comfy Cloud out of beta](https://blog.comfy.org/p/comfy-cloud-is-out-of-beta-and-its)
- [InfiniteTalk ComfyUI guide (VRAM)](https://learn.thinkdiffusion.com/latest-in-lipsync-infinitetalk-video2video-comfyui-guide/), [RunComfy InfiniteTalk workflow](https://www.runcomfy.com/comfyui-workflows/comfyui-infinitetalk-workflow-audio-portrait-to-lip-synced-video), [WanVideoWrapper readme mirror](https://huggingface.co/aliensmn/ComfyUI-WanVideoWrapper/blob/main/readme.md)
- RunPod catalog reads (`list-public-endpoints`, `list-hub-repos`, `list-gpu-types`), 2026-10-29
- [ArcReel overview](https://themenonlab.blog/blog/arcreel-story-to-video-multi-agent-pipeline), [ArcReel site](https://arcreel-website.pages.dev/en/)
- [dramaclaw summary](https://ai-tldr.dev/tools/dramaclaw/)
- [Open-source lip-sync licences 2026](https://www.creativeainews.com/articles/open-source-lip-sync-licenses-commercial-use-2026/), [MuseTalk](https://github.com/TMElyralab/MuseTalk), [Wav2Lip README mirror](https://huggingface.co/camenduru/Wav2Lip/resolve/main/README.md)
- [IndicF5 derivative card](https://huggingface.co/dheeyantra/dhee-indic-f5), [Indic Parler-TTS listing](https://dev.co/ai/llms/indic-parler-tts)
- [Image model licence matrix, Aug 2026](https://invideo.io/blog/open-source-image-models-licenses/), [Qwen-Image 2.1 licence](https://www.creativeainews.com/articles/qwen-image-2-1-research-license-commercial-use-2026/)
- [Wan open weights stopped at 2.2](https://howaiworks.ai/blog/alibaba-wan-open-weights-stopped-at-2-2), [RunPod Wan 2.7 guide](https://www.runpod.io/articles/guides/wan-2-7-runpod)
- [LongCat-Video-Avatar 1.5](https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5), [InfiniteTalk speed issue](https://github.com/MeiGen-AI/InfiniteTalk/issues/210), [LightWan2.2](https://huggingface.co/lightx2v/LightWan2.2-A14B)
- [MiniMax H3 on RunPod](https://www.runpod.io/blog/minimax-h3-the-open-weight-omni-modal-video-model-and-what-it-takes-to-run-it)
- Hosting prices (third-party aggregators unless noted; verify before budgeting): [Modal pricing (official)](https://modal.com/pricing), [fal pricing (official)](https://fal.ai/pricing), [Replicate pricing (official)](https://replicate.com/pricing), [RunComfy serverless billing (official)](https://docs.runcomfy.com/serverless/about-billing), [Vast.ai 5090 tracker](https://computeprices.com/providers/vast/gpus/rtx5090), [Vast.ai pricing guide](https://deploybase.ai/articles/vast-ai-gpu-cloud-pricing-complete-guide-vs-hr-for-every-gpu), [AWS g6e.xlarge prices](https://calculator.holori.com/aws/ec2/g6e.xlarge/us-east-1), [g6e.xlarge spot (DoiT)](https://compute.doit.com/spot/us-east-1/g6e.xlarge)
- Lip-sync APIs: [WaveSpeed InfiniteTalk](https://wavespeed.ai/models/wavespeed-ai/infinitetalk), [fal sync-lipsync v2](https://fal.ai/models/fal-ai/sync-lipsync/v2), [fal OmniHuman 1.5 comparison](https://fal.ai/learn/biz/omnihuman-15-vs-omnihuman-comparison)
