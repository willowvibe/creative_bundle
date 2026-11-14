#!/usr/bin/env bash
# Start MioTTS-Inference server pointing at the local vLLM Indic-Mio backend.
set -euo pipefail

REPO_DIR="/workspace/MioTTS-Inference"
PIDFILE="/tmp/miotts_server.pid"
LOG_FILE="/workspace/miotts_server.log"

if [[ ! -d "$REPO_DIR/.venv" ]]; then
    echo "MioTTS-Inference not installed. Run install_indic_mio_on_pod.sh first." >&2
    exit 1
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "MioTTS server already running (PID $(cat "$PIDFILE"))"
    exit 0
fi

export MIOTTS_PORT=8002
export MIOTTS_HOST=127.0.0.1
export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

cd "$REPO_DIR"
nohup .venv/bin/python run_server.py \
    --llm-base-url http://localhost:8000/v1 \
    > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 3

if kill -0 "$PID" 2>/dev/null; then
    echo "MioTTS server started: http://localhost:8002 -> pid $PID"
else
    echo "Failed to start MioTTS server. See $LOG_FILE" >&2
    rm -f "$PIDFILE"
    exit 1
fi
