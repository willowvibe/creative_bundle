# Sarvam AI Bulbul TTS Integration

The video-editor uses Sarvam AI's **Bulbul** text-to-speech models for
Indian-language narration. Sarvam Bulbul v4 Flash is the primary model; Bulbul
v3 is the automatic fallback when v4 Flash is unavailable, and `bulbul:v2`
remains a legacy override. For Indian languages the Sarvam path is roughly
**2× cheaper than ElevenLabs TTS** (₹20 / 10K chars for v4 Flash vs ElevenLabs'
per-character pricing), so the storyteller templates route Indian-language
speakers through Sarvam by default and fall back to ElevenLabs for non-Indian
languages.

The provider lives at
`packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/sarvam_tts.py`
and is selected by the `tts_router` when a speaker's language resolves to an
Indian-language code.

## Scope: speech, not sound effects

Bulbul (TTS) and Saaras (STT) synthesize and transcribe speech; neither
generates sound effects, and the Model APIs have no standalone mix endpoint.
Sarvam's "adjust the audio mix" is a Content Studio/editor UI step, and the
voice-agent ambient bed is a Samvaad platform feature — neither is a public
SFX or mix API for this engine.

The **Dubbing API** (`POST /jobs`, asynchronous, 12 target languages, voice
cloning, ₹40/min per language on Starter) is a candidate for a future dubbing
provider if that feature is revived; it is a full replace-translate-synthesize
pipeline, not an SFX source. The engine's own `mix_voice_music_sfx` (FFmpeg,
deterministic, QA-measured) remains the mixer.

## Setup

Add your Sarvam API key to the repo-root `.env`:

```bash
SARVAM_API=...
```

> **Env-var spelling:** the provider accepts both `SARVAM_API` and the legacy
> misspelling `SARAVAM_API` (which `config.yaml` and `.env.example` still
> use). If both are set, the name configured as `api_key_env` wins.

The provider reads:

- **Base URL:** `https://api.sarvam.ai` (overridable via
  `ProviderConfig.base_url`).
- **Auth header:** `api-subscription-key: $SARVAM_API` (NOT the more common
  `Authorization: Bearer ...`).
- **Endpoint:** `POST /text-to-speech` with a JSON body.

## Supported languages

The provider accepts both bare language codes (`hi`, `bn`) and BCP-47 codes
(`hi-IN`, `en-IN`). The mapping table normalises both forms to the codes
Sarvam expects:

| BCP-47 code | Language |
|---|---|
| `bn-IN` | Bengali |
| `en-IN` | English (Indian) |
| `gu-IN` | Gujarati |
| `hi-IN` | Hindi |
| `kn-IN` | Kannada |
| `ml-IN` | Malayalam |
| `mr-IN` | Marathi |
| `od-IN` | Odia |
| `pa-IN` | Punjabi |
| `ta-IN` | Tamil |
| `te-IN` | Telugu |

Bare codes (`hi`, `bn`, `gu`, `kn`, `ml`, `mr`, `od`, `pa`, `ta`, `te`,
`en`, `en-in`, `hi-in`) are accepted as shortcuts and mapped to the
corresponding BCP-47 entry. An unknown code falls back to `en-IN`.

## API shape

Request body (JSON):

```json
{
  "text": "नमस्ते, दोस्त",
  "target_language_code": "hi-IN",
  "model": "bulbul:v4-flash",
  "speaker": "shubh"
}
```

Response body (JSON):

```json
{
  "audios": ["<base64-encoded MP3 audio>"],
  "language_code": "hi-IN",
  "model": "bulbul:v4-flash"
}
```

The provider decodes `audios[0]` from base64 and writes the bytes directly
to the requested output path. An empty `audios` array raises
`RuntimeError("Sarvam returned no audio for model=...")`.

## Voice IDs

- **Bulbul v4 Flash / v3 default speaker:** `shubh`
- **Bulbul v2 default speaker:** `anushka`

