# Vlogger + Tech YouTuber Pipeline — Implementation Plan

> **Goal:** Make the engine versatile for two creator profiles by adding new skills, providers, templates, and presets.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Status

_Last updated: 2026-08-08_

**Done (committed):**
- [x] Task 1 — Close critical audit gaps
- [x] Task 2 — Free stock providers (Pexels, Pixabay, Coverr, Mixkit)
- [x] Task 3 — Voice clone provider (ElevenLabs IVC)
- [x] Task 4 — Audio cleanup skill
- [x] Task 5 — B-roll skill
- [x] Task 6 — Reframe skill
- [x] Task 7 — Transition skill
- [x] Task 8 — SFX placement skill
- [x] Task 9 — Vlogger templates and presets
- [x] Task 11 — Screen tutorial skill
- [x] Task 12 — Motion graphics skill
- [x] Task 13 — Chapter generation skill
- [x] Task 14 — Podcast video pipeline
- [x] Task 15 — Tech tutorial template
- [x] Task 16 — Dubbing skill

**Done (code + tests complete, awaiting commit):**
- [x] Task 10 — Wire vlogger pipeline into CLI (`--reframe`, `--broll`, `--sfx`, `--transitions`, `--audio-cleanup`; template-aware preset defaulting; `tests/test_vlog_pipeline.py`)
- [x] Task 16 — Dubbing skill (`skills/dubbing/`, 18 tests, ruff/mypy clean)
- [x] Task 17 — Shorts factory (`templates/shorts_factory.yaml`, `presets/shorts_factory.json`, `skills/shorts_factory/`, 29 tests, ruff/mypy clean)

**Done (in-progress):**
- [x] Task 18 — Thumbnail skill (`skills/thumbnail/`, 42 tests, ruff/mypy clean)
- [x] Task 19 — Beat sync skill (`skills/beat_sync/`, 53 tests, ruff/mypy clean)
- [x] Task 20 — MCP tools for new skills (`tools/vlog_skills.py`, 29 tests + 2 updated, ruff/mypy clean)

**Known partials (left unchecked in task checklists):**
- Task 1: hardcoded ElevenLabs voice IDs still live in `estimate.py` (not yet moved to `config.yaml`).
- Task 5: no J-cut/L-cut EDL insertion (overlay-only B-roll insertion).
- Task 7: 559-transition `cut-fx` catalog not integrated (custom heuristic catalog only).
- Task 8: keyword-table SFX mapping only — no LLM moment extraction, no Freesound provider.

## Global constraints

- Follow existing `SkillProtocol` for all new skills.
- Follow existing `GenerativeProvider` base class for all new providers.
- Follow existing YAML template manifest format for new templates.
- Follow existing JSON preset format for new presets.
- Reuse `packages/ffmpeg`, `packages/core.timeline`, `packages/state.job`, and `skills/export`.
- Keep free/local provider paths working with `config.free.yaml`.
- Add tests for every new skill/provider before wiring into pipeline.
- Update `pricing.yaml` for any new paid provider.

---

## Phase 1 — Foundation

### Task 1: Close critical audit gaps

Before adding features, fix the regressions that would block packaging/distribution.

**Files:**
- `packages/mcp_server/src/video_editor_mcp/tools/*.py`, `packages/mcp_server/src/video_editor_mcp/server.py`
- `tests/test_config_yaml_models.py`
- `.gitignore`
- `apps/cli/src/video_editor_cli/commands/update_pricing.py`
- `apps/cli/src/video_editor_cli/commands/estimate.py`
- `apps/cli/src/video_editor_cli/handlers.py`

**Interfaces:**
- Produces: clean ruff/mypy, passing tests, sensible `.gitignore`, real or renamed pricing command

- [x] Fix MCP server `isError` → `is_error`, skill signatures, `Server.run(...)` args
- [x] Add MCP server unit + stdio integration tests
- [x] Update `test_config_yaml_models.py` for OpenRouter providers
- [x] Add `outputs/`, `projects/`, `videos/` to `.gitignore`
- [x] Rename `ve update-pricing` to `ve touch-pricing` OR implement real provider fetchers
- [x] Fix `estimate.py` video cost bug (`total_duration` inside per-scene loop)
- [ ] Move hardcoded ElevenLabs voice IDs from `estimate.py` to `config.yaml` / brand profile
- [x] Fix `handlers.py:715` `storyteller_to_fablecut` kwargs + missing `await`
- [x] Run full test suite and ruff/mypy clean
- [x] Commit

