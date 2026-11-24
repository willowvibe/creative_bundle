# Documentation index

All of these ship inside the bundle, so they are readable offline.

## Cross-cutting

| Path | What |
|---|---|
| `../README.md` | Bundle overview, setup commands, env-key reference, caveats |
| `../SETUP.md` | New-machine setup: tools, per-repo install, API keys, MCP connections, gotchas |
| `../CLAUDE.md` | Shared guidance: repo layout, sibling links, env rules, browser automation |
| `README.md` (this folder) | Installing skills, memory and docs into Claude |
| `PLUGINS.md` | Plugins the skills assume |

## video-editor

| Path | What |
|---|---|
| `video-editor/README.md` | Architecture, workflows, model routing, quick start |
| `video-editor/docs/ARCHITECTURE.md` | Engine/package architecture |
| `video-editor/docs/CLI_USAGE.md` | `ve` command reference |
| `video-editor/docs/MODEL_ROUTING.md` | Providers, tasks, `api_key_env` values |
| `video-editor/docs/WORKFLOWS.md` | Raw-footage and script-to-video workflows |
| `video-editor/docs/POYO_ROUTING.md` | PoYo routing and music-bed regeneration |
| `video-editor/docs/SARVAM_INTEGRATION.md` | Sarvam TTS/dialogue |
| `video-editor/docs/OPENROUTER_GENERATION.md` | OpenRouter image/video/music |
| `video-editor/docs/CLAUDE_TOOLS.md` | Tooling notes for Claude-driven runs |
| `video-editor/docs/MAP_STORIES.md` | Map-scene recipe details |
| `video-editor/docs/DEVOTIONAL_CHANTS.md` | Chant ×N workflow |
| `video-editor/docs/TECH_TUTORIALS.md` | Coding tutorials: verified demos, real page captures, editor/terminal screen track, talking avatar, SFX, music bed, bumpers, thumbnails, upload gotchas |
| `video-editor/.claude/skills/tech-tutorial-video/SKILL.md` | Skill for those tutorials (Python Decoder channel) |
| `video-editor/.claude/skills/phone-vlog-edit/SKILL.md` | Editing self-recorded phone talking-head footage |
| `video-editor/docs/LIVE_PROVIDER_RUN.md` | Live provider smoke runs |
| `video-editor/docs/design/`, `docs/archive/` | Design notes and historical plans/audits |
| `video-editor/.claude/skills/video-editor/SKILL.md` | Skill entry point |
| `video-editor/.claude/skills/youtube-packaging/SKILL.md` | Title/thumbnail packaging |
| `video-editor/.claude/skills/agent-reach/SKILL.md` | Research helper |

## video-projects

| Path | What |
|---|---|
| `video-projects/README.md` | Content layout and how `ve` reaches it |
| `video-projects/videos/mantra-rahasyam/` | Drama series bible and episodes |
| `video-projects/brand/python-decoder/` | Channel intro/outro generator (`make_bumpers.py`) and upload defaults |
| `video-projects/videos/2026-11-24/python-315-features/` | Python Decoder EP1: template project (capture, screen-track, SFX, music, thumbnail, publish scripts) |
| `video-projects/videos/2026-11-24/talking-avatar-pilot/` | Talking-avatar pilot: sprites generation, bubbles, lip-sync versions |
| `video-projects/docs/superpowers/specs/2026-11-24-pythondecoder-channel-first-3-videos.md` | Python Decoder channel plan |
| Per-project `README.md`, `script.md`, notes | Individual video briefs and prompts |

## vocal-to-song

| Path | What |
|---|---|
| `vocal-to-song/README.md` | Overview and dependencies |
| `vocal-to-song/SKILL.md` | The full workflow (tuning, tanpura, mix/master, chants) |

## social-engine

| Path | What |
|---|---|
| `social-engine/README.md` | Lanes, quick start, owner-safety rules |
| `social-engine/CLAUDE.md` | Hard rules for agents working in the repo |
| `social-engine/docs/usage.md` | Operator usage |
| `social-engine/docs/live-verification.md` | Live Studio findings and selector map |
| `social-engine/docs/specs/2026-11-03-social-engine-youtube-design.md` | Design spec |
| `social-engine/.claude/skills/social-publish/SKILL.md` | Publish skill |
| `social-engine/.plan/youtube.md` | Implementation plan |

## drama-studio

| Path | What |
|---|---|
| `drama-studio/README.md` | Repo map, local layout |
| `drama-studio/docs/DRAMA_PRODUCTION_PLAYBOOK.md` | End-to-end production playbook |
| `drama-studio/docs/DRAMA_PIPELINE_V2_2026-11-01.md` | Current pipeline |
| `drama-studio/docs/DRAMA_STORY_AND_CONTINUITY.md` | Story rules and continuity |
| `drama-studio/docs/DRAMA_FEASIBILITY_2026-10-29.md` | Feasibility study |
| `drama-studio/docs/DRAMA_VIDEO_STACK_RESEARCH_2026-11-01.md` | Video-stack research |
| `drama-studio/.claude/skills/drama-episode/SKILL.md` | Production skill; start here |
| `drama-studio/apps/drama-review/README.md` | Review-board app |
| `drama-studio/apps/drama-review/wrangler.jsonc`, `schema.sql` | Worker config and D1 schema |
