# Packaging the Video Editor Engine for Distribution

This guide explains how to package the engine so another person can install and run it on their laptop or server without needing to understand the monorepo internals.

## What must travel with the engine

The engine is not a single binary. At runtime it needs:

| Requirement | Why |
|-------------|-----|
| **Python 3.12+** | The whole workspace is `>=3.12`. |
| **uv** | Installs the Python workspace and creates the virtual env. |
| **Node.js 22+** | Optional FableCut bridge and bundled FableCut editor. |
| **pnpm** | Installs the Node workspace. |
| **FFmpeg 8.1 + ffprobe** | All video rendering and probing (`scripts/install_ffmpeg.sh` installs a static build). |
| **API keys / `.env`** | Generative providers (OpenRouter, ElevenLabs, Sarvam, FAL, OpenAI, Replicate, Poyo, etc.). |
| **`config.yaml`** | Model routing, output paths, provider selection. |
| **`pricing.yaml`** | USD cost table used by `ve estimate` and `BudgetGovernor`. |

You cannot avoid sharing secrets/config unless you only use free/local mode (`config.free.yaml` + local TTS/music). The recipient must still supply their own API keys.

---

## Recommended approach: Docker image (most reliable)

A Docker image bakes Python, Node, uv, pnpm, FFmpeg, and the engine source into one artifact. The recipient only needs Docker and a `.env` file.

### 1. Add a `Dockerfile` at repo root

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base