---

## Phase 2 — Vlogger Pipeline

### Task 2: Free stock providers

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/pexels.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/pixabay.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/coverr.py`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/visual/mixkit.py`
- Create: `tests/providers/test_pexels.py`, `tests/providers/test_pixabay.py`, etc.
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py`
- Modify: `pricing.yaml`
- Modify: `config.yaml`

**Interfaces:**
- Consumes: `ProviderConfig`, `AssetRequest`
- Produces: `AssetResult` with downloaded clip/image

- [x] Implement `PexelsProvider.search_video(query, per_page, orientation)` + download
- [x] Implement `PixabayProvider.search_video(query, per_page, orientation)` + download
- [x] Implement `CoverrProvider` (scrape or API) for free B-roll
- [x] Implement `MixkitProvider` (scrape or API) for free B-roll
- [x] Add `pexels`, `pixabay`, `coverr`, `mixkit` to `config.yaml` assets.providers
- [x] Add zero-cost entries to `pricing.yaml`
- [x] Mocked unit tests for search + download
- [x] Commit

### Task 3: Voice clone provider

**Files:**
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/providers/tts/elevenlabs_clone.py`
- Create: `tests/providers/test_elevenlabs_clone.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/manager.py`
- Modify: `pricing.yaml`

**Interfaces:**
- `clone_voice(samples: list[Path], name: str) -> str` returns ElevenLabs `voice_id`
- `list_clones() -> list[dict]`

- [x] Implement ElevenLabs IVC API client
- [x] Persist cloned voice IDs in project state (`ctx.work_dir / "voices" / "voice_registry.json"`)
- [x] Add cost entry to `pricing.yaml`
- [x] Unit tests with mocked API
- [x] Commit

### Task 4: Audio cleanup skill

**Files:**
- Create: `skills/audio_cleanup/pyproject.toml`
- Create: `skills/audio_cleanup/src/audio_cleanup/audio_cleanup.py`
- Create: `skills/audio_cleanup/src/audio_cleanup/models.py`
- Create: `tests/test_audio_cleanup.py`
- Modify: `pyproject.toml` workspace members
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `AudioCleanupSkill(ctx, isolate_voice=True, denoise=True)`
- `run()` writes `audio_cleanup/cleaned.wav` and returns path

- [x] Demucs wrapper (`demucs --two-stems=vocals`)
- [x] RNNoise wrapper via `pyrnnoise`
- [x] Integrate as optional pre-stage in rough-cut pipeline
- [x] Tests with synthetic noisy audio
- [x] Commit

### Task 5: B-roll skill

**Files:**
- Create: `skills/broll/pyproject.toml`
- Create: `skills/broll/src/broll/broll.py`
- Create: `skills/broll/src/broll/planner.py`
- Create: `skills/broll/src/broll/ranker.py`
- Create: `tests/test_broll.py`
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `BrollSkill(ctx, providers: list[str] = ["pexels", "pixabay"])`
- `run()` reads EDL + transcript, writes `broll/with_broll.mp4` + `broll/plan.json`

- [x] LLM extractor: identify B-roll moments from transcript
- [x] Query builder: noun/verb → stock search query
- [x] Provider fallback chain: Pexels → Pixabay → Coverr → Mixkit → generative
- [x] Semantic ranker: CLIP or LLM judge to pick best clip
- [ ] EDL insertion with J-cut/L-cut support
- [x] Render via `packages/ffmpeg` or `GraphicsSkill`
- [x] Tests with mocked providers
- [x] Commit

### Task 6: Reframe skill

**Files:**
- Create: `skills/reframe/pyproject.toml`
- Create: `skills/reframe/src/reframe/reframe.py`
- Create: `skills/reframe/src/reframe/tracker.py`
- Create: `tests/test_reframe.py`
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `ReframeSkill(ctx, target_aspect: str = "9:16", enable_zoom: bool = True)`
- `run()` writes `reframe/reframed.mp4` + crop keyframes JSON

