# creative_bundle

A Claude-ready bundle of five creative repositories — video creation and AI
editing — from a local creative-video stack, snapshotted 2026-11-14. The five
folders sit side by side, exactly as they are meant to run. This combined
repository carries each repo's working tree, not the original per-repo git
histories.

**Setting up a new machine?** See [`SETUP.md`](SETUP.md) for the full install
order, per-repo commands, API keys, MCP connections and gotchas.

## What is here

| Repo | What it does | Stack |
|---|---|---|
| `video-editor/` | `ve` CLI: transcript-first video editing, script-to-video generation, HyperFrames/FFmpeg rendering | Python (uv), Node/pnpm |
| `video-projects/` | Content made with `ve`: per-video scripts, prompts, notes, shorts packs | Markdown/JSON content |
| `vocal-to-song/` | Claude Code skill: raw vocal → tuned, arranged, mixed song; chant ×N tracks | Python scripts (librosa, pyworld, …) |
| `social-engine/` | Publishing and channel management (YouTube first): API lane + browser lane | Python (uv), Node (Playwright) |
| `drama-studio/` | Telugu drama pipeline: review gates, dialogue/story checks, review board | Python (uv), Cloudflare Worker |

## What is not here

- **`.env` files.** Never shared. Every repo has a `.env.example` listing only
  the keys that repo actually reads.
- **Local/generated assets.** `assets/`, `outputs/`, `projects/`, `renders/`,
  intermediate frames, music beds and other untracked media stay on the
  original machine. `.venv/`, `node_modules/`, and caches likewise — recreate
  them with the setup commands below.
- **Machine-specific agent state.** `video-editor/.superpowers/` and the local
  `.mcp.json` (absolute `/mnt/...` paths) were removed. The session memory in
  `video-editor/.remember/` is kept (see *Memory*), tracked `.claude/skills/`
  are kept, and a portable `.mcp.json.example` replaces the local MCP config.
- **`tool-lab` symlinks.** `.claude/skills/tool-trial` pointed outside the
  bundle and was removed.
- **Account/machine settings.** No `settings.json`, `settings.local.json`,
  `.credentials.json` or plugin installs are shared; the Claude-facing assets
  live in `claude/` instead.

## Layout

The five repos are siblings. `video-editor/videos` and `video-editor/shorts`
are symlinks into `video-projects/`, and `drama-studio/videos` points at the
same content, so commands that expect a checkout-local `videos/` directory
still work:

```
creative_bundle/
├── CLAUDE.md                   # shared guidance for Claude, applies to all five
├── README.md
├── claude/                     # user-level skills, memory archive, docs index
│   ├── skills/                 # install into ~/.claude/skills/
│   ├── memory/                 # archived memory for repos not in the bundle
│   ├── docs/INDEX.md
│   └── PLUGINS.md
├── video-editor/
│   ├── videos -> ../video-projects/videos
│   └── shorts -> ../video-projects/shorts
├── video-projects/
├── vocal-to-song/
├── social-engine/
└── drama-studio/
    └── videos -> ../video-projects/videos
```

## Setup

**video-editor**

```bash
cd video-editor
uv sync
pnpm install
bash scripts/install_ffmpeg.sh      # FFmpeg 8.1+ into tools/ffmpeg
cp .env.example .env                # then fill in OPENROUTER_API_KEY
uv run ve --help
```

**video-projects** — nothing to install; it is content. Renders run through
`../video-editor`.

**vocal-to-song**

```bash
cd vocal-to-song
uv venv -q -p 3.11 .venv
uv pip install -q -p .venv librosa soundfile pyworld pyloudnorm noisereduce scipy numpy "setuptools<70"
```

See `SKILL.md` for the full workflow (and the optional DDSP flute setup).

**social-engine**

```bash
cd social-engine
uv sync
npm install                         # browser lane
# Secrets live OUTSIDE the repo: ~/.config/social-engine/
#   youtube/client_secret.json, tokens/, chrome profiles
```

**drama-studio**

```bash
cd drama-studio
uv sync
```

## Claude setup

The bundle carries the context Claude needs, but nothing is installed
automatically:

- **Instructions.** `CLAUDE.md` at the bundle root is read when Claude Code is
  opened here. It covers the sibling layout, env rules and browser automation.
- **Skills.** `claude/skills/` has 48 user-level skills. Link them once:

  ```bash
  cd /path/to/creative_bundle
  mkdir -p ~/.claude/skills
  for d in claude/skills/*/; do
    ln -sfn "$PWD/$d" ~/.claude/skills/"$(basename "$d")"
  done
  ```

  Repo-local skills (`.claude/skills/`) already load on their own.
- **Memory.** `video-editor/.remember/` holds the 41 restored session notes, so
  the `remember` plugin loads them when that repo is opened. `claude/memory/`
  archives the notes from `tool-lab` and the parent workspace.
- **Docs.** `claude/docs/INDEX.md` maps every documentation folder in the
  bundle.
- **Plugins.** `claude/PLUGINS.md` lists the plugins the skills assume
  (`remember`, `superpowers`, `hyperframes`, `firecrawl`, …).

## Environment files

Only the keys a repo reads are listed; everything else is commented out as
optional.

| File | Required | Optional / notes |
|---|---|---|
| `video-editor/.env` | `OPENROUTER_API_KEY` (default `config.yaml` routing) | `OLLAMA_CLOUD_API_KEY` (`config.free.yaml`), `TYPESAFE_API_KEY`, and per-workflow asset providers (`ELEVENLABS_API_KEY`, `SARVAM_API`, `POYO_API_KEY`, `FAL_KEY`, `OPENAI_API_KEY`, `REPLICATE_API_TOKEN`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `FREESOUND_API_KEY`). `VIDEO_EDITOR_DATA_DIR` keeps run data off the checkout. |
| `drama-studio/.env` | `POYO_API_KEY`, `SARVAM_API` (dialogue check) | `DRAMA_REVIEW_URL`, `DRAMA_REVIEW_KEY` for the review board; `RPC_REPO` for upscale scripts. Originally a symlink to `../video-editor/.env`. |
| `social-engine/.env` | — | The code does **not** read `.env`; OAuth secrets live under `~/.config/social-engine/`. The file only documents runtime overrides. |
| `vocal-to-song/.env` | — | No keys; arguments only. |
| `video-projects/.env` | — | Content repo; `ve` reads `video-editor/.env`. |

## Caveats

- Some docs and configs still contain absolute paths from the original machine
  (for example `/mnt/data2/...`). Rewrite them for your layout.
- A few tracked symlinks under `video-projects/videos/...` point at untracked
  asset folders and will dangle until you regenerate those assets.
- `video-editor/config.yaml` is the owner's live routing config.
  `config.example.yaml` is the annotated template to start from.
- `social-engine` never makes anything visible (upload, publish, privacy
  change) without an explicit `--yes` / `confirm` — keep it that way.
