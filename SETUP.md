# Setup on a new machine

Everything needed to get these five repos running, in the order that avoids
dead ends. Sections 1–3 are required; section 4 depends on which workflows you
run; the rest is optional or per-workflow.

- **Required overall:** git, Python 3.12+, uv, FFmpeg, Chrome.
- **Required per workflow:** Node/pnpm (`video-editor`), Python 3.11
  (`vocal-to-song`), a Google Cloud OAuth client (`social-engine`), API keys.
- **Optional:** Cloudflare review board, Modal, Flow MCP, RunPod, Xvfb.

Replace `/path/to/creative_bundle` with wherever you unpacked the bundle. Keep the
five repo folders as siblings — the `videos`/`shorts` symlinks depend on it.

---

## 1. Install order

```text
git
uv  ──────────────►  Python 3.12 (uv can install it)
Python 3.11       ─►  vocal-to-song venv
Node 22 + pnpm    ─►  video-editor (HyperFrames), social-engine (Playwright)
FFmpeg 8.1+       ─►  video-editor, vocal-to-song, drama-studio, Flow MCP
Chrome/Chromium   ─►  video-editor renders, social-engine browser lane
repo setup        ─►  uv sync / pnpm install / npm install
.env keys         ─►  video-editor/.env, drama-studio/.env
MCP configs       ─►  per-repo .mcp.json (optional but useful)
```

Run `video-editor/scripts/install_ffmpeg.sh` **before**
`video-editor/scripts/bootstrap.sh`; the bootstrap checks for `ffmpeg` and
exits if it is missing.

---

## 2. System tools

### Required