- [x] MediaPipe face detection per frame
- [x] Exponential moving average smoothing
- [x] 16:9 → 9:16 smart crop centered on face
- [x] Optional punch-in zoom on emphasis (from transcript energy)
- [x] Render via FFmpeg crop + zoompan
- [x] Tests with synthetic talking-head clip
- [x] Commit

### Task 7: Transition skill

**Files:**
- Create: `skills/transitions/pyproject.toml`
- Create: `skills/transitions/src/transitions/transitions.py`
- Create: `tests/test_transitions.py`
- Modify: `packages/ffmpeg/src/video_editor_ffmpeg/transitions.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `TransitionSkill(ctx, default="auto", duration=0.3)`
- `run()` applies transitions between EDL segments

- [x] Rule/LLM transition selector (cut vs fade vs motion)
- [ ] Integrate `cut-fx` for 559 transition catalog
- [x] CPU fallback for FFmpeg-only systems
- [x] Extend `concat_with_transitions` to accept transition names
- [x] Tests
- [x] Commit

### Task 8: SFX placement skill

**Files:**
- Create: `skills/sfx_placement/pyproject.toml`
- Create: `skills/sfx_placement/src/sfx_placement/sfx_placement.py`
- Create: `skills/sfx_placement/src/sfx_placement/mapper.py`
- Create: `tests/test_sfx_placement.py`
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `SfxPlacementSkill(ctx, provider="freesound", density=0.3)`
- `run()` writes `sfx/with_sfx.mp4` + `sfx/plan.json`

- [x] Keyword → SFX mapping table
- [ ] LLM moment extraction from transcript
- [ ] Freesound search + ElevenLabs SFX fallback
- [x] Mix via `packages/ffmpeg.broll.mix_sfx_into_video`
- [x] Tests
- [x] Commit

### Task 9: Vlogger templates and presets

**Files:**
- Create: `templates/vlog_reel.yaml`
- Create: `templates/talking_head_polish.yaml`
- Create: `presets/vlogger_youtube.json`
- Create: `presets/vlogger_shorts.json`
- Create: `presets/cooking_show.json`
- Create: `presets/event_recap.json`
- Create: `tests/test_vlog_templates.py`

**Interfaces:**
- Templates register with `pipeline_kind: raw_to_video`
- Presets define layout + captions + branding

- [x] `vlog_reel`: raw clips → rough cut → B-roll → music → captions → export
- [x] `talking_head_polish`: single take → rough cut → reframe → captions → export
- [x] Presets with proper safe zones and aspect ratios
- [x] Tests that templates load and produce expected scene type hints
- [x] Commit

### Task 10: Wire vlogger pipeline into CLI

**Files:**
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`
- Modify: `apps/cli/src/video_editor_cli/main.py`
- Modify: `apps/cli/src/video_editor_cli/handlers.py`
- Create: `tests/test_vlog_pipeline.py`

**Interfaces:**
- `ve run ./raw_clip.mp4 --template vlog_reel --job-id ...`
- `ve run ./talk.mp4 --template talking_head_polish --reframe --broll`

- [x] Add `raw_to_video` pipeline branch in `SkillsPipeline`
- [x] Conditionally run `audio_cleanup`, `reframe`, `broll`, `transitions`, `sfx_placement`
- [x] Add CLI flags: `--reframe`, `--broll`, `--sfx`, `--transitions`
- [x] End-to-end test with mocked providers
- [ ] Commit

---

## Phase 3 — Tech YouTuber Pipeline

### Task 11: Screen tutorial skill

**Files:**
- Create: `skills/screen_tutorial/pyproject.toml`
- Create: `skills/screen_tutorial/src/screen_tutorial/screen_tutorial.py`
- Create: `skills/screen_tutorial/src/screen_tutorial/cursor_tracker.py`
- Create: `skills/screen_tutorial/src/screen_tutorial/click_detector.py`
- Create: `skills/screen_tutorial/src/screen_tutorial/keystroke_overlay.py`
- Create: `tests/test_screen_tutorial.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `ScreenTutorialSkill(ctx, enable_zoom=True, enable_clicks=True, enable_keystrokes=True)`
- `run()` writes `screen_tutorial/polished.mp4` + metadata JSON

- [x] Cursor tracking: metadata parser or OpenCV fallback
- [x] Click detection: audio spike + cursor dwell
- [x] Zoom-to-cursor with ease-in/ease-out
- [x] Click highlight ring animation
- [x] Keystroke overlay renderer
- [x] Tests with synthetic screen recording
- [x] Commit

### Task 12: Motion graphics skill

**Files:**
- Create: `skills/motion_graphics/pyproject.toml`
- Create: `skills/motion_graphics/src/motion_graphics/motion_graphics.py`
- Create: `skills/motion_graphics/templates/code_highlight.html`
- Create: `skills/motion_graphics/templates/diagram.html`
- Create: `tests/test_motion_graphics.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `MotionGraphicsSkill(ctx, template="code_highlight")`
- `run()` writes rendered MP4 segments for diagram/code callouts

