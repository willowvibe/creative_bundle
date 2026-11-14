#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "== Video Editor Engine Bootstrap =="

# Verify Python 3.12+
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install Python 3.12+."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
REQUIRED="3.12"
if [ "$(printf '%s\n' "$REQUIRED" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED" ]; then
    echo "ERROR: Python $PYTHON_VERSION found, but $REQUIRED+ is required."
    exit 1
fi
echo "✓ Python $PYTHON_VERSION"

# Verify uv
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
uv --version

# Verify Node 22+
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: Node.js not found. Install Node 22 (e.g. via nvm)."
    exit 1
fi
NODE_VERSION=$(node --version | sed 's/v//')
REQUIRED_NODE="22.0.0"
if [ "$(printf '%s\n' "$REQUIRED_NODE" "$NODE_VERSION" | sort -V | head -n1)" != "$REQUIRED_NODE" ]; then
    echo "ERROR: Node $NODE_VERSION found, but $REQUIRED_NODE+ is required."
    exit 1
fi
echo "✓ Node $NODE_VERSION"

# Verify pnpm
if ! command -v pnpm >/dev/null 2>&1; then
    echo "Installing pnpm..."
    npm install -g pnpm
fi
pnpm --version

# Verify FFmpeg
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "ERROR: ffmpeg and ffprobe are required."
    exit 1
fi
FFMPEG_VERSION=$(ffmpeg -version | head -n1 | awk '{print $3}')
echo "✓ FFmpeg $FFMPEG_VERSION"

# Install Python workspace
echo "Installing Python workspace with uv..."
uv sync --all-packages

# Install Node workspace
echo "Installing Node workspace with pnpm..."
pnpm install

echo ""
echo "== Bootstrap complete =="
echo "Run the CLI with: uv run ve --help"
echo "Run the dev stack with: pnpm dev"
