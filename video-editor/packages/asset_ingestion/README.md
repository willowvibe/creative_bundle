# Asset Ingestion Pipeline

Generative asset ingestion for the video editor engine.

Provides async connectors for cloud media providers (OpenAI TTS, ElevenLabs,
Replicate) and a local `matplotlib` data visualization renderer. All generated
assets are SHA-256 cached under `projects/{job_id}/.cache/assets/`.
