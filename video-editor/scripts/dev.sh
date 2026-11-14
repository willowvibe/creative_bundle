#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

cat <<'EOF'
== Video Editor Engine Dev ==

The Remotion dev stack (apps/editor + apps/renderer) has been retired — all
video is rendered either by the FFmpeg render graph or by HyperFrames.

Preview a HyperFrames composition project:
    hyperframes preview <project_dir>

Render a script-to-video scene plan:
    uv run ve render --job-id <job>

Run the MCP server:
    uv run video-editor-mcp
EOF
