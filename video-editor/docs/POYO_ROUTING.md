# PoYo.ai Model & Credit Reference

PoYo.ai is the **recommended primary provider** for generative images and
video in this repo. It exposes a single, model-agnostic async API that hosts
many of the same models available on FAL.AI at lower marketplace pricing, while
also giving access to first-party models such as `gpt-image-2` and `veo3.1`.

FAL.AI remains configured as a fallback and can be selected explicitly with
`--image-provider fal_ai` / `--video-provider fal_ai`.

*Pricing and model list refreshed from [poyo.ai/pricing](https://poyo.ai/pricing)
on **2026-10-22**. Rates change frequently; verify live pricing before large
production runs. PoYo publishes no models/pricing API (`/api/models` and
`/v1/models` both 404), so this table is read from the web price list and the
per-model docs under `docs.poyo.ai/api-manual/`.*

---

## TL;DR — defaults for video templates

| Tier | Default image model | Default video model | When to use |
|---|---|---|---|
| 🟢 **Budget** | `flux-schnell` / `z-image` | `seedance-2-mini` / `wan2.2-image-to-video-fast` | Prototypes, bulk b-roll, internal drafts |
| 🔵 **Standard** (template default) | **`gpt-image-2`** 1K low | **`seedance-2-fast`** 720p | Most production reels; best quality/price balance |
| 🔴 **Premium** | `gpt-image-2` 2K/4K high or `nano-banana-2-api` (pro) | `seedance-2` 1080p+ or `sora-2-official` | Hero shots, brand films, cinematic output |

| Asset | Default model | Typical config | Approx. cost |
|---|---|---|---|
| **Image (provider default)** | `nano-banana-2-lite` | `aspect_ratio: 16:9` (or the request's width/height) | **~$0.025/gen** (5 credits, flat) |
| **Image (template standard tier)** | `gpt-image-2` | `size: 16:9`, `resolution: 1K`, `quality: low` | **~$0.010/gen** (2 credits) |
| **Video (standard)** | `seedance-2-fast` | `aspect_ratio: 16:9`, `resolution: 720p`, `duration: 5–10`, `generate_audio: false` | **~$0.07–$0.14/sec** (14–28 credits/sec) |

**Engine default.** `poyo_ai` generates images with `nano-banana-2-lite` unless
something names another model — `props.image_model` in a scene plan, or
`default_model` in `config.yaml`. It is the flat 5-credit still, so the cost
gate does not depend on two other props (`resolution`, `quality`) to be
correct. `gpt-image-2` is only $0.010 at its **low/1K cell** and jumps 17x at
high/4K, which is why it is a template *tier*, not a provider default.
Because nano-banana rejects gpt-image's `size`/`resolution` presets (#265), the
provider translates a plan's `size` preset into `aspect_ratio` and drops the
resolution tier for the aspect-ratio-only models (`nano-banana-2-lite`,
`nano-banana-2-1`).

## Draft vs final image chain

`assets.image_chain` is the draft chain. It starts with `agy_image` (free
quota, slower, the local Gemini agent), then falls through to `poyo_ai`,
`openrouter` and `fal_ai`.

`assets.image_chain_final` is the chain for a finished render. It skips the
local agent and starts with a direct image API:

```yaml
assets:
  image_chain: [agy_image, poyo_ai, openrouter, fal_ai]
  image_chain_final: [poyo_ai, openrouter, fal_ai]
```

Select it with `--final` on `ve short`, `ve explain`, `ve run`, `ve assets` or
`ve estimate`, or with `"final": true` in a worker `run` payload. The estimate
prices the same chain the run will use, and `max_images_per_run` counts the
selected chain.

The repo’s planning/LLM routing is handled by the `packages/llm` `ModelRouter`
(see [`MODEL_ROUTING.md`](./MODEL_ROUTING.md)), **not** by PoYo’s Chat API.
SFX and TTS are wired directly to ElevenLabs in this repo (with Sarvam Bulbul
v3 as the TTS provider for Indian languages — see
[`SARVAM_INTEGRATION.md`](./SARVAM_INTEGRATION.md)). Music uses the PoYo
`PoyoMusicProvider` for the storyteller templates and ElevenLabs for the
other templates; see the [Music generation (PoYo path)](#music-generation-poyo-path)
section below.

### New in the 2026-10-22 refresh — cheaper than the current defaults

| Model | Modality | Price | Why it matters |
|---|---|---|---|
| `gemini-omni-1-1-flash` | video | $0.225 / 4 s at 720p (~$0.056/s; ~$0.045/s at 10 s) | Google lists the same model at ~$0.10/s. An Oct-2026 hands-on benchmark scored it top of eight video models (23/24), level with `wan-3-0` and ahead of `veo-3.1` and `kling-o3`. |
| `veo-3-1-official` | video | $0.018/s (720p, silent) | Cheapest quality tier on the page; $0.030/s at 1080p silent, $0.030/s with audio. |
| `minimax-h3-max-turbo` | video | $0.040/s (768p) | Half the `minimax-h3` list rate; 480p $0.025, 1080p $0.080. |
| `seedance-2-5` | video | $0.315/s (720p, no input) | Now generally available (was "coming soon"); 480p $0.140, 1080p $0.570. Premium tier only. |
| `nano-banana-2-1` | image | $0.020 (1K) | Newer Gemini Flash than `nano-banana-2-lite`, 20 % cheaper. |
| `seedream-5-0-flash` | image | $0.020 | Cheapest Seedream tier, text-to-image and edit. |
| `flux-3-klein` | image | $0.009 (1K) | FLUX 3 at `gpt-image-2-5` money; 2K $0.014. |

**Recommendation:** the defaults stay on `seedance-2-fast` until a side-by-side
on a real scene clears `gemini-omni-1-1-flash` and `veo-3-1-official`; at 2.5–8×
lower cost per second they are the first things to benchmark. Both are priced in
`models.yaml`; since #262 the estimate prices the model a scene names in
`props.motion_model` (there is no `--video-model` flag), so a test scene can
point at either one directly.

---

## Auth

Add your API key to the repo-root `.env`:

```bash
POYO_API_KEY=sk-...
```

The provider reads `assets.providers.poyo_ai.api_key_env: POYO_API_KEY` from
`config.yaml`.

---

## Credit basics

PoYo bills in **credits**. Across the pricing tables the conversion is
consistent:

> **1 credit ≈ $0.005** (i.e. **$1 ≈ 200 credits**).

Charging unit depends on the API:

| API | Billing unit |
|---|---|
| Image | per generation, with 1K/2K/4K and quality multipliers |
| Video | per generated second (or per fixed-duration generation for Sora) |
| 3D | per generation |
| Chat | per 1M tokens (input/output split) |
| Music/Audio | per 1K characters (TTS), per minute (music), or per generation |
| Tools | per image, per minute, per second, or per megapixel |

---

## API shape

PoYo uses a unified two-endpoint flow for every model:

- `POST /api/generate/submit` → returns `data.task_id` and an initial status
- `GET /api/generate/status/{task_id}` → poll until `data.status == "finished"`
- Result media is in `data.files[].file_url`

Terminal statuses are `finished` and `failed`; intermediate statuses are
`not_started` and `running`. Generated URLs are short-lived (24 hours is a
common retention window), so the provider downloads and caches results
immediately.

Sources: [PoYo API overview](https://docs.poyo.ai/api-manual/overview),
[PoYo getting started](https://docs.poyo.ai/)

---

## Default configuration in `config.yaml`

```yaml
assets:
  providers:
    poyo_ai:
      api_key_env: POYO_API_KEY
      default_model: nano-banana-2-lite
      timeout: 300.0
      provider_options:
        video_model: seedance-2-fast
        poll_interval_seconds: 2.0
        poll_timeout_seconds: 600.0
    fal_ai:
      api_key_env: FAL_KEY
      default_model: fal-ai/flux/dev
      timeout: 300.0
  defaults:
    image: poyo_ai
    video: poyo_ai
```

`provider_options.video_model` is the slot used for `AssetType.VIDEO` so that
`default_model` (an image model) is never accidentally used for video requests.

---

## Image generation models

Models are grouped into three tiers by **quality vs. price**. Video
*templates* default to the standard tier; a bare `poyo_ai` image request
defaults to the budget tier's `nano-banana-2-lite`.

### 🟢 Budget tier — prototypes, bulk, or cost-first

**Best quality in this tier:** `nano-banana-2-lite` — for $0.025/gen it delivers
Gemini Flash quality, wide aspect ratios, and solid text rendering; better
overall output than `kling-o3-image` at the same price point.

| Rank | Model ID | Best for | Price | Credits | Notes |
|---|---|---|---|---|---|
| 1 | `nano-banana-2-lite` | Fast Gemini Flash images | $0.025/gen | 5 | Best budget quality: wide aspect ratios, decent text, good anime/illustration. |
| 2 | `kling-o3-image` | Low-cost Kling images | $0.018–$0.035/image | 3.5–7 | 1K–2K and 4K tiers; text-to-image and image-edit. Best Kling budget option. |
| 3 | `z-image` | Ultra-low-cost single images | $0.010/gen | 2 | Cheapest per-generation option. Quality is basic. |
| 4 | `flux-schnell` | Cheapest usable stills | $0.0024/MP | 0.48/MP | Billed per megapixel. Good for rough cuts and placeholders only. |

### 🔵 Standard tier — default for most reels

**Best quality in this tier:** `gpt-image-2` (`medium` quality, 1K) for
photorealism/text/prompt adherence; `nano-banana-2` (2K default) is a close
alternative for anime/illustration, multi-subject scenes, and native 4K.

| Rank | Model ID | Best for | Price | Credits | Notes |
|---|---|---|---|---|---|
| 1 | **`gpt-image-2`** | Cinematic stills, prompt-adherent marketing frames, anime/illustration | **Low:** $0.010/gen (1K) → **High:** $0.321/gen (4K) | 2 → 64 | Template standard tier. Best photorealism, text rendering, and reference handling. `size` presets + custom `WIDTHxHEIGHT`; `resolution` `1K`/`2K`/`4K`; `quality` `low`/`medium`/`high`. |
| 2 | `nano-banana-2` / `nano-banana-2-new` | Fast Gemini Flash images | $0.025 (1K) / $0.040 (2K) / $0.060 (4K) | 5 / 8 / 12 | 2K default, up to 14 reference images, strong multilingual text and anime/illustration. The `official@` tiers are $0.050 / $0.075 / $0.100. |
| 3 | `seedream-4.5` / `seedream-5.0-lite` | ByteDance Seedream (standard) | $0.025/gen | 5 | Strong prompt adherence without the pro price. `seedream-5.0-lite` is the `seedream-5-0-lite-api` slug; text-to-image and image edit both 5 credits. |
| 4 | `grok-imagine` | xAI Grok images | $0.030/gen | 6 | Good general-purpose quality. |
| 5 | `flux-dev` | Higher-quality Flux | $0.020/MP | 4/MP | Official Flux dev pricing. Slightly more refined than `flux-schnell`. |
| 6 | `gpt-4o-image` | Quick concept art, editable images | $0.020/gen | 4 | Smaller prompt budget; good for fast iteration. |
| 7 | `nano-banana-2-lite` | Fast Gemini Flash Lite images | $0.025/gen | 5 | Faster/cheaper variant with lower max quality than `nano-banana-2`. |

### 🔴 Premium tier — hero frames and high-fidelity output

**Best quality in this tier:** `gpt-image-2` at `high` quality + `4K`
resolution — the top cinematic output available on PoYo for single stills.
For native 4K, anime/illustration, or multi-subject hero frames, use
`nano-banana-2-official` 4K or `nano-banana-pro`.

| Rank | Model ID | Best for | Price | Credits | Notes |
|---|---|---|---|---|---|
| 1 | `gpt-image-2` (`high`, `4K`) | Best cinematic output | $0.321/gen | 64 | Same model as default, but pushed to highest quality/resolution. |
| 2 | `nano-banana-2-official` / `nano-banana-pro` (4K) | Native 4K Google Gemini images | $0.075–$0.100/gen (official 4K); pro higher | 15–35 | Native 4K, strong multi-subject and anime/illustration, up to 14 reference images. Close to GPT Image 2 quality. |
| 3 | `seedream-5.0-pro` | Top Seedream quality | not on the 2026-10-22 price page | — | Was $0.075/gen. Still reachable through OpenRouter as `bytedance-seed/seedream-5-0-pro` (~$0.045/image); on PoYo prefer `nano-banana-2-api` (pro) in the meantime. |
| 4 | `flux-kontext` (Pro / Max) | Native editable images | $0.040–$0.080/gen | 8–16 | Official Kontext generation + edit. Best when you need iterative refinement, not raw quality. |
| 5 | `grok-imagine-quality` | xAI Grok 1K/2K quality | $0.040–$0.055/gen | 8–11 | Quality variant with reference support. |

### Image default recommendation

**`nano-banana-2-lite`** is the provider default: a flat 5 credits
(~$0.025) and the best quality/price still for most reels. It takes
`aspect_ratio` only (the provider derives it from the request's width/height
and translates a plan's `size` preset), so there is no resolution or quality
tier to pin.

Move to **`gpt-image-2` at `size: 16:9`, `resolution: 1K`, `quality: low`**
(~$0.01/frame) when you need its prompt adherence, text rendering, or reference
handling, and to premium settings only for hero frames. Because that price is
the cheapest cell of a 3x3 quality x resolution matrix, name the model *and*
both tiers in the plan (`props.image_model`, `image_resolution`,
`image_quality`) so the gate quotes what the run will pay.

**The cost gate prices the tier, not just the model (since #269).** A model
whose rate depends on a request attribute declares it in `models.yaml`:

```yaml
gpt-image-2.5-fixed:
  cost_per_image: 0.0085        # base tier (1K, 1.7 credits)
  price_by:
    resolution:
      "2K": {cost_per_image: 0.03}    # 6 credits
      "4K": {cost_per_image: 0.04}    # 8 credits
```

`ve estimate` and `BudgetGovernor` read the tier from the request's own params,
so a plan that sets `props.image_resolution: "2K"` is quoted at $0.03/frame and
reserves $0.03/frame. An unknown or absent tier falls back to the base rate —
never to $0.00.

A tier entry may itself carry `price_by`, for models that price on more than
one axis. `gpt-image-2` (the template standard tier) bills a quality x resolution
matrix, so both axes are data since #270:

```yaml
gpt-image-2:
  cost_per_image: 0.01          # low 1K, the cheapest cell
  price_by:
    quality:
      high:
        cost_per_image: 0.169   # high 1K; used when no resolution is asked
        price_by:
          resolution:
            "4K": {cost_per_image: 0.321}   # 64 credits
```

A plan declaring `props.image_quality: "high"` and
`props.image_resolution: "4K"` is therefore quoted $0.321/frame; quality alone
gets that quality's 1K rate ($0.169), and an unpriced resolution keeps the
quality rate rather than dropping to the cheapest cell.

Sources: [PoYo GPT Image 2 docs](https://docs.poyo.ai/api-manual/image-series/gpt-image-2),
[PoYo pricing](https://poyo.ai/pricing)

---

## Video generation models

Models are grouped into three tiers by **quality vs. price**. The default for
video templates is the standard tier.

### 🟢 Budget tier — prototypes, bulk b-roll, or cost-first

**Best quality in this tier:** `kling-o3` (720p no audio) — cheapest reliable
Kling tier with decent motion and reference support.

| Rank | Model ID | Best for | Price | Credits | Res / Duration | Notes |
|---|---|---|---|---|---|
| 1 | `kling-o3` | Lower-cost Kling | $0.050–$0.250/sec | 10–50/sec | Standard/Pro/4K tiers | Text/image/reference-to-video. 720p no-audio is the sweet spot. |
| 2 | `seedance-2-mini` | Cheapest Seedance clips | 480p: $0.030/sec with input, $0.050/sec without; 720p: $0.063 / $0.120/sec | 6–24/sec | 480p/720p; 4–15 s | Good for prototypes or bulk generation. Supports image/reference inputs. |
| 3 | `hailuo-02` | Fast realistic motion on a budget | $0.035–$0.325/sec or fixed price | 7–65 | 768p/1080p | NCR architecture, realistic physics. Price spread can push it above budget. |
| 4 | `wan2.2-image-to-video-fast` | Cheapest fast iteration | $0.030–$0.060/gen | 6–12 | 480p/720p | Lowest per-clip cost, but quality is basic. |
| 5 | `veo3.1-fast-official` | Fast Google VEO | $0.018–$0.050/sec | 3.6–10/sec | Not fully stated | Very cheap official fast tier; specs and support are less documented. |

#### Storyteller budget motion (`wan2.2-image-to-video-fast`)

`wan2.2-image-to-video-fast` is the API model ID in PoYo's Capability Catalog (checked
2026-11-11, #319). The pricing page's `/models/wan-2-2-fast` is only a web slug: the API
rejects it (`model_capability_not_found`). Inputs are `prompt`, `image_urls` (start frame,
optional end frame), `resolution` (480p or 720p) and `seed`; there is no duration, so each
request is one fixed-length clip billed per request (480p $0.03, 720p $0.06). It is the recommended budget video model for narrator-heavy
storyteller cuts. Storyteller budget motion scenes — adds gentle parallax to a
still image, ideal for narration-heavy cuts. Used by the `storyteller_budget`
template and accessible in any storyteller preset via `--video-tier budget`.

### 🔵 Standard tier — default for most reels

**Best quality in this tier:** `veo3.1-official` (720p/1080p) — strongest
photorealism and camera motion, though it caps at ~8 s and costs slightly more.
For longer clips with good quality, use `seedance-2-fast`.

| Rank | Model ID | Best for | Price | Credits | Res / Duration | Notes |
|---|---|---|---|---|---|
| 1 | `veo3.1-lite` / `veo3.1-official` | Google VEO quality tiers | $0.10–$0.30/video or $0.05–$0.36/sec | 20–72 | 720p–4K | 4–8 s typical. Strongest physics and camera motion in standard. |
| 2 | **`seedance-2-fast`** | Fast, affordable motion clips; default b-roll | 480p: $0.045–$0.070/sec; 720p: $0.100–$0.140/sec | 9–28/sec | 480p/720p only; 4–15 s | **Default.** Best balance of quality, length (up to 15 s), and cost. The price page renders this block (the cheaper of the two Seedance 2 blocks) without its own slug; the API slug the provider sends is `seedance-2-fast`. |
| 3 | `hailuo-2.3` | Expressive characters | $0.175–$0.300/video | 35–60 | 768p/1080p; 6 s / 10 s | Realistic human motion, first-frame guidance. Fixed-price per clip. |
| 4 | `wan-2.7-video` | Latest Wan generation | $0.060–$0.090/sec | 12–18/sec | 720p/1080p | Text/image/reference/edit-to-video. Good quality/cost ratio. |
| 5 | `kling-3.0-turbo` | Speed-optimized Kling | 720p $0.085/sec; 1080p $0.110/sec | 17–22/sec | 720p/1080p | Multi-shot storyboarding. A solid upgrade from Seedance fast. |
| 6 | `happy-horse` / `happy-horse-1.1` | Happy Horse motion | $0.080–$0.160/sec or $0.11–$0.14/sec | 16–32/sec | 720p/1080p | Text/image/reference-to-video + video editing. |

### 🔴 Premium tier — hero shots, cinematic quality

**Best quality in this tier:** `seedance-2` (1080p/4K) — strongest all-round
cinematic quality, supports first/last frame and references, and scales to 4K.
For OpenAI-native physics/character motion, use `sora-2-official`.

| Rank | Model ID | Best for | Price | Credits | Res / Duration | Notes |
|---|---|---|---|---|---|
| 1 | `seedance-2` | Higher-fidelity cinematic motion | 480p: $0.045–$0.100/sec; 720p: $0.100–$0.200/sec; 1080p: $0.310–$0.450/sec; 4K: $0.640–$1.00/sec | 9–200/sec | 480p–4K; 4–15 s | First/last frame + multimodal references. Best overall cinematic quality on PoYo. |
| 2 | `sora-2-official` | OpenAI Sora clips | $0.20–$1.20/video by duration tier | 40–240 | Fixed 4/8/12/16/20 s | Also per-second rates: 720p $0.24/sec, 1024p $0.40/sec, 1080p $0.56/sec. Best physics/character motion. |
| 3 | `kling-3.0` | Standard/pro Kling | 720p $0.135–$0.245/sec; 4K $0.250/sec | 27–50/sec | 720p/1080p/4K | Optional synchronized audio; element consistency. |
| 4 | `runway-gen-4.5` | Runway cinematic | $0.375–$0.750/video | 75–150 | 5 s / 10 s | Cinematic motion; optional reference image. |
| 5 | `wan-2.6` | Longer Wan clips | $0.400–$1.80/video | 80–360 | 720p/1080p; 5/10/15 s | Text/image/video-to-video, multi-shot. |
| 6 | `omni-flash` | Omni Flash fusion | $0.60–$2.25/gen (no input); up to $2.00/gen with input | 120–450 | 720p/1080p/4K; 4–10 s | Three-image reference fusion. **Expensive** — use selectively for special hero shots. |
| 7 | `seedance-2-5` | Long-form consistency, region editing | Coming soon | — | Up to 4K; ~30 s single segment | 50 multimodal references; not available yet. |

### Video default recommendation

**`seedance-2-fast` at `aspect_ratio: 16:9`, `resolution: 720p`,
`duration: 5–10`, `generate_audio: false`.**

At 720p without reference video, a 5 s clip costs ~$0.70 and a 10 s clip ~$1.40.
This is the best balance of speed, cost, and quality for standard reels.

**Important:** pass `generate_audio: false` for Seedance models unless you
explicitly want generated audio. The default provider injects this flag for
video requests to avoid content-policy rejections and unexpected audio tracks.

Sources: [PoYo text-to-video models](https://poyo.ai/tasks/text-to-video),
[PoYo Seedance 2 docs](https://docs.poyo.ai/api-manual/video-series/seedance-2),
[PoYo Seedance 2 Mini docs](https://docs.poyo.ai/api-manual/video-series/seedance-2-mini)

---

## Music generation (PoYo path)

> ⚠️ **Cost rule (Bug #229): do NOT use ElevenLabs for music generation.**
> `elevenlabs_music` is ~$0.15/minute, several times the PoYo path
> (`poyo_music`, ~$0.10 per generation) and it is never a sensible default.
> All templates, the CLI `--music-provider` default, and the built-in fallback
> now use `poyo_music`; `ace_step` is the free/local option.

The storyteller templates (`storyteller_budget` / `storyteller_hd` /
`storyteller_premium`) use PoYo for background music instead of ElevenLabs,
via the `PoyoMusicProvider` in
`packages/asset_ingestion/src/video_editor_asset_ingestion/providers/audio/poyo_music.py`.
It implements the `MusicProvider` protocol, so the storyteller planner can
swap between PoYo and ElevenLabs without code changes.

Alternative music providers: `ace_step` (free/local) and the opt-in `openrouter`
gateway (Google Lyria, experimental — no documented endpoint or published
pricing). See [`OPENROUTER_GENERATION.md`](OPENROUTER_GENERATION.md).

> **Choosing between providers for the same model.** When a provider is `auto`
> and a model is named, selection is cost-first and picks the cheapest configured
> provider that serves that model (`models.yaml:model_equivalents`). See
> [`MODEL_ROUTING.md`](MODEL_ROUTING.md).

PoYo exposes music models through the same submit/poll API as images and
video: `POST /api/generate/submit` returns a `task_id`; the provider polls
`GET /api/generate/status/{task_id}` until `data.status == "finished"` and
downloads `data.files[0].file_url` (Suno reports the URL as `audio_url`;
the provider accepts either key).

| Model ID | Underlying provider | Price | Credits | Notes |
|---|---|---|---|---|
| **`generate-music`** | Suno | **$0.10/gen** | 20 | **Default.** Answers in well under a minute (verified 2026-10-23, ~25 s). Needs `custom_mode: false` + an `mv` tag. |
| `minimax-music-2.6` | MiniMax (sourced via Runware) | **$0.10/gen** | 20 | Fallback. Same price, but has taken several minutes per bed. Needs `is_instrumental` and rejects `custom_mode`/`mv`. |

> Model ids are exact. `minimax-music-2-6` (hyphens) is **not** a PoYo model
> and answers `Model not found`. Likewise `suno-v5`, `music-gen`,
> `minimax-music-2-5` and `ace-step` all 404 on this endpoint (checked
> 2026-10-23) — `ace-step` is a local provider in this repo, not a PoYo id.

Each backend validates `input` separately, so `PoyoMusicProvider._build_input`
sends a different body per model family; a shared body cannot serve both.
PoYo reports an unknown model as HTTP 400, so the provider treats any 4xx from
`/submit` as "this model cannot serve it" and falls back.

Both backends bill per generation and return a **whole composition**, not a
clip of the requested length: a 30 s request came back as a 224 s track
(2026-10-23). `PoyoMusicProvider` therefore reports the probed length of the
file it downloaded, and the mixer (`-stream_loop -1` + `-shortest`) trims it
to the video.

### Cost comparison: PoYo music vs ElevenLabs music

| Provider | Model | Price unit | Effective cost per 60 s | Notes |
|---|---|---|---|---|
| **PoYo** | `generate-music` | per generation (~60 s bed) | **~$0.10/gen** | One generation yields a usable music bed. |
| ElevenLabs | `elevenlabs-music` | per minute | **~$0.15/min** | ~1.5× the PoYo path for a 60 s bed. |

For a 10-minute story video that needs a single 60 s looped bed, the PoYo
path costs ~$0.10 versus ~$0.15 for ElevenLabs — a ~1.5× saving on the music
line item (and PoYo bills per generation, so longer beds do not scale the
price). The storyteller templates default to `poyo_music` for this reason;
switch back to ElevenLabs via `--music-provider elevenlabs_music` when you need
ElevenLabs' specific music style controls.

Sources: [PoYo pricing](https://poyo.ai/pricing),
[ElevenLabs music pricing](https://elevenlabs.io/pricing)

---

## Template selection guide

Use this table to pick the right **tier** for a scene or full template:

| Template / scene type | Image tier | Video tier | Recommended image model | Recommended video model | Why |
|---|---|---|---|---|---|
| **Standard talking-head / marketing reel** | Standard | Standard | `gpt-image-2` 1K low | `seedance-2-fast` 720p | Cheap, fast, good enough for most b-roll. |
| **Cinematic hero shot / high-end brand** | Premium | Premium | `gpt-image-2` 2K–4K high | `seedance-2` 1080p / `veo3.1-official` | Quality matters more than cost. |
| **Rapid prototyping / bulk b-roll** | Budget | Budget | `flux-schnell` / `z-image` | `seedance-2-mini` / `wan2.2-image-to-video-fast` 480p | Lowest per-asset cost. |
| **Portrait / vertical short-form** | Standard | Standard | `gpt-image-2` `9:16` | `seedance-2-fast` `9:16` 720p | All Seedance models support 9:16. |
| **Photorealistic / camera-motion-heavy** | Standard | Premium | `gpt-image-2` 1K low | `veo3.1-official` / `sora-2-official` | Strong physics and camera motion. |
| **Character / expressive motion** | Standard | Standard/Premium | `gpt-image-2` 1K low | `hailuo-2.3` / `kling-3.0` | Good for human/character motion. |
| **Long-form consistency (coming soon)** | Standard | Premium | `gpt-image-2` | `seedance-2-5` | Designed for longer, consistent sequences. |
| **Image-to-video / first-last frame control** | any | Standard/Premium | — | `seedance-2` / `seedance-2-fast` | Native first/last frame + reference support. |

When building a new template, default to the **standard tier** and promote
individual scenes to **premium** only when the script calls out hero shots,
complex motion, or high visual fidelity. Keep the **budget** tier behind a flag or
preset so it is an intentional cost-saving choice, not the default.

---

## Cost examples for common reels

Assumptions: images are `gpt-image-2` 16:9 1K; videos use `generate_audio: false`.

| Reel type | Image assets | Video assets | Estimated total |
|---|---|---|---|
| **10-scene standard reel** | 10 × 1K low = ~$0.10 | 5 × 5 s 720p fast no input = ~$3.50 | **~$3.60** |
| **20-scene cinematic reel** | 20 × 1K medium = ~$0.84 | 10 × 8 s 1080p seedance-2 no input = ~$24.80–$36.00 | **~$25.65–$36.85** |
| **8-scene prototype reel** | 8 × 1K low = ~$0.08 | 4 × 4 s 480p mini = ~$0.48–$0.80 | **~$0.56–$0.88** |
| **Vertical 9:16 short (6 scenes)** | 6 × 1K low = ~$0.06 | 3 × 5 s 720p fast 9:16 = ~$2.10 | **~$2.16** |

Add ~20–50% buffer for retries, reference inputs, and higher quality tiers.

---

## PoYo vs FAL.AI cost snapshot

| Asset type | PoYo default | FAL default | Rule of thumb |
|---|---|---|---|
| 1× 16:9 1K image (`gpt-image-2` low) | ~$0.01 | `fal-ai/flux/dev` ~$0.05 | PoYo is usually cheaper for image-first workflows. |
| 1× 5 s 720p video (`seedance-2-fast`) | ~$0.70 | FAL Luma endpoints are deprecated/unreliable | Use PoYo Seedance for reliable video generation. |

The actual price varies by resolution, duration, quality, and credit
multipliers; always check the [PoYo pricing page](https://poyo.ai/pricing) for
current rates.

---

## Other PoYo APIs (not wired to the video pipeline)

PoYo also exposes Chat, 3D, Music/Audio, and Tools APIs. This repo does **not**
currently use them for the main `ve run` pipeline:

| API | What it offers | Why we don’t use it here |
|---|---|---|
| **Chat** | Claude, GPT, Gemini, DeepSeek, etc. | `packages/llm` routes through Ollama Cloud Pro / OpenAI-compatible endpoints configured in `config.yaml` (see `MODEL_ROUTING.md`). |
| **3D** | Meshy, Hunyuan 3D, Tripo3D | Not needed for 2D video templates. |
| **Music / Audio** | ElevenLabs TTS, music, SFX, etc. | TTS/SFX wired directly to ElevenLabs (Sarvam for Indian languages via `docs/SARVAM_INTEGRATION.md`). Music is wired through the PoYo `PoyoMusicProvider` for the storyteller templates — see [Music generation (PoYo path)](#music-generation-poyo-path). |
| **Tools** | Video background removal, upscaler, image/video translator | Available for future use but not currently integrated. |

If you want to route music/TTS through PoYo instead, add a new provider wrapper
in `packages/asset_ingestion` and update `config.yaml`.

---

## CLI usage

Generate images with PoYo:

```bash
ve assets my-job \
  --tts-provider elevenlabs_tts \
  --image-provider poyo_ai
```

Generate both images and motion video (only scenes with
`props.requires_motion: true` get a video clip):

```bash
ve assets my-job \
  --tts-provider elevenlabs_tts \
  --image-provider poyo_ai \
  --video-provider poyo_ai
```

Override the video model per request via `model_kwargs` in a custom composer
script:

```python
request = AssetRequest(
    asset_type=AssetType.VIDEO,
    prompt="...",
    provider="poyo_ai",
    params={
        "model_kwargs": {
            "model": "seedance-2",
            "resolution": "1080p",
            "duration": 8,
            "generate_audio": False,
        }
    },
)
```

---

## Switching back to FAL.AI

FAL is still registered and configured. Explicitly select it:

```bash
ve assets my-job --image-provider fal_ai --video-provider fal_ai
```

Or use the legacy composer script:

```bash
USE_FAL=1 uv run python scripts/compose_anime_tram.py
```

---

## Stock footage with Pexels

[Pexels](https://www.pexels.com) provides a free, attribution-friendly stock
photo and video API. The engine registers it as a visual provider named
`pexels` under the same asset-ingestion system used by PoYo and FAL.

### Auth

Add your API key to the repo-root `.env`:

```bash
PEXELS_API_KEY=your_key_here
```

`config.yaml` already declares the provider block:

```yaml
assets:
  providers:
    pexels:
      api_key_env: PEXELS_API_KEY
      timeout: 60.0
      provider_options:
        per_page: 5
        orientation: landscape
```

### CLI usage

```bash
# Stock images for an explainer
ve assets my-job --image-provider pexels

# Stock video for motion scenes
ve assets my-job --video-provider pexels

# Stock b-roll in the raw-footage pipeline
ve run ./talk.mp4 --job-id webinar \
    --work-root ./projects --output-root ./outputs \
    --broll-provider pexels
```

### How it works

- `AssetType.IMAGE` → `GET /v1/search`
- `AssetType.VIDEO` → `GET /videos/search`
- The scene's `visual_description` is used as the search query.
- The first matching result is downloaded and cached like any other asset.
- Result metadata includes the original Pexels URL and photographer/
  videographer attribution.

### Caveats

- Pexels is **search-based**, not generative. You cannot prompt a specific
  composition; you receive the first matching stock result.
- Niche queries may return no results. The provider raises a clear error in
  that case.
- No TTS, music, SFX, or data-viz support — Pexels is image/video only.

## agy (Gemini CLI) as the first image provider

`config.yaml` sets `assets.image_chain: [agy_image, poyo_ai, openrouter,
fal_ai]`; the model default is an empty chain, which restores cost-first
selection. `agy_image` runs `agy --sandbox -p …` locally and verifies the
written PNG before returning it. It costs $0 (it consumes Google OAuth quota),
so `--budget` only limits the paid fallbacks. `max_images_per_run` (default 25)
bounds quota use per run. Expect seconds-to-minutes per image: this is an agent
round-trip, not an API.

The chain applies when a request names no provider, or names `auto`. `auto` is
the default for both `ve explain --image-provider` and `ve run
--image-provider`, so their stills walk the chain without any flag. Any other
named provider bypasses the chain — and `ve run` accepts a named provider only
on the storyteller path, because the other templates build no image requests.
When only the chain is configured (`auto`), approval gates price the first
member of the chain that the registry prices above zero (agy_image is
quota-based and unpriced, so the shipped chain prices at `poyo_ai`).

The chain keeps `openrouter` immediately before `fal_ai` because `fal_ai` is
the only provider with a stub fallback: when fal_ai's own generation fails, the
job still gets a placeholder asset rather than an error. Pre-flight failures
on the last attempt — an open circuit breaker or an exhausted `--budget` —
still surface as errors.
