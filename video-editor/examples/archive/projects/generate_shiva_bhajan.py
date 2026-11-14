#!/usr/bin/env python3
"""Generate a 9-verse Shiva bhajan (शिवं शङ्करं शम्भु मीशानमीडे) using ACE-Step 1.5 + RagaLoRA.

Produces two tracks on the Vast.ai pod (via SSH tunnel at 127.0.0.1:8001):

1. ``shiva_bhajan_vocal_30s.wav``  — deep male Carnatic-style vocal reciting the verses.
2. ``shiva_bhajan_instrumental_30s.wav`` — pure instrumental version (tanpura + mridangam).

Then splits each into 4 stems (vocals / drums / bass / other) using Demucs so
the user can remix the vocal stem against a different backing track.

Run from the laptop (the SSH tunnel must already be active):

    python3 scripts/generate_shiva_bhajan.py

Outputs land in /tmp/shiva_bhajan/ on the local machine.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import time
import wave
from io import BytesIO
from pathlib import Path

import requests

ACESTEP_URL = os.environ.get("ACESTEP_BASE_URL", "http://127.0.0.1:8001")
MODEL = os.environ.get("ACESTEP_MODEL", "acestep-v15-turbo")
OUT_DIR = Path(os.environ.get("OUT_DIR", "/tmp/shiva_bhajan"))
POD_TMP = "/tmp/shiva_bhajan"  # on the pod, used for Demucs split
DURATION_S = int(os.environ.get("DURATION_S", "30"))


# -- ACE-Step API helpers -----------------------------------------------------


def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    r = requests.post(f"{ACESTEP_URL}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _decode_audio(message: dict) -> bytes:
    audio = message["audio"]
    if isinstance(audio, list):
        url = audio[0]["audio_url"]["url"]
    elif isinstance(audio, dict) and "audio_url" in audio:
        url = audio["audio_url"]["url"]
    else:
        raise ValueError(f"unexpected audio shape: {audio!r}")
    b64 = url.split(",", 1)[1]
    return base64.b64decode(b64)


def _wav_meta(wav: bytes) -> dict[str, int | float]:
    with wave.open(BytesIO(wav), "rb") as w:
        return {
            "channels": w.getnchannels(),
            "framerate": w.getframerate(),
            "nframes": w.getnframes(),
            "duration_s": round(w.getnframes() / w.getframerate(), 3),
            "bytes": len(wav),
        }


# -- LoRA + generation --------------------------------------------------------


def ensure_ragalora(scale: float = 0.85) -> None:
    """Make sure RagaLoRA is loaded at the requested scale, idempotently."""
    # Clean slate
    requests.post(f"{ACESTEP_URL}/v1/lora/unload", timeout=10)
    r = requests.post(
        f"{ACESTEP_URL}/v1/lora/load",
        json={
            "lora_path": "/workspace/ACE-Step-1.5/checkpoints/loras/raga-indian",
            "adapter_name": "raga-indian",
        },
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"LoRA load failed: {r.status_code} {r.text[:200]}")
    s = requests.post(
        f"{ACESTEP_URL}/v1/lora/scale",
        json={"adapter_name": "raga-indian", "scale": scale},
        timeout=10,
    )
    s.raise_for_status()
    print(f"  [lora] raga-indian loaded @ scale={scale}")


def generate(prompt: str, vocal: bool, duration: int, tag: str) -> bytes:
    """Run one generation through ACE-Step and return the decoded WAV bytes."""
    print(f"  [gen] {tag}: {prompt[:90]}...")
    t0 = time.monotonic()
    resp = _post(
        "/v1/chat/completions",
        {
            "model": MODEL,
            # ACE-Step only accepts the "user" role; instrumental is toggled
            # by audio_config.instrumental, not by the role name.
            "messages": [{"role": "user", "content": prompt}],
            "audio_config": {
                "duration": duration,
                "instrumental": not vocal,
                "format": "wav",
            },
            "sample_mode": False,
        },
        timeout=300,
    )
    elapsed = time.monotonic() - t0
    wav = _decode_audio(resp["choices"][0]["message"])
    meta = _wav_meta(wav)
    print(f"  [gen] {tag}: {meta['bytes']/1024:.1f} KB, "
          f"{meta['duration_s']}s, {meta['framerate']} Hz, {meta['channels']} ch "
          f"({elapsed:.1f}s wall)")
    return wav


# -- Prompts ------------------------------------------------------------------

# Style brief shared by both tracks. Concrete and Carnatic-grammar-aware so
# RagaLoRA's style transfer has something to latch onto.
STYLE_COMMON = (
    "South Indian Carnatic devotional bhajan, "
    "Sanskrit Vedic verses, "
    "raga Shivashakti or Shankarabharanam, "
    "adi talam, slow tempo (~70 BPM), "
    "tanpura drone, mridangam, nadaswaram, "
    "temple atmosphere, reverent, meditative"
)

VOCAL_PROMPT = (
    f"Deep male Carnatic vocalist singing the Shiva stotram "
    f"\"shivam shankaram shambhu mishanamide\" as a 9-verse pallavi-structured bhajan, "
    f"{STYLE_COMMON}, "
    f"with gamakas, steady shruti, end of each verse on the refrain"
)

INSTRUMENTAL_PROMPT = (
    f"Pure instrumental Carnatic bhajan, no vocals, "
    f"{STYLE_COMMON}, "
    f"tanpura + mridangam + nadaswaram playing the pallavi refrain in a cycle"
)


# -- Demucs stem split --------------------------------------------------------


def split_stems(local_wav: Path, tag: str) -> dict[str, Path]:
    """Push WAV to pod, run demucs, pull 4 stems back. Returns {stem: path}."""
    remote_in = f"{POD_TMP}/{tag}_in.wav"
    remote_out = f"{POD_TMP}/demucs_out"
    remote_track = f"{remote_out}/htdemucs/{tag}_in"

    ssh = ["ssh", "-p", "30532", "-i", str(Path.home() / ".ssh/id_ed25519"),
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           "root@ssh7.vast.ai"]

    # Make sure the remote working dir exists.
    subprocess.run(
        ssh + [f"mkdir -p {POD_TMP}"],
        check=True, capture_output=True, text=True,
    )
    # Push
    subprocess.run(
        ["scp", "-P", "30532", "-i", str(Path.home() / ".ssh/id_ed25519"),
         "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         str(local_wav), f"root@ssh7.vast.ai:{remote_in}"],
        check=True,
    )
    # Run demucs
    cmd = (
        f"cd /workspace/ACE-Step-1.5 && source .venv/bin/activate && "
        f"mkdir -p {remote_out} && "
        f"demucs --device cuda --out {remote_out} --name htdemucs --jobs 1 {remote_in}"
    )
    subprocess.run(ssh + [cmd], check=True, capture_output=True, text=True)
    # Pull stems
    stems: dict[str, Path] = {}
    for stem in ("vocals", "drums", "bass", "other"):
        remote = f"{remote_track}/{stem}.wav"
        local = OUT_DIR / f"{tag}_{stem}.wav"
        subprocess.run(
            ["scp", "-P", "30532", "-i", str(Path.home() / ".ssh/id_ed25519"),
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             f"root@ssh7.vast.ai:{remote}", str(local)],
            check=True,
        )
        stems[stem] = local
    return stems


# -- Main ---------------------------------------------------------------------


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"== ACE-Step {MODEL} @ {ACESTEP_URL}")
    print(f"== duration={DURATION_S}s, scale=0.85")
    print(f"== output → {OUT_DIR}\n")

    print("[1/3] Loading RagaLoRA adapter")
    ensure_ragalora(scale=0.85)

    print("\n[2/3] Generating vocal + instrumental tracks")
    vocal_wav = generate(VOCAL_PROMPT, vocal=True, duration=DURATION_S, tag="vocal")
    instr_wav = generate(INSTRUMENTAL_PROMPT, vocal=False, duration=DURATION_S, tag="instr")

    vocal_path = OUT_DIR / "shiva_bhajan_vocal_30s.wav"
    instr_path = OUT_DIR / "shiva_bhajan_instrumental_30s.wav"
    vocal_path.write_bytes(vocal_wav)
    instr_path.write_bytes(instr_wav)
    print(f"  saved → {vocal_path}")
    print(f"  saved → {instr_path}")

    print("\n[3/3] Demucs stem split on pod")
    print("  vocals...")
    split_stems(vocal_path, "vocal")
    print("  instrumental...")
    split_stems(instr_path, "instr")

    # Final summary
    print("\n=== Files ===")
    for p in sorted(OUT_DIR.iterdir()):
        if p.suffix == ".wav":
            with wave.open(str(p), "rb") as w:
                dur = w.getnframes() / w.getframerate()
            print(f"  {p.name:42s} {p.stat().st_size/1024:8.1f} KB  {dur:6.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
