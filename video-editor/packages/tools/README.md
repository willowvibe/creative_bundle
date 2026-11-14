# Tools

Thin wrappers around external binaries:

- `transcription.py` — resolve config/env ASR settings + `transcribe()` (faster-whisper).
- `gpu.py` — CUDA availability probe + a process-local GPU session lock.
- `ffmpeg_wrapper.py` — Re-export/extension of `video_editor_ffmpeg` for skills.
