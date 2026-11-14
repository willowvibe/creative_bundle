#!/usr/bin/env bash
# Stop the SSH tunnel for the Vast.ai ComfyUI API.

set -euo pipefail
PIDFILE="/tmp/comfyui_ssh_tunnel.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "No tunnel PID file found"
    exit 0
fi

PID=$(cat "$PIDFILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "Stopped ComfyUI tunnel (PID $PID)"
else
    echo "Tunnel process $PID not running"
fi

rm -f "$PIDFILE"