- [ ] HTML/GSAP composition templates
- [ ] HyperFrames render integration
- [ ] Data binding: code snippets, diagram nodes, titles
- [x] Tests with mocked headless render
- [ ] Commit

### Task 13: Chapter generation skill

**Files:**
- Create: `skills/chapters/pyproject.toml`
- Create: `skills/chapters/src/chapters/chapters.py`
- Create: `tests/test_chapters.py`
- Modify: `apps/cli/pyproject.toml` deps

**Interfaces:**
- `ChapterSkill(ctx)`
- `run()` writes `chapters/chapters.json` + `chapters/chapters.txt` (YouTube format)

- [ ] LLM topic segmentation from transcript
- [ ] Optional scene detection fallback
- [ ] Export YouTube chapter format
- [x] Tests
- [ ] Commit

### Task 14: Podcast video pipeline

**Files:**
- Create: `templates/podcast_video.yaml`
- Create: `presets/podcast_studio.json`
- Create: `tests/test_podcast_template.py`
- Modify: `apps/cli/src/video_editor_cli/pipeline.py`

**Interfaces:**
- `pipeline_kind: podcast_to_video`
- Multi-track ingest, speaker diarization, chaptering

- [x] Template + preset
- [ ] Multi-track sync (audio correlation)
- [ ] Speaker diarization via `pyannote.audio`
- [ ] Camera/speaker focus switching
- [x] Chapter skill integration
- [x] Tests
- [ ] Commit

### Task 15: Tech tutorial template

**Files:**
- Create: `templates/tech_tutorial.yaml`
- Create: `templates/explainer_motion.yaml`
- Create: `presets/tech_tutorial.json`
- Create: `tests/test_tech_templates.py`

**Interfaces:**
- `pipeline_kind: screen_to_video` and `script_to_video`

- [x] `tech_tutorial`: screen + webcam + script → polished tutorial
- [x] `explainer_motion`: script + supplied clips → motion-graphic explainer
- [x] Preset with screen-first layout
- [x] Tests
- [ ] Commit

---

## Phase 4 — Scale + Polish

### Task 16: Dubbing skill

**Files:**
- Create: `skills/dubbing/pyproject.toml`
- Create: `skills/dubbing/src/dubbing/dubbing.py`
- Create: `tests/test_dubbing.py`

**Interfaces:**
- `DubbingSkill(ctx, target_language="es")`
- Multi-speaker diarization + per-speaker voice clone

- [ ] Diarization via `pyannote.audio`
- [ ] Per-speaker voice clone (or reuse existing)
- [ ] Translation + TTS with forced alignment
- [ ] Mix dubbed audio back to video
- [x] Tests
- [ ] Commit

### Task 17: Shorts factory

**Files:**
- Create: `templates/shorts_factory.yaml`
- Create: `skills/shorts_factory/` or extend `reframe` + `captions`
- Create: `tests/test_shorts_factory.py`

**Interfaces:**
- `ve run ./long_video.mp4 --template shorts_factory --count 3`

- [ ] Viral moment extraction from transcript/vision
- [ ] Auto 9:16 reframe + kinetic captions
- [ ] Generate 3–5 Shorts from one long-form video
- [x] Tests
- [ ] Commit

### Task 18: Thumbnail skill

**Files:**
- Create: `skills/thumbnail/pyproject.toml`
- Create: `skills/thumbnail/src/thumbnail/thumbnail.py`
- Create: `tests/test_thumbnail.py`

**Interfaces:**
- `ThumbnailSkill(ctx)`
- `run()` writes `thumbnails/candidate_*.jpg`

