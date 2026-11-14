# OpenRouter generation (image, video, music)

OpenRouter is no longer LLM-only: it fronts dedicated **image**, **video**, and
**audio** (music/TTS) generation APIs behind one key and one bill. The
`openrouter` asset provider (`packages/asset_ingestion/.../providers/visual/openrouter.py`)
exposes that gateway as a first-class provider for `image`, `video`, and
`music` assets.

> **Defaults are unchanged.** PoYo remains the default image/video provider and
> `poyo_music` the default music provider. OpenRouter is opt-in per run.

## Enabling it

`config.yaml` already declares the provider (key: `OPENROUTER_API_KEY`, already
in `.env`):

```yaml
assets:
  providers:
    openrouter:
      api_key_env: OPENROUTER_API_KEY
      base_url: https://openrouter.ai/api/v1
      default_model: openai/gpt-image-2        # image model
      timeout: 300.0
      provider_options:
        video_model: google/veo-3.1-fast
        music_model: google/lyria-3-pro-preview
        # Optional, passed through when set:
        # aspect_ratio: "9:16"
        # resolution: "1080p"
        # quality: high
        poll_interval_seconds: 5.0
        poll_timeout_seconds: 900.0
```

## Using it

```bash
# Per-run provider overrides (scene-plan flow)
ve assets my-job --image-provider openrouter --video-provider openrouter
ve render my-job

# Music via OpenRouter (experimental — see caveats)
ve run ./script.md --template explainer_reel \
  --music-prompt "calm ambient underscore" --music-provider openrouter
```

Requests may also set `params` per asset:

| param | applies to | notes |
|-------|-----------|-------|
| `model` | image/video/music | overrides the configured slot |
| `aspect_ratio` | image/video | e.g. `9:16`, `16:9` |
| `resolution` | image/video | e.g. `1080p`, `2K` |
| `quality` | image | `low`/`medium`/`high` |
| `duration_seconds` | video | mapped to OpenRouter `duration` |
| `model_kwargs` | all | extra request fields (seed, n, …) |

## How each modality works

- **Image** — synchronous `POST /api/v1/images`; the provider decodes
  `data[0].b64_json`, validates the magic bytes, and writes the file.
- **Video** — asynchronous `POST /api/v1/videos` → poll `GET /api/v1/videos/{id}`
  until `completed` → download `unsigned_urls[0]` (bearer token required).
  Duration is probed with FFmpeg.
- **Music** — chat completions with `modalities: ["text","audio"]` (e.g.
  `google/lyria-3-pro-preview`). Audio is returned base64 on
  `choices[0].message.audio`.

## Pricing (live 2026-10-22)

Stored in `models.yaml` → generated into `pricing.yaml` → consumed by
`ve estimate` / budget governance. Verify live pricing before large runs
(`curl -s https://openrouter.ai/api/v1/models`).

### Image (per image)

| Model | Cost |
|-------|------|
| `bytedance-seed/seedream-5-0-pro` | **$0.045** (token-priced, ~4175 tokens/image) |
| `bytedance-seed/seedream-5-0-flash` | ~$0.018 |
| `black-forest-labs/flux-3-image` | ~$0.041 (was mis-listed at $0.10) |
| `black-forest-labs/flux-3-image-klein` | ~$0.008 |
| `openai/gpt-image-2` | ~$0.13 high / $0.032 medium / $0.008 low |
| `google/gemini-3-pro-image` (Nano Banana Pro) | ~$0.155 |

### Video (per output second)

| Model | Cost/s |
|-------|--------|
| `google/veo-3.1-lite` | $0.08 (audio) / $0.05 silent |
| `alibaba/wan-3.0` | $0.10 @720p ($0.05–0.20 by res) |
| `kwaivgi/kling-v3.0-std` | $0.084 ($0.126 w/ audio) |
| `kwaivgi/kling-v3.0-pro` | $0.112 ($0.168 w/ audio) |
| `runway/gen-4.5` | $0.12 |
| `google/veo-3.1-fast` | $0.12 (audio) / $0.10 silent |
| `minimax/hailuo-3` | $0.13 |
| `google/veo-3.1` | $0.40 (audio) / $0.20 silent |

### Music

| Model | Cost |
|-------|------|
| `google/lyria-3-pro-preview` | preview — **pricing not published** |

## Caveats

- **Music is experimental.** OpenRouter documents image and video generation,
  but has no dedicated music endpoint and does not publish Lyria pricing. The
  provider drives it through chat-completions audio output and parses the
  response defensively. Prefer `poyo_music` (~$0.10/track) or `ace_step` (free)
  unless you specifically need a Lyria model.
- **Video is billed per second** and is asynchronous — long clips cost real
  money. Keep it opt-in.
- **Never use ElevenLabs for music** (Bug #229); OpenRouter music is the
  sanctioned OpenRouter path if you need it.
