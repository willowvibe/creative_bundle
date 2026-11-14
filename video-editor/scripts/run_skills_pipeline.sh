#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# Environment-based configuration with sensible defaults.
SOURCE="${SOURCE:-}"
JOB_ID="${JOB_ID:-}"
PRESET="${PRESET:-short_explainer}"
WORK_ROOT="${WORK_ROOT:-./projects}"
OUTPUT_ROOT="${OUTPUT_ROOT:-./outputs}"
TRANSCRIPT="${TRANSCRIPT:-}"
MUSIC="${MUSIC:-}"
OUTPUT_NAME="${OUTPUT_NAME:-}"

usage() {
    cat <<EOF
Run the full skills-first video pipeline in one command.

Required environment variables:
  SOURCE    Path to raw footage file (e.g. ./demo/raw.mp4)
  JOB_ID    Unique job identifier (e.g. demo-1)

Optional environment variables:
  PRESET        Graphics preset [default: short_explainer]
  WORK_ROOT     Project workspace root [default: ./projects]
  OUTPUT_ROOT   Final exports root [default: ./outputs]
  TRANSCRIPT    Path to pre-generated transcript JSON
  MUSIC         Path to background music file
  OUTPUT_NAME   Final video filename [default: final.mp4]

Example:
  export SOURCE=./demo/raw.mp4
  export JOB_ID=demo-1
  export PRESET=short_explainer
  export TRANSCRIPT=./demo/transcript.json
  bash scripts/run_skills_pipeline.sh
EOF
}

if [ -z "$SOURCE" ] || [ -z "$JOB_ID" ]; then
    echo "ERROR: SOURCE and JOB_ID are required." >&2
    usage >&2
    exit 1
fi

if [ ! -f "$SOURCE" ]; then
    echo "ERROR: SOURCE file not found: $SOURCE" >&2
    exit 1
fi

check_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "ERROR: $1 not found. Install $2 and try again." >&2
        exit 1
    fi
}

check_command ffmpeg "FFmpeg 6.1+"
check_command ffprobe "ffprobe (from FFmpeg)"

VE_BIN="ve"
if ! command -v "$VE_BIN" >/dev/null 2>&1; then
    # Fall back to uv run when the CLI is not installed globally.
    VE_BIN="uv run ve"
fi

args=(
    run "$SOURCE"
    --job-id "$JOB_ID"
    --work-root "$WORK_ROOT"
    --output-root "$OUTPUT_ROOT"
    --preset "$PRESET"
)

if [ -n "$TRANSCRIPT" ]; then
    if [ ! -f "$TRANSCRIPT" ]; then
        echo "ERROR: TRANSCRIPT file not found: $TRANSCRIPT" >&2
        exit 1
    fi
    args+=(--transcript "$TRANSCRIPT")
fi

if [ -n "$MUSIC" ]; then
    if [ ! -f "$MUSIC" ]; then
        echo "ERROR: MUSIC file not found: $MUSIC" >&2
        exit 1
    fi
    args+=(--music-path "$MUSIC")
fi

if [ -n "$OUTPUT_NAME" ]; then
    args+=(--output-name "$OUTPUT_NAME")
fi

echo "== Running skills-first pipeline =="
echo "Source:      $SOURCE"
echo "Job ID:      $JOB_ID"
echo "Preset:      $PRESET"
echo "Work root:   $WORK_ROOT"
echo "Output root: $OUTPUT_ROOT"
[ -n "$TRANSCRIPT" ] && echo "Transcript:  $TRANSCRIPT"
[ -n "$MUSIC" ]      && echo "Music:       $MUSIC"
[ -n "$OUTPUT_NAME" ] && echo "Output name: $OUTPUT_NAME"
echo ""

$VE_BIN "${args[@]}"

echo ""
echo "== Pipeline complete =="
echo "Final video:   $OUTPUT_ROOT/$JOB_ID/final.mp4"
echo "Report:        $OUTPUT_ROOT/$JOB_ID/report.md"
echo "Decision log:  $OUTPUT_ROOT/$JOB_ID/decision_log.jsonl"