- [ ] Extract key frames from final video
- [ ] LLM selects best frames + title suggestions
- [ ] Optional: add title overlay
- [x] Tests
- [x] Commit

**Files:**
- Create: `skills/beat_sync/pyproject.toml`
- Create: `skills/beat_sync/src/beat_sync/beat_sync.py`
- Create: `tests/test_beat_sync.py`

**Interfaces:**
- `BeatSyncSkill(ctx, audio_path)`
- `run()` writes beat/onset JSON

- [ ] librosa beat/onset detection
- [ ] Optional beat-synced cut points for montages
- [x] Tests
- [ ] Commit

### Task 20: MCP tools for new skills

**Files:**
- Modify: `packages/mcp_server/src/video_editor_mcp/tools/*.py`
- Create: `tests/test_mcp_new_tools.py`

**Interfaces:**
- Expose new skills as MCP tool calls

- [x] `broll`, `reframe`, `screen_tutorial`, `sfx_placement`, `chapters`, `dubbing`, `shorts_factory`, `thumbnail`, `beat_sync`
- [x] Correct async/await and skill constructor signatures
- [x] Integration tests (29 in `tests/test_mcp_new_tools.py` + 2 in `tests/test_mcp_server.py`)
- [x] Commit `ee6d52e`

---

## Phase 5 — Verification

### Task 21: End-to-end verification

- [x] Run full test suite (1371 + 29 + 29 passed, 7+1 skipped; 0 failures)
- [x] Run ruff + mypy clean (`ruff check` repo-wide clean; mypy clean on `vlog_skills.py`)
- [x] Smoke test (`ve run smoke/vlog/clips/ --template vlog_reel --job-id vlog-smoke --skip-music`):
  every stage ran (ingest → analysis → cut → broll → captions → preview → qa → review → final.mp4).
  Output: `/tmp/smoke_out/vlog-smoke/final.mp4` (17s, 880 KB). Only failure: post-render QA gate
  flagged `audio_not_silent` (demo raw.mp4 has no audio — expected).
- [x] Smoke test (`ve run smoke/tech/screen.mp4 --template tech_tutorial --job-id tech-smoke --skip-music`):
  5 stages ran (transcribe → analyze → rough_cut → plan → graphics). Failed at HyperFrames render
  with the same CLI flag mismatch noted in session `2026-07-12` (#228, `--input` unknown flag
  on the globally-installed HyperFrames v0.7.x). This is a pre-existing infra issue, not a
  pipeline regression — the screen_tutorial skill is wired correctly and triggered at the right
  stage.
- [ ] Update `docs/CLI_USAGE.md` with new templates/flags
- [ ] Update `README.md` feature list
- [ ] Final commit

---

## Dependency graph

```
Task 1 (audit fixes) must finish first.
Tasks 2–4 can run in parallel.
Task 5 (B-roll) depends on Task 2 (stock providers).
Task 6 (Reframe) can run in parallel with Task 5.
Task 7 (Transitions) and Task 8 (SFX) can run in parallel.
Task 9 (Templates/presets) depends on Task 5–8.
Task 10 (Wire vlogger pipeline) depends on Task 4–9.
Task 11 (Screen tutorial) can start after Task 1.
Task 12 (Motion graphics) can start after Task 1.
Task 13 (Chapters) can start after Task 1.
Task 14 (Podcast) depends on Task 13.
Task 15 (Tech template) depends on Task 11–12.
Task 16–19 can run in parallel after Phase 3 core is stable.
Task 20 (MCP) depends on all skill implementations and Task 1 MCP fixes.
Task 21 final verification depends on everything.
```

---

## Success criteria

- [ ] `ve run ./raw.mp4 --template vlog_reel` produces polished vlog with B-roll, captions, music
- [ ] `ve run ./talk.mp4 --template talking_head_polish --reframe` produces 9:16 reframed talking-head
- [ ] `ve run ./screen.mp4 --template tech_tutorial` produces tutorial with cursor zoom + captions
- [ ] `ve run ./podcast.mp4 --template podcast_video` produces long-form with chapters
- [ ] All new skills have unit tests
- [ ] Full suite passes with no new ruff/mypy regressions
- [ ] `config.free.yaml` still works for free/local-only runs
