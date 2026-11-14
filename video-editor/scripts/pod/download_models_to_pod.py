#!/usr/bin/env python3
"""Reliable HTTP range-resume downloader for ComfyUI models on the Vast.ai pod."""

import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path("/workspace/ComfyUI/models")

MODELS = [
    # FLUX text encoders (enable native FLUX UNET workflows)
    {
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors",
        "dest": BASE_DIR / "clip" / "clip_l.safetensors",
        "size": "246 MB",
    },
    {
        "url": "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors",
        "dest": BASE_DIR / "clip" / "t5xxl_fp8_e4m3fn.safetensors",
        "size": "4.89 GB",
    },
    # Wan2.1 support files
    {
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "dest": BASE_DIR / "clip" / "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "size": "6.74 GB",
    },
    {
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors",
        "dest": BASE_DIR / "vae" / "wan_2.1_vae.safetensors",
        "size": "319 MB",
    },
    # Video diffusion models
    {
        "url": "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/diffusion_models/wan2.1_t2v_14B_fp8_e4m3fn.safetensors",
        "dest": BASE_DIR / "diffusion_models" / "Wan2.1" / "wan2.1_t2v_14B_fp8_e4m3fn.safetensors",
        "size": "14.3 GB",
    },
    {
        "url": "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled-fp8.safetensors",
        "dest": BASE_DIR / "checkpoints" / "LTXV" / "ltxv-2b-0.9.8-distilled-fp8.safetensors",
        "size": "4.46 GB",
    },
    # FLUX.1-dev fp8 (all-in-one, public mirror — no HF auth needed)
    {
        "url": "https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors",
        "dest": BASE_DIR / "checkpoints" / "FLUX1" / "flux1-dev-fp8.safetensors",
        "size": "17.2 GB",
    },
]

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB
TIMEOUT = 120


def download(item):
    url = item["url"]
    dest = Path(item["dest"])
    dest.parent.mkdir(parents=True, exist_ok=True)

    existing = dest.exists() and dest.stat().st_size or 0
    headers = {}
    if existing:
        headers["Range"] = f"bytes={existing}-"
        print(f"[resume] {dest.name} from {existing} bytes")
    else:
        print(f"[start]  {dest.name} -> {dest}")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            total = existing
            mode = "ab" if existing else "wb"
            with open(dest, mode) as f:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    print(f"  {dest.name}: {total / (1024**3):.2f} GB", end="\r", flush=True)
        print(f"\n[done]   {dest.name}: {total / (1024**3):.2f} GB")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 416 and existing:
            print(f"\n[done]   {dest.name} already complete ({existing} bytes)")
            return True
        print(f"\n[error]  {dest.name}: HTTP {e.code} {e.reason}")
        return False
    except Exception as e:  # noqa: BLE001 - keep the downloader alive
        print(f"\n[error]  {dest.name}: {e}")
        return False


def main():
    failed = []
    for item in MODELS:
        if not download(item):
            failed.append(item["dest"].name)

    print("\n--- Summary ---")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All downloads completed.")


if __name__ == "__main__":
    main()