# Install system dependencies: FFmpeg, Node 22, curl, git
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ffprobe \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node 22 (LTS) and pnpm
ENV NODE_VERSION=22
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && corepack enable \
    && corepack prepare pnpm@latest --activate \
    && rm -rf /var/lib/apt/lists/*

# Install uv
ADD https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN sh /tmp/uv-install.sh && rm /tmp/uv-install.sh
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock .python-version ./
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
# One line per [tool.uv.workspace] member (keep in sync with pyproject.toml)
COPY apps/cli/pyproject.toml apps/cli/
COPY apps/engine/pyproject.toml apps/engine/
COPY apps/transcribe/pyproject.toml apps/transcribe/
COPY packages/asset_ingestion/pyproject.toml packages/asset_ingestion/
COPY packages/comfystudio_bridge/pyproject.toml packages/comfystudio_bridge/
COPY packages/config/pyproject.toml packages/config/
COPY packages/core/pyproject.toml packages/core/
COPY packages/fablecut_bridge/pyproject.toml packages/fablecut_bridge/
COPY packages/ffmpeg/pyproject.toml packages/ffmpeg/
COPY packages/llm/pyproject.toml packages/llm/
COPY packages/maps/pyproject.toml packages/maps/
COPY packages/mcp_server/pyproject.toml packages/mcp_server/
COPY packages/pipelines/pyproject.toml packages/pipelines/
COPY packages/skills/pyproject.toml packages/skills/
COPY packages/state/pyproject.toml packages/state/
COPY packages/tools/pyproject.toml packages/tools/

# Install Python dependencies
RUN uv sync --all-packages --no-dev

# Copy full source and install Node workspace
COPY . .
RUN pnpm install --frozen-lockfile

# Default entrypoint: the `ve` CLI
ENTRYPOINT ["uv", "run", "ve"]
CMD ["--help"]
```

### 2. Add `.dockerignore`

```gitignore
# Build/runtime artifacts
.venv/
node_modules/
.output/
output/
outputs/
projects/
videos/
tests/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.pyc
__pycache__/

# Secrets and local config
.env
.env.local
*.db

# Git / editor
.git/
.gitignore
.claude/
.vscode/
.idea/
```

### 3. Build and share

```bash
# Build the image
docker build -t video-editor:0.1.0 .

# Save to a tarball they can copy over USB/cloud storage
docker save video-editor:0.1.0 | gzip > video-editor-0.1.0.tar.gz

# Recipient loads it
gunzip -c video-editor-0.1.0.tar.gz | docker load
```

### 4. Run on the recipient's machine

```bash
# Create a directory with their config and .env
mkdir -p ~/video-editor-run/{projects,outputs,videos}
cp .env.example ~/video-editor-run/.env
# (recipient edits ~/video-editor-run/.env with their keys)
cp config.free.yaml ~/video-editor-run/config.yaml

# Run the CLI through the container
docker run --rm -it \
  -v ~/video-editor-run/.env:/app/.env:ro \
  -v ~/video-editor-run/config.yaml:/app/config.yaml:ro \
  -v ~/video-editor-run/projects:/app/projects \
  -v ~/video-editor-run/outputs:/app/outputs \
  -v ~/video-editor-run/videos:/app/videos \
  video-editor:0.1.0 \
  run ./demo/raw.mp4 --job-id demo --work-root ./projects --output-root ./outputs
```

### 5. Optional: publish to a registry

If both parties have internet access, push to Docker Hub or GitHub Container Registry instead of passing a tarball:

```bash
docker tag video-editor:0.1.0 ghcr.io/YOUR_ORG/video-editor:0.1.0
docker push ghcr.io/YOUR_ORG/video-editor:0.1.0
```

---

## Option 2: Source archive + bootstrap script (simplest for developers)

If the recipient is comfortable with a terminal, give them the repo and let `scripts/bootstrap.sh` install everything.

```bash
# On your machine
git archive --format=tar.gz HEAD > video-editor-source.tar.gz

# Or just give them the git URL
# git clone <repo-url> video-editor
```

Recipient steps:

```bash
tar -xzf video-editor-source.tar.gz
cd video-editor

# Install prerequisites manually if missing:
# - Python 3.12+   (https://www.python.org/)
# - Node.js 22+    (https://nodejs.org/)
# - FFmpeg 6.1+    (apt/brew/choco)
# - uv             (curl -LsSf https://astral.sh/uv/install.sh | sh)
# - pnpm           (npm install -g pnpm)

bash scripts/bootstrap.sh
cp .env.example .env
# edit .env with their keys
uv run ve --help
```

Pros: no Docker knowledge needed.  
Cons: recipient must manage Python/Node/FFmpeg versions themselves; platform-specific install steps differ.

---

## Option 3: pip-installable wheel / PyPI (cleanest for Python users)

The workspace already uses `hatchling` and `[project.scripts]`, so each package can be built as a wheel. However, the CLI depends on many local workspace packages, so publishing to PyPI means uploading all of them.

### Build wheels for the whole workspace

```bash
# Build every workspace member
uv build --all-packages

# Wheels land in dist/ at each package root and at repo root
find . -name '*.whl' -path '*/dist/*'
```

### Share offline

```bash
mkdir -p dist-wheels
find . -name '*.whl' -path '*/dist/*' -exec cp {} dist-wheels/ \;
# Also collect external deps if the target has no internet
uv pip download -r pyproject.toml --no-deps -d dist-wheels/
```

### Install on recipient's machine

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --no-index --find-links ./dist-wheels video-editor-cli
ve --help
```

Pros: feels like a normal Python package.  
Cons: still requires FFmpeg/Node to be pre-installed; wheel distribution for 20+ workspace packages is verbose.

---

## Option 4: Single-file standalone executable (hardest, most convenient)

Tools like PyInstaller or `uv python` can produce a self-contained binary, but this engine has too many external moving parts (FFmpeg subprocesses, Node server, config files) for a single file to be practical without major code changes. Not recommended unless you want to maintain a custom launcher.

---

## How to share secrets and configuration

Never commit `.env` or live API keys. Use this pattern:

1. **Ship `.env.example`** and `config.example.yaml` in the repo/image.
2. **Tell the recipient to copy and fill them in** on their own machine.
3. **Mount them as files** when running Docker (see Option 1).

For a one-click experience, create a small setup script:

```bash
#!/usr/bin/env bash
# scripts/setup-recipient.sh
set -euo pipefail

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env — please edit it with your API keys."
fi

if [ ! -f config.yaml ]; then
  cp config.free.yaml config.yaml
  echo "Created config.yaml (free/local mode)."
fi

mkdir -p projects outputs videos
```

---

## Verification checklist

Before handing the packaged engine to someone else, verify it works from a clean environment:

```bash
# 1. Build the chosen artifact
# 2. Create a fresh directory and copy only the artifact + .env.example + config.example.yaml
# 3. Install/run following only the instructions above
# 4. Run the smoke test
ve --help
ve run ./demo/raw.mp4 --job-id smoke --work-root ./projects --output-root ./outputs --config config.free.yaml
```

If using Docker, test the image without mounting your local `.venv` or `node_modules`:

```bash
docker run --rm -it -v $(pwd)/demo:/app/demo:ro video-editor:0.1.0 --help
```

---

## Recommendation

| Recipient profile | Best option |
|-------------------|-------------|
| Non-technical / wants one command | **Docker image + setup script** |
| Comfortable with Python/Node | **Source + `scripts/bootstrap.sh`** |
| Wants `pip install video-editor` | **Wheels / PyPI** |
| Air-gapped / no internet | **Docker tarball + wheels in `dist-wheels/`** |

Start with the Docker route. It is the only option that fully controls the Python, Node, FFmpeg, and dependency versions, which is where most "works on my machine" problems come from.
