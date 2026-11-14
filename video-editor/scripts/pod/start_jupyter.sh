#!/usr/bin/env bash
# Start the JupyterLab server on the Vast.ai pod.
#
# Usage on the pod:
#   bash /workspace/start_jupyter.sh
#
# Exposes JupyterLab on 127.0.0.1:8888 — to reach it from your local
# machine, add the following to your local SSH tunnel:
#   -L 8888:127.0.0.1:8888
# Then open http://localhost:8888 in your browser.
set -euo pipefail

JUPYTER_DIR="/workspace/jupyter"
PIDFILE="/tmp/jupyter.pid"
LOG_FILE="/workspace/jupyter.log"

if [[ ! -x "$JUPYTER_DIR/.venv/bin/jupyter-lab" ]]; then
    echo "Jupyter not installed. Run install_jupyter_on_pod.sh first." >&2
    exit 1
fi

# If a previous instance is alive, exit cleanly.
if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Jupyter already running (PID $(cat "$PIDFILE"))"
    exit 0
fi
rm -f "$PIDFILE"

# Generate a fresh per-boot token. The full URL is printed in the log so
# the user can copy it once. Token auth is sufficient because the server
# is bound to 127.0.0.1 and only reachable through the SSH tunnel.
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export JUPYTER_TOKEN="$TOKEN"
export JUPYTERHUB_DISABLE_USER_CHECK=1

cd "$JUPYTER_DIR"
nohup ./.venv/bin/jupyter-lab \
    --config /root/.jupyter/jupyter_lab_config.py \
    --ServerApp.token="$TOKEN" \
    --ServerApp.password="" \
    --ServerApp.allow_origin="" \
    > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 3

if kill -0 "$PID" 2>/dev/null; then
    echo "JupyterLab started: http://127.0.0.1:8888 (pid $PID)"
    echo
    echo "  Open this URL in your browser (token is per-boot, copy it now):"
    echo "    http://localhost:8888/lab?token=$TOKEN"
    echo
    echo "  If you don't have an SSH tunnel yet, start one with:"
    echo "    ssh -N -p 30532 -i ~/.ssh/id_ed25519 \\"
    echo "      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\"
    echo "      -L 8888:127.0.0.1:8888 root@ssh7.vast.ai"
    echo
    echo "  Logs: $LOG_FILE"
    echo "  Stop:  kill \$(cat $PIDFILE)"
else
    echo "Failed to start Jupyter. See $LOG_FILE" >&2
    rm -f "$PIDFILE"
    exit 1
fi
