# video-editor-config

Unified project-level configuration loader for the video editor engine.

Loads `config.yaml` and exposes typed sections for `audio`, `render`, `paths`,
and `transcription`, while delegating `llm` and `assets` sections to their
existing dedicated loaders in `video-editor-llm` and
`video-editor-asset-ingestion`.