Voice IDs are passed as `voice_id` in the `AssetRequest.params` and may use
the `sarvam:<speaker>` prefix (e.g. `sarvam:shubh`). The provider strips the
prefix before sending `speaker` to the API; a bare speaker name is also
accepted and defaults to the `sarvam` prefix internally.

## Cost

| Model | Price | Notes |
|---|---|---|
| `bulbul:v4-flash` | ₹20 / 10K chars (~$0.023 / 1K chars) | Primary. Latest, lowest latency. |
| `bulbul:v3` | ₹30 / 10K chars (~$0.034 / 1K chars) | Fallback. Stable API default. |
| `bulbul:v2` | ₹30 / 10K chars (~$0.034 / 1K chars) | Legacy override only. |

Billing is **per call**, rounded to the character: a request is charged
`ceil(chars / 1000)` thousand-character units, so one long request is cheaper
than several short ones at the same total length. Sarvam's public price page
(both `sarvam.ai/pricing` and `docs.sarvam.ai/api/getting-started/pricing`)
listed only `bulbul:v3` at ₹30 / 10K chars when last checked on 2026-10-22;
the v4 Flash rate above is the one our own invoice confirms (657 characters,
one call, $0.023).

For a 10-minute Hindi story with ~15K characters of narration, the Sarvam
v4 Flash cost is roughly **~$0.35** (15K chars × $0.023/1K).

## v4 Flash → v3 fallback

If the primary `bulbul:v4-flash` call returns an `HTTPStatusError` with status
`400` or `503` (Sarvam signals `model_unavailable` this way), the provider
retries the same request with `bulbul:v3` and logs a
`sarvam_tts_falling_back` warning with the primary model, fallback model,
and HTTP status. The `AssetResult.metadata["model"]` reflects whichever
model actually produced the audio, so downstream consumers can tell which
tier was used.

The fallback is skipped when:

- The error status is not `400` or `503` (e.g. `401` auth errors propagate).
- `fallback_model` is unset or equals the primary model (avoids a no-op
  retry).

## Speaking speed and output format (2026-11-11)

- **Pace:** explainer scripts set `voice_pace: 0.5–2.0` in front matter; it is sent as Sarvam's `pace` on both the
  v4 Flash call and the v3 fallback. The india-geography explainer runs at `voice_pace: 1.2` (100 s of narration
  came down to 83 s, inside the 30–90 s gate, and the place names still transcribe correctly).
- **Indian place names:** with an Indian-English voice (`sarvam:sarang_en_narration`, `voice_language: en`) no
  `pronunciations:` respellings were needed for Kutch, Thar or Sundarbans (#346).
- **Format:** Sarvam returns WAV. A `.mp3` (or any non-`.wav`) output path is converted with ffmpeg
  (`sarvam_tts.write_audio`, #321); before that fix the file held WAV bytes under an `.mp3` name.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SARVAM_API is not set in the environment. ...` | Neither `SARVAM_API` nor `SARAVAM_API` is set. | Add `SARVAM_API=...` to the repo-root `.env` and re-run. |
| `Sarvam returned no audio for model='bulbul:v4-flash'` | The API returned a 2xx with an empty `audios` array. | Retry; if it persists, the v4 Flash model may be temporarily unavailable — the next call should hit the v3 fallback path. |
| `httpx.HTTPStatusError` 401 / 403 | Bad or expired API key. | Regenerate the key in the Sarvam dashboard and update `.env`. |
| Hindi text rendered as `?` or boxes in the output MP3 | The TTS audio is correct but the captions renderer lacks a Devanagari-capable font. | Use `scripts/burn_musical_captions.py --font /usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf` or another Devanagari font. |

## Relationship to other docs

- For the storyteller templates that use this provider see
  [`templates.md`](./templates.md) and
  [`Templates_capabilities.md`](./Templates_capabilities.md).
- For the PoYo music provider used alongside Sarvam TTS in the storyteller
  templates see [`POYO_ROUTING.md`](./POYO_ROUTING.md) →
  "Music generation (PoYo path)".
- For the CLI `ve tts-test` command see [`CLI_USAGE.md`](./CLI_USAGE.md).