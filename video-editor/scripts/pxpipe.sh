#!/usr/bin/env bash
set -euo pipefail

# Start the pxpipe token-saving proxy for Claude Code sessions.
# Usage:
#   scripts/pxpipe.sh
#
# Then, in another terminal:
#   export ANTHROPIC_BASE_URL=http://127.0.0.1:47821
#   claude
#
# Or combine them:
#   ANTHROPIC_BASE_URL=http://127.0.0.1:47821 claude

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

if ! command -v npx >/dev/null 2>&1; then
    echo "ERROR: npx is required. Install Node.js and npm."
    exit 1
fi

echo "== pxpipe token-saving proxy =="
echo "Dashboard: http://127.0.0.1:47821/"
echo "Run Claude with: ANTHROPIC_BASE_URL=http://127.0.0.1:47821 claude"
echo "Press Ctrl+C to stop."
echo ""

exec npx pxpipe-proxy "$@"
