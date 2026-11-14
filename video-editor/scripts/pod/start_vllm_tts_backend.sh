#!/usr/bin/env bash
# Serve SPRINGLab/Indic-Mio via vLLM as the LLM backend for MioTTS-Inference.
set -euo pipefail

VENV_DIR="/workspace/vllm-tts-backend/.venv"
PIDFILE="/tmp/vllm_tts_backend.pid"
LOG_FILE="/workspace/vllm_tts_backend.log"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "vLLM TTS backend not installed. Run install_indic_mio_on_pod.sh first." >&2
    exit 1
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "vLLM TTS backend already running (PID $(cat "$PIDFILE"))"
    exit 0
fi

export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

cd /workspace/vllm-tts-backend
nohup "$VENV_DIR/bin/vllm" serve SPRINGLab/Indic-Mio \
    --host 127.0.0.1 \
    --port 8000 \
    --gpu-memory-utilization 0.45 \
    --max-model-len 8192 \
    --dtype bfloat16 \
    > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 5

if kill -0 "$PID" 2>/dev/null; then
    echo "vLLM TTS backend started: http://localhost:8000 -> pid $PID"
else
    echo "Failed to start vLLM TTS backend. See $LOG_FILE" >&2
    rm -f "$PIDFILE"
    exit 1
fi
