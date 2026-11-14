---
name: poyo-music-and-sarvam-tts-providers-2026-07-17
description: "New PoyoMusicProvider and SarvamTTSProvider wrappers + TTSRouter for Indian-language routing; cuts music cost ~6.4× and Indian-language TTS cost ~2× vs ElevenLabs."
metadata:
  type: project
---

Two new audio provider wrappers plus a routing layer for the storyteller templates.

- `PoyoMusicProvider` (`packages/asset_ingestion/.../audio/poyo_music.py`): submits to PoYo `/api/generate/submit` + polls `/api/generate/status/{task_id}`. Default model `minimax-music-2-6` (MiniMax via Runware, $0.10/gen = 20 credits). Fallback model `generate-music` (Suno-backed, also $0.10/gen).
- `SarvamTTSProvider` (`packages/asset_ingestion/.../tts/sarvam_tts.py`): calls `POST https://api.sarvam.ai/text-to-speech` with header `api-subscription-key: $SARAVAM_API`. Decodes base64 `audios[0]`. Default model `bulbul:v3` (₹30/10K chars, ~$0.036/1K chars), falls back to `bulbul:v2` (₹15/10K chars). Supported langs: bn-IN, en-IN, gu-IN, hi-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN.
- `TTSRouter` (`packages/asset_ingestion/.../tts/tts_router.py`): routes by voice prefix (`sarvam:` / `elevenlabs:`) first, then by language — Indian-language scenes go to Sarvam, everything else to ElevenLabs.

Cost impact (10-min story, 15K chars narration):
- Music: ElevenLabs $6.40 → PoYo `minimax-music-2-6` $0.10
- Hindi TTS: ElevenLabs $1.20 → Sarvam v3 $0.54
- Total saving per story: ~$6.96 (~91%)

Related: [[storyteller-templates-2026-07-17]], [[poyo-ai-primary-provider]], [[poyo-model-pricing-reference-2026-07-13]].