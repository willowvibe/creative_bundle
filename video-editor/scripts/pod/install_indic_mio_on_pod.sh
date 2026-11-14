#!/usr/bin/env bash
# Install Indic-Mio TTS stack on the Vast.ai ComfyUI pod.
set -euo pipefail

export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

{
echo "=== Indic-Mio TTS setup started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1. MioTTS-Inference (server + codec wrapper)
if [[ ! -d /workspace/MioTTS-Inference ]]; then
    echo "Cloning MioTTS-Inference..."
    cd /workspace
    git clone https://github.com/Aratako/MioTTS-Inference.git
else
    echo "MioTTS-Inference already cloned."
fi
cd /workspace/MioTTS-Inference
if [[ ! -d .venv ]]; then
    echo "Syncing MioTTS-Inference dependencies..."
    uv sync 2>&1 | tail -30
else
    echo "MioTTS-Inference venv already exists."
fi

# 2. Separate vLLM backend venv for serving SPRINGLab/Indic-Mio
mkdir -p /workspace/vllm-tts-backend
cd /workspace/vllm-tts-backend
if [[ ! -d .venv ]]; then
    echo "Creating vLLM backend venv..."
    uv venv --python 3.11
fi
echo "Installing vLLM in backend venv..."
uv pip install vllm transformers torch --python .venv/bin/python 2>&1 | tail -20

echo "=== Setup complete at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee /workspace/indic_mio_install.log