| Tool | Version | Needed by | Install |
|---|---|---|---|
| **git** | any recent | all repos | `sudo apt install git` / `brew install git` |
| **Python** | **3.12+** | `video-editor`, `social-engine`, `drama-studio` (the first two pin `3.12` in `.python-version`; all require `>=3.12`) | `uv python install 3.12` or your distro package |
| **uv** | recent | all Python repos | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js** | **22+** | `video-editor` (HyperFrames 0.8.208), `social-engine` (Playwright driver) | `nvm install 22` or NodeSource |
| **pnpm** | 9/10 | `video-editor` workspace | `corepack enable && corepack prepare pnpm@latest --activate`, or `npm i -g pnpm` |
| **npm** | ships with Node | `social-engine` (`package-lock.json`), global CLIs | comes with Node |
| **FFmpeg + ffprobe** | **8.1+** (6.1 runs most paths) | `video-editor`, `vocal-to-song`, drama audio slicing, Flow MCP assembly | `cd video-editor && bash scripts/install_ffmpeg.sh` (static BtbN build; symlinks into `~/.local/bin`) |
| **Google Chrome** or Chromium | stable | HyperFrames rendering; `social-engine` browser lane | `sudo apt install google-chrome-stable` (Google's deb repo) or `chromium` |

Make sure `~/.local/bin` is on `PATH` after `install_ffmpeg.sh`:

```bash
export PATH="$HOME/.local/bin:$PATH"
ffmpeg -version | head -1     # expect 8.1 or newer
ffprobe -version | head -1
```

### Required only for `vocal-to-song`

| Tool | Why | Install |
|---|---|---|
| **Python 3.11** | the main venv; some audio wheels lag 3.12 | `uv python install 3.11` |
| **C/C++ toolchain** | `pyworld` may build from source | `sudo apt install build-essential` |
| **libsndfile** | `soundfile` runtime library | `sudo apt install libsndfile1` |

### Optional

| Tool | Why | Install |
|---|---|---|
| **Xvfb** | `social-engine` runs Chrome hidden on a virtual `:99` display on Linux | `sudo apt install xvfb` |
| **Cloudflare `wrangler`** | deploy the drama review board | `npm i -g wrangler` then `wrangler login` |
| **Modal CLI** | MiniMax H3 lip-sync (`v2_h3_lipsync.py`) | `uv tool install modal` then `modal token new` |
| **Rhubarb Lip Sync 1.14** | mouth cues for `ve talking-avatar` (cartoon avatar over screen recordings) | release binary from github.com/DanielSWolf/rhubarb-lip-sync on `PATH`; without it the engine falls back to loudness timing |
| **`.venv-lipsync`** (CPU torch + transformers + g2p_en, ~1 GB) | default `--lipsync hybrid`: lip closures on wav2vec2-measured M/B/P letters | in `video-editor/`: `uv venv .venv-lipsync && uv pip install --python .venv-lipsync/bin/python torch --index-url https://download.pytorch.org/whl/cpu && uv pip install --python .venv-lipsync/bin/python transformers g2p_en numpy`; without it hybrid falls back to Rhubarb |
| **`agent-browser`** | browser automation preferred by `CLAUDE.md` | `npm i -g agent-browser` (source machine: 0.27.0) |
| **`agy`** | optional draft-image agent that `ve doctor` reports on | separate Antigravity CLI install |
| **`rpc` / RunPod ComfyUI** | upscales, Wan/InfiniteTalk renders — a *separate* repo not in this bundle | clone `runpod-comfyui` and follow its README |

`ve doctor` only **fails** on missing FFmpeg or HyperFrames. Missing Chrome is
reported but does not fail the command — renders will still fail later.

---

## 3. Repo setup

### 3.1 `video-editor`

```bash
cd /path/to/creative_bundle/video-editor

bash scripts/install_ffmpeg.sh     # FFmpeg 8.1 -> tools/ffmpeg, ~/.local/bin
bash scripts/bootstrap.sh          # checks Python/uv/Node/pnpm/FFmpeg,
                                   # runs: uv sync --all-packages && pnpm install

cp .env.example .env               # then set OPENROUTER_API_KEY
uv run ve doctor                   # ffmpeg, hyperframes, chrome, agy
uv run ve --help
```

- `bootstrap.sh` installs `uv` / `pnpm` itself if they are missing, but it will
  not install Python, Node or FFmpeg.
- `uv sync --all-packages` is the workspace-wide install used by bootstrap.
  A plain `uv sync` at the root may miss optional workspace packages.
- `pnpm install` installs **HyperFrames 0.8.208** (pinned in `package.json`).
- `uv run ve doctor` must show `ffmpeg` and `hyperframes` as `OK`.
- `pnpm dev` starts the optional local dev stack.
- First transcription downloads a Whisper model (`base` by default) from
  Hugging Face; `VE_WHISPER_MODEL` / `config.yaml` change it.

Optional path isolation — keeps projects, outputs, queue DB and cache off the
checkout:

```bash
# in .env
VIDEO_EDITOR_DATA_DIR=/path/to/video-editor-data
```

### 3.2 `video-projects`

Nothing to install. It is content (scripts, prompts, notes, `publish.yaml`).
Renders run through `../video-editor`. `video-editor/videos` and
`video-editor/shorts` are symlinks into it.

### 3.3 `vocal-to-song`

```bash
cd /path/to/creative_bundle/vocal-to-song
uv venv -q -p 3.11 .venv
uv pip install -q -p .venv librosa soundfile pyworld pyloudnorm noisereduce scipy numpy "setuptools<70"

# verify
.venv/bin/python -I -c "import librosa, soundfile, pyworld; print('ok')"
```

- Run the scripts with `.venv/bin/python -I` exactly as `SKILL.md` shows.
- `setuptools<70` is required because `pyworld` imports `pkg_resources`.
- `ffmpeg` must be on `PATH` (the pipeline converts audio to 44.1 kHz mono).
- Optional DDSP flute: a **separate Python 3.10 venv**, ~1.4 GB, with
  `tensorflow-cpu==2.11.*`, `tensorflow-probability==0.19.*`,
  `ddsp==3.7.0 --no-deps` and the pins listed in `SKILL.md` §6. Skip it unless
  you need the real-flute timbre.

### 3.4 `social-engine`

```bash
cd /path/to/creative_bundle/social-engine
uv sync
npm install                     # browser lane: playwright-core 1.63.0
uv run social --help
```

Chrome is required. On Linux, install Xvfb if you want the browser lane to run
without a visible window.

**One-time Google setup** (needed for the API lane; the browser lane can upload
without it):

1. Google Cloud project → enable **YouTube Data API v3** and
   **YouTube Analytics API**.
2. OAuth consent screen → External → add yourself as a test user → **publish to
   production** (otherwise tokens expire after 7 days).
3. OAuth client → **Desktop app** → download the JSON to
   `~/.config/social-engine/youtube/client_secret.json` and `chmod 600` it.
4. `social auth youtube <account>` — consent in the browser.
5. `social login youtube <account>` — sign in to Google in the dedicated Chrome
   window.
6. Optional: apply for the YouTube API compliance audit; once granted, set
   `api_audited: true` in the account file under
   `~/.config/social-engine/youtube/accounts/`.

Scopes requested by the code:

```text
https://www.googleapis.com/auth/youtube.upload
https://www.googleapis.com/auth/youtube.force-ssl
https://www.googleapis.com/auth/yt-analytics.readonly
```

Everything stateful lives under `~/.config/social-engine/` (override with
`SOCIAL_ENGINE_HOME`): client secret, tokens, account files, Chrome profiles,
logs. The repo never reads `.env`.

Safety rules stay in force: nothing is uploaded, published or made public
without an explicit `--yes` / `confirm`, and there are no delete operations.

### 3.5 `drama-studio`

```bash
cd /path/to/creative_bundle/drama-studio
uv sync

# either keep drama keys separate:
cp .env.example .env
# ...or reuse video-editor's file, as the original setup did:
ln -s ../video-editor/.env .env
```

The scripts read environment variables first, then `drama-studio/.env`.
`dialogue_check.py` needs `SARVAM_API`; `poyo_jobs.py` needs `POYO_API_KEY`;
`drama_board.py` needs `DRAMA_REVIEW_URL` + `DRAMA_REVIEW_KEY`.

Most episode runners (`v2_*.py`) live in `video-projects/videos/<date>/<episode>/`,
not in `drama-studio`. The review gates and checks here work offline; the
production stages need the external services in §6.

---

## 4. API keys, accounts, and where they go

Copy each `.env.example` to `.env`; never commit `.env`. The examples contain
only placeholders.

### `video-editor/.env`

| Key | Required? | Used for |
|---|---|---|
| `OPENROUTER_API_KEY` | **yes** for default routing | all LLM tasks in `config.yaml`: plan, decompose, edit/scene plans, code, utility, verify-judge; also optional OpenRouter image/video/music |
| `OLLAMA_CLOUD_API_KEY` | only if using `config.free.yaml` | free-mode routing to Ollama Cloud Pro |
| `OLLAMA_API_KEY` | legacy fallback | `OllamaClient`, `config.example.yaml` |
| `TYPESAFE_API_KEY` | optional | TypeSafe/JEV classifier for per-item judgments |
| `ELEVENLABS_API_KEY` | per workflow | voice / TTS / speech-to-speech / audio isolation |
| `SARVAM_API` | per workflow | Sarvam Bulbul TTS, Indian-language voices |
| `POYO_API_KEY` | per workflow | PoYo music, image and video providers (default generative stack) |
| `FAL_KEY` | optional | fal.ai asset provider |
| `OPENAI_API_KEY` | optional | OpenAI asset provider |
| `REPLICATE_API_TOKEN` | optional | Replicate asset provider |
| `ACE_STEP_API_KEY` | optional | local ACE-Step music service, if it requires auth |
| `PEXELS_API_KEY` | optional | stock footage |
| `PIXABAY_API_KEY` | optional | stock footage |
| `FREESOUND_API_KEY` | optional | SFX search/download |
| `OPENVERSE_API_KEY` | optional | openverse search; anonymous requests work without it |
| `VIDEO_EDITOR_DATA_DIR` | recommended | keep projects/outputs/queue/cache outside the checkout |
| `VE_WORK_ROOT`, `VE_OUTPUT_ROOT`, `VE_CACHE_DIR` | optional | finer-grained overrides; they win over `VIDEO_EDITOR_DATA_DIR` |

`video-editor` loads `.env` from the **repo root** before the CLI is imported,
and does not override variables already in the environment.

### `drama-studio/.env`

| Key | Required? | Used for |
|---|---|---|
| `POYO_API_KEY` | yes for paid stages | PoYo jobs (`poyo_jobs.py`) |
| `SARVAM_API` | yes for dialogue check | `dialogue_check.py` second-read |
| `DRAMA_REVIEW_URL` | only for review board | Worker URL |
| `DRAMA_REVIEW_KEY` | only for review board | owner token (the `owner` row in D1 `reviewers`) |
| `RPC_REPO` | optional | path to a `runpod-comfyui` checkout for upscale scripts |

Originally `drama-studio/.env` was a symlink to `../video-editor/.env`, so the
`POYO_API_KEY` / `SARVAM_API` values were shared. Either symlink it again or
copy the values.

### `social-engine`

No `.env` and no API key string. Credentials are:

| Item | Location |
|---|---|
| OAuth client (Desktop app) | `~/.config/social-engine/youtube/client_secret.json` |
| OAuth tokens | `~/.config/social-engine/youtube/tokens/<account>.json` |
| Account metadata / `api_audited` | `~/.config/social-engine/youtube/accounts/` |
| Chrome profiles | `~/.config/social-engine/` |

### `vocal-to-song`, `video-projects`

No keys. `vocal-to-song` is fully local; `video-projects` is content.

### Where to obtain the keys

- OpenRouter — <https://openrouter.ai/keys>
- Ollama Cloud — <https://ollama.com>
- ElevenLabs — <https://elevenlabs.io>
- Sarvam — <https://dashboard.sarvam.ai>
- PoYo — <https://poyo.ai>
- fal — <https://fal.ai/dashboard/keys>
- Replicate — <https://replicate.com/account/api-tokens>
- Pexels / Pixabay / Freesound — their developer portals
- Google Cloud OAuth — <https://console.cloud.google.com/apis/credentials>

---

## 5. MCP connections

MCP is optional: the `ve` and `social` CLIs work without it. It is useful when
Claude should call the tools directly.

### 5.1 Servers that ship with the repos

| Server | Repo | Command | Needs |
|---|---|---|---|
| `video-editor` | `video-editor` | `uv run video-editor-mcp` | repo synced |
| `comfyui` | `video-editor` | `uv run python scripts/comfyui_mcp_server.py` | `COMFYUI_URL` (default `http://localhost:18188`) and a reachable ComfyUI |
| `openrouter` | `video-editor` | HTTP: `https://mcp.openrouter.ai/mcp` | OpenRouter auth when prompted |
| `social-engine` | `social-engine` | `uv run --directory /path/to/social-engine social-engine-mcp` | `SOCIAL_ENGINE_ROOTS` (colon-separated allowed content roots) |

Copy the examples and edit the absolute paths:

```bash
cp video-editor/.mcp.json.example video-editor/.mcp.json
cp social-engine/.mcp.json.example social-engine/.mcp.json
```

`social-engine` example:

```json
{
  "mcpServers": {
    "social-engine": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/social-engine", "social-engine-mcp"],
      "env": { "SOCIAL_ENGINE_ROOTS": "/path/to/video-projects/videos" }
    }
  }
}
```

`.mcp.json` is **project-scoped**: Claude Code reads the one at the root of the
directory you open. If you open each repo separately, copy that repo's example.
If you open `creative_bundle/` as one project, create a root `.mcp.json` instead:

```json
{
  "mcpServers": {
    "video-editor": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/creative_bundle/video-editor", "video-editor-mcp"]
    },
    "comfyui": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/creative_bundle/video-editor", "python", "scripts/comfyui_mcp_server.py"],
      "env": { "COMFYUI_URL": "http://localhost:18188" }
    },
    "social-engine": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/creative_bundle/social-engine", "social-engine-mcp"],
      "env": { "SOCIAL_ENGINE_ROOTS": "/path/to/creative_bundle/video-projects/videos" }
    },
    "openrouter": { "type": "http", "url": "https://mcp.openrouter.ai/mcp" }
  }
}
```

Claude Code asks for approval the first time a project-scoped server starts.

### 5.2 Flow MCP (not bundled)

The drama image/hero stages call Google Flow through a separate local MCP
server, and `drama-studio/scripts/flow_call.py` spawns it over stdio:

```text
node ~/flow-mcp/dist/index.js
```

Set it up separately from its own repo: `npm install`, then `npm run login`,
sign in to Google in the dedicated Chrome window, and open a Flow project. It
uses Chrome CDP on port `9333` (override with `FLOW_MCP_CDP_PORT`) and writes
outputs to `~/flow-mcp-out/`. Optional `GEMINI_API_KEY` for its own agent.
Add it to `.mcp.json` if Claude should drive Flow directly:

```json
{
  "mcpServers": {
    "flow": { "command": "node", "args": ["/home/<you>/flow-mcp/dist/index.js"] }
  }
}
```

> **Known break (2026-11-24):** Flow renamed its agent toggle from "Agent" to "Flow agent", so flow-mcp
> generations time out (`src/flow.ts`, two `getByRole("button", { name: "Agent", exact: true })` locators; see
> `video-editor/docs/ISSUES_BUGS.md` #373). Until it is patched, drive Flow in your own Chrome with the Claude in
> Chrome extension (Image mode, Nano Banana, 0 credits).

`drama-studio/scripts/flow_call.py` hardcodes `~/flow-mcp/dist/index.js`. Keep
the checkout there, or edit the `SERVER` line in that script if yours lives
elsewhere.

### 5.3 Plugin / third-party MCPs

These are not part of the repos. Install the plugins only if you use the
corresponding workflow; each brings its own auth.

| MCP | Use | Notes |
|---|---|---|
| `context7` | library docs | plugin `context7` |
| `github` | issues/PRs | plugin `github`, OAuth |
| `firecrawl` | scraping/research | plugin `firecrawl`, API key |
| `cloudflare` | review-board deploys | plugin `cloudflare` |
| `supabase`, `vercel`, `posthog` | unrelated to these five repos | skip unless needed |
| `runpod` | pod/endpoint management | plugin; useful for the drama GPU stages |
| `replicate` | model predictions | API token |
| `elevenlabs` | voice generation | API key |
| `playwright`, `chrome-devtools` | browser automation | **not preferred here** — `CLAUDE.md` says use the `agent-browser` CLI instead |

`claude/PLUGINS.md` lists the plugins the bundled skills expect.

---

## 6. External services the pipelines call

Only needed for the stage that uses them.

| Service | Used by | Setup |
|---|---|---|
| **OpenRouter** | `video-editor` LLM routing by default | `OPENROUTER_API_KEY` |
| **PoYo** | `video-editor`/drama music, image, video | `POYO_API_KEY` |
| **Sarvam** | TTS, drama dialogue check | `SARVAM_API` |
| **ElevenLabs** | voice work | `ELEVENLABS_API_KEY` |
| **Freesound** | SFX search/download | `FREESOUND_API_KEY`; respect CC0 licences |
| **Google Flow** | drama stills and hero shots; `video-editor` image gen | Flow MCP + signed-in Chrome (§5.2) |
| **Modal** | MiniMax H3 lip-sync (`rpc run h3_lipsync --modal`) | `uv tool install modal`; `modal token new` |
| **RunPod ComfyUI** | upscales, Wan/InfiniteTalk renders | separate `runpod-comfyui` repo + its own config |
| **earshot** | designed SFX bank | local tool at `~/tools/earshot`; no API key |
| **Cloudflare** | drama review board | `wrangler login`, R2 + D1 (§7) |
| **YouTube** | publishing | Google OAuth (§3.4) |

The drama production playbook documents which stage uses which service:
`drama-studio/docs/DRAMA_PRODUCTION_PLAYBOOK.md`.

---

## 7. Optional: drama review board (Cloudflare)

Only needed if you want remote reviewers and the `DRAMA_REVIEW_*` integration.
The local gates (`scripts/drama_review.py`) work without it.

```bash
cd drama-studio/apps/drama-review
wrangler login
wrangler r2 bucket create drama-review
wrangler d1 create drama-review

# put the returned database_id into wrangler.jsonc, then:
wrangler d1 execute drama-review --remote --file schema.sql
wrangler deploy

# insert one owner row; the token is the owner's personal link:
wrangler d1 execute drama-review --remote --command \
  "INSERT INTO reviewers (token, name, role) VALUES ('<owner-token>', 'Owner', 'owner');"

# then in video-editor/.env:
# DRAMA_REVIEW_URL=https://<your-worker>.workers.dev
# DRAMA_REVIEW_KEY=<owner-token>
```

The bundled `wrangler.jsonc` still contains the **original owner's** D1
`database_id`, and the app README shows their live URL. Replace both with your
own — do not deploy against someone else's account.

Usage, from `drama-studio/`:

```bash
cd /path/to/creative_bundle/drama-studio
uv run python scripts/drama_board.py publish dialogue videos/<date>/<episode> "note"
uv run python scripts/drama_board.py invite "Name"
uv run python scripts/drama_board.py pull dialogue videos/<date>/<episode>
```

A personal reviewer link works like a password. Revoke it by deleting the row
from `reviewers`.

---

## 8. Claude Code setup

The bundle carries user-level context, but nothing is installed automatically.
Full instructions: `claude/README.md`.

```bash
cd /path/to/creative_bundle
mkdir -p ~/.claude/skills
for d in claude/skills/*/; do
  ln -sfn "$PWD/$d" ~/.claude/skills/"$(basename "$d")"
done
```

- `CLAUDE.md` at the bundle root is read when Claude opens `creative_bundle/`.
- Repo-local skills (`.claude/skills/`) load on their own when you open a repo.
- `video-editor/.remember/` is picked up by the `remember` plugin.
- Plugin list and marketplaces: `claude/PLUGINS.md`.
- Docs map: `claude/docs/INDEX.md`.

If you open a single repo instead of the bundle root, also place/copy the root
`CLAUDE.md` guidance where Claude can see it, or rely on each repo's own
`CLAUDE.md`.

---

## 9. Smoke tests

```bash
# video-editor
cd /path/to/creative_bundle/video-editor
uv run ve doctor
uv run ve --help

# social-engine
cd /path/to/creative_bundle/social-engine
uv run social --help
uv run social list youtube <account>        # after auth

# drama-studio (offline gates)
cd /path/to/creative_bundle/drama-studio
uv run python scripts/drama_review.py status <episode>

# vocal-to-song
cd /path/to/creative_bundle/vocal-to-song
.venv/bin/python -I -c "import librosa, soundfile, pyworld; print('ok')"
```

A minimal `video-editor` run still needs `OPENROUTER_API_KEY`; `ve doctor` and
`ve --help` do not.

---

## 10. Gotchas

- **Keep the sibling layout.** `video-editor/videos`, `video-editor/shorts` and
  `drama-studio/videos` are relative symlinks into `../video-projects`. Moving
  one repo alone breaks them.
- **Assets are not included.** The bundle has content and code, not the 13 GB of
  source footage, renders, music beds or intermediate frames. Some tracked
  symlinks under `video-projects/videos/` point at untracked asset folders and
  will dangle until you regenerate them.
- **Absolute paths from the original machine** still appear in some docs,
  configs and scripts (`/mnt/data2/...`). Rewrite them for your layout.
- **`video-editor/config.yaml` is the original owner's live routing config.**
  `config.example.yaml` is the annotated template; use it if you want a clean
  start. Keys are read from `.env`.
- **Python versions differ on purpose.** `video-editor`, `social-engine` and
  `drama-studio` use 3.12; `vocal-to-song` uses 3.11; the optional DDSP flute
  uses 3.10. Don't run vocal scripts with the repo's default interpreter.
- **`~/.local/bin` must be on `PATH`** for the FFmpeg symlinks that
  `install_ffmpeg.sh` creates.
- **Chrome binary names.** `ve doctor` looks for `google-chrome`, `chromium` or
  `chromium-browser`. `social-engine` tests also honour `CHROME_PATH`.
- **No GPU required for `ve` itself.** Whisper runs on CPU with `int8`; CUDA is
  picked up automatically if present. GPU work happens on Modal/RunPod.
- **Disk.** FFmpeg + HyperFrames + Python/Node environments are a few GB;
  Whisper models, caches and renders grow from there. Budget 10–20 GB for real
  work, more for drama episodes.
- **Network egress** is required for model providers, YouTube, Cloudflare and
  Flow. Nothing here is fully offline except `vocal-to-song`.
- **Never commit `.env`.** Each repo's `.gitignore` already covers it; the
  examples are the only env files meant to travel.
- **`social-engine` safety.** `--yes`/`confirm` is the only way to make
  something visible; nothing is deleted. Keep those checks when modifying it.
- **Review board config is owner-specific.** Replace the D1 `database_id` and
  the URL before deploying your own (§7).
