#!/usr/bin/env bash
# Install and start ACE-Step 1.5 REST API on the Vast.ai ComfyUI pod.
set -euo pipefail

REPO_DIR="/workspace/ACE-Step-1.5"
LOG_FILE="/workspace/acestep_install.log"

# uv's rustls/webpki root store sometimes rejects PyPI's Let's Encrypt chain on
# this pod. Point it to the system CA bundle to avoid spurious TLS errors.
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"

{
echo "=== ACE-Step 1.5 setup started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [[ ! -d "$REPO_DIR" ]]; then
    echo "Cloning ACE-Step-1.5..."
    cd /workspace
    git clone https://github.com/ACE-Step/ACE-Step-1.5.git "$REPO_DIR"
else
    echo "ACE-Step-1.5 already cloned."
fi

cd "$REPO_DIR"

# Ensure .env is configured for API-only, remote-tunnel-friendly operation
cat > .env <<'EOF'
ACESTEP_CONFIG_PATH=acestep-v15-turbo
ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B
ACESTEP_DEVICE=auto
ACESTEP_LM_BACKEND=vllm
ACESTEP_INIT_LLM=auto
ACESTEP_DOWNLOAD_SOURCE=huggingface
ACESTEP_NO_INIT=true
ACESTEP_API_HOST=127.0.0.1
ACESTEP_API_PORT=8001
EOF

echo "Syncing dependencies with uv (this may take several minutes)..."
if ! uv sync; then
    echo "uv sync failed. Check network / disk space."
    exit 1
fi

# Install optional torchao compat if needed (script already handles this, but explicit)
uv pip install --python .venv/bin/python --force-reinstall torchao==0.16.0 || true

echo "=== Setup complete at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee "$LOG_FILE"
