#!/usr/bin/env bash
# Start an SSH tunnel for the Vast.ai ComfyUI API.
# After running this, ComfyUI API is reachable at http://localhost:18188

set -euo pipefail

# Source project env if available
if [ -f /mnt/data2/git_repos/creative_repos/video-editor/.env ]; then
    set -a
    . /mnt/data2/git_repos/creative_repos/video-editor/.env
    set +a
fi

HOST="${COMFYUI_POD_SSH_HOST:-${COMFYUI_POD_IP:-154.64.230.50}}"
PORT="${COMFYUI_POD_SSH_PORT:-22464}"
LOCAL_PORT="18188"
REMOTE="localhost:18188"
PIDFILE="/tmp/comfyui_ssh_tunnel.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Tunnel already running (PID $(cat $PIDFILE))"
    exit 0
fi

ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -N -L "${LOCAL_PORT}:${REMOTE}" -p "$PORT" "root@${HOST}" &

PID=$!
echo "$PID" > "$PIDFILE"
sleep 2

if kill -0 "$PID" 2>/dev/null; then
    echo "ComfyUI tunnel started: http://localhost:${LOCAL_PORT} -> ${HOST}:${PORT}"
else
    echo "Failed to start tunnel" >&2
    rm -f "$PIDFILE"
    exit 1
fi
