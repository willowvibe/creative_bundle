#!/usr/bin/env bash
# EP3 full build after narration + captures: screen track -> avatar -> bumpers -> SFX -> join + mix. usage: bash render_all.sh
set -euo pipefail
P="$(cd "$(dirname "$0")" && pwd)"; VE=/mnt/data2/git_repos/creative_repos/video-editor; PY=$VE/.venv/bin/python
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$P/audio/narration.wav")
cd "$P" && $PY screen/render_scenes.py audio/words.json screen/outputs.json screen/screen.mp4 --duration "$D"
cd "$VE" && uv run ve talking-avatar --screen "$P/screen/screen.mp4" --audio "$P/audio/narration.wav" \
  --avatar "$P/../../2026-11-24/talking-avatar-pilot/avatar/sprites" --script "$P/audio/narration.txt" \
  --bubbles "$P/bubbles.yaml" --out "$P/out/mcp_v1.mp4"
cd "$P/../.." && $PY ../brand/python-decoder/make_bumpers.py "$P/out" --episode "EP 3 · MCP server" --next "Up next: more modern Python" --question "What tool would you give your AI first?"
TOTAL=$(python3 -c "print($D + 16)")
cd "$P" && $PY screen/make_sfx.py audio/words.json screen/outputs.json out/sfx --offset 4.0 --duration "$TOTAL"
bash "$P/mix.sh"
echo ALL_DONE
