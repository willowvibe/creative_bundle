#!/usr/bin/env bash
# Stop ACE-Step 1.5 REST API server on the Vast.ai pod.
set -euo pipefail
PIDFILE="/tmp/acestep_api.pid"
if [[ -f "$PIDFILE" ]]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Stopped ACE-Step API server (PID $PID)"
    else
        echo "ACE-Step API server not running"
    fi
    rm -f "$PIDFILE"
else
    echo "No PID file found at $PIDFILE"
fi
