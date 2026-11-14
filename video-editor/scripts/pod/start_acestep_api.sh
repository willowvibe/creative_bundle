#!/usr/bin/env bash
# Start ACE-Step 1.5 REST API server on the Vast.ai pod.
set -euo pipefail

REPO_DIR="/workspace/ACE-Step-1.5"
PIDFILE="/tmp/acestep_api.pid"
LOG_FILE="/workspace/acestep_api.log"

if [[ ! -d "$REPO_DIR/.venv" ]]; then
    echo "ACE-Step not installed. Run install_acestep_on_pod.sh first." >&2
    exit 1
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "ACE-Step API already running (PID $(cat "$PIDFILE"))"
    exit 0
fi

cd "$REPO_DIR"

# Bind to localhost only; tunnel via SSH to local machine
nohup uv run --no-sync acestep-api \
    --host 127.0.0.1 \
    --port 8001 \
    --download-source huggingface \
    --lm-model-path acestep-5Hz-lm-1.7B \
    --no-init \
    > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 3

if kill -0 "$PID" 2>/dev/null; then
    echo "ACE-Step API server started: http://localhost:8001 -> pid $PID"
else
    echo "Failed to start ACE-Step API. See $LOG_FILE" >&2
    rm -f "$PIDFILE"
    exit 1
fi
