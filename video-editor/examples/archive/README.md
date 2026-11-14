# Archived render scripts

These are historical, unmaintained render paths kept for reference only.

- `projects/` contains one-off Saju, Shiva and anime-tram composers and their
  pipeline runners. They predate the current `SkillsPipeline` and duplicate
  probing/FFmpeg logic.
- `render_musical_plan.py` renders a FableCut scene plan directly with FFmpeg.
  The supported paths are the render graph (`video_editor_core.compile_ffmpeg`)
  and the Composition IR (`video_editor_core.compile_composition`).

Nothing here is imported by the packages, CLI, MCP server or tests. New work
belongs in `packages/pipelines` and the Composition IR; see A3 in
`docs/ISSUES_BUGS.md`.
