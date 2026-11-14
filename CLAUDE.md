# Shared guidance for this bundle

This folder is a sibling set of five repositories. Open Claude Code at this
directory so this file applies to all of them.

## Repository layout

| Repo | Purpose |
|---|---|
| `video-editor/` | `ve` CLI, HyperFrames/FFmpeg renderer, explainer and shorts pipelines |
| `video-projects/` | Content made with `ve`: `videos/<date>/<slug>/` scripts and notes, `shorts/` packs |
| `vocal-to-song/` | Claude Code skill: vocal → tuned, arranged, mixed song; chant ×N tracks |
| `social-engine/` | Publishing (API + browser lanes) for YouTube et al. |
| `drama-studio/` | Telugu drama pipeline: review gates, review board, dialogue/story checks |

`video-projects`, `drama-studio` and `vocal-to-song` were split out of
`video-editor` on 2026-11-11 with history kept; `video-editor`'s own history was
rewritten the same day.

**Not included in this bundle:** `runpod-comfyui`, `tool-lab` and
`scene3d-engine`. Some docs and scripts still mention them as optional external
tooling (GPU pods, trials, 3D control videos). Cross-repo links use
`../<repo>` because the repos are siblings.

## Sibling links

The bundle restores the original working layout:

```
video-editor/videos  -> ../video-projects/videos
video-editor/shorts  -> ../video-projects/shorts
drama-studio/videos  -> ../video-projects/videos
```

Keep the five folders together; moving one out on its own breaks these links.

## Environment

- `video-editor` loads `video-editor/.env` with `dotenv` (`override=False`). Only
  `OPENROUTER_API_KEY` is needed for the default `config.yaml` routing; every
  other provider key is optional and documented in `.env.example`.
- `social-engine` does **not** read `.env`. OAuth client secrets, tokens and
  Chrome profiles live under `~/.config/social-engine/` (override with
  `SOCIAL_ENGINE_HOME`).
- `drama-studio` reads the environment first, then `drama-studio/.env`. In the
  original setup that file was a symlink to `../video-editor/.env`.
- `vocal-to-song` and `video-projects` need no keys.
- Never commit `.env`; share `.env.example` only.

## Claude context shipped with the bundle

- `claude/skills/` — user-level skills to install into `~/.claude/skills/`
  (`claude/README.md` has the one-liner).
- `claude/memory/` — archived session memory for repos not in the bundle; the
  `video-editor` memory is already in `video-editor/.remember/`, where the
  `remember` plugin loads it automatically.
- `claude/docs/INDEX.md` — map of every documentation folder in the bundle.
- `claude/PLUGINS.md` — plugins the skills assume, and how to install them.
- Each repo also carries its own `.claude/skills/` (for example
  `video-editor/.claude/skills/video-editor/`,
  `social-engine/.claude/skills/social-publish/`,
  `drama-studio/.claude/skills/drama-episode/`).

## Conventions

- Renders, caches and generated media stay untracked. `video-projects` keeps
  scripts, prompts and notes; the engine writes outputs elsewhere.
- `social-engine` never makes anything visible (upload, publish, privacy change)
  without an explicit `--yes` / `confirm`. Keep that guard in place.
- `config.yaml` is the live routing config; `config.example.yaml` is the
  annotated template.

## Browser automation

If `agent-browser` is installed, prefer it for browser work (screenshots,
clicking, form fill, scraping, app testing) over Playwright or chrome-devtools
MCP: it is cheaper on tokens via accessibility-tree snapshots with `@eN` refs.

- Load the command reference before first use: `agent-browser skills get core --full`.
- Core loop: `agent-browser open <url>` → `agent-browser snapshot -i` → act on
  `@eN` refs → re-snapshot after any page change (refs go stale).
