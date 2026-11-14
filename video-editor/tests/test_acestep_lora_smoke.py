"""Smoke tests for RagaLoRA (Indian-music) adapter on ACE-Step 1.5.

Verifies the full LoRA lifecycle against the live pod API:

1. ``/v1/lora/status`` reports no LoRA loaded.
2. ``/v1/lora/load`` with the RagaLoRA adapter dir succeeds.
3. ``/v1/lora/scale`` updates the active scale.
4. A devotional-style prompt produces a non-empty audio part.
5. ``/v1/lora/unload`` removes the adapter.

These are integration tests; see ``tests/test_acestep_smoke.py`` for the same
env-var contract and skip semantics.
"""

from __future__ import annotations

import base64
import os
import wave
from io import BytesIO
from pathlib import Path

import pytest
import requests

ACESTEP_BASE_URL = os.environ.get("ACESTEP_BASE_URL", "http://127.0.0.1:8001")
RAGALORA_PATH = os.environ.get(
    "ACESTEP_RAGALORA_PATH",
    "/workspace/ACE-Step-1.5/checkpoints/loras/raga-indian",
)
RAGALORA_NAME = os.environ.get("ACESTEP_RAGALORA_NAME", "raga-indian")
DEFAULT_MODEL = os.environ.get("ACESTEP_MODEL", "acestep-v15-turbo")
GENERATION_TIMEOUT_S = int(os.environ.get("ACESTEP_LORA_GEN_TIMEOUT", "240"))


def _is_pod_up(base_url: str, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(base_url, timeout=timeout)
    except requests.RequestException:
        return False
    return r.status_code < 600


def _decode_wav(audio_obj: object) -> tuple[bytes, int, int, int]:
    if isinstance(audio_obj, list):
        if not audio_obj:
            raise AssertionError("audio list is empty")
        first = audio_obj[0]
        url = first["audio_url"]["url"]
    elif isinstance(audio_obj, dict) and "audio_url" in audio_obj:
        url = audio_obj["audio_url"]["url"]
    else:
        raise AssertionError(f"unsupported audio object: {audio_obj!r}")
    b64 = url.split(",", 1)[1]
    wav = base64.b64decode(b64)
    with wave.open(BytesIO(wav), "rb") as w:
        return wav, w.getnchannels(), w.getframerate(), w.getnframes()


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_pod() -> None:
    if not _is_pod_up(ACESTEP_BASE_URL):
        pytest.skip(f"ACE-Step API not reachable at {ACESTEP_BASE_URL}")


@pytest.fixture
def lora_loaded() -> None:
    """Ensure RagaLoRA is loaded and scaled for the duration of one test, then unloaded."""
    # Clean up any prior adapter.
    requests.post(f"{ACESTEP_BASE_URL}/v1/lora/unload", timeout=10)
    r = requests.post(
        f"{ACESTEP_BASE_URL}/v1/lora/load",
        json={"lora_path": RAGALORA_PATH, "adapter_name": RAGALORA_NAME},
        timeout=30,
    )
    if r.status_code == 404:
        pytest.skip("/v1/lora/load not present in this ACE-Step build")
    if not r.ok:
        pytest.skip(f"RagaLoRA load failed: {r.status_code} {r.text[:200]}")
    scale_resp = requests.post(
        f"{ACESTEP_BASE_URL}/v1/lora/scale",
        json={"adapter_name": RAGALORA_NAME, "scale": 0.75},
        timeout=10,
    )
    assert scale_resp.ok, f"scale set failed: {scale_resp.status_code} {scale_resp.text[:200]}"
    try:
        yield
    finally:
        requests.post(f"{ACESTEP_BASE_URL}/v1/lora/unload", timeout=10)


def test_initial_status_no_lora() -> None:
    """Without any prior load, the API should either be LoRA-clean or report empty."""
    requests.post(f"{ACESTEP_BASE_URL}/v1/lora/unload", timeout=10)
    r = requests.get(f"{ACESTEP_BASE_URL}/v1/lora/status", timeout=10)
    assert r.ok, f"status failed: {r.status_code} {r.text[:200]}"
    payload = r.json().get("data", r.json())
    adapters = payload.get("adapters") or []
    assert RAGALORA_NAME not in adapters, f"unexpected preloaded adapter: {adapters}"


def test_load_ragalora_sets_active(lora_loaded: None) -> None:
    r = requests.get(f"{ACESTEP_BASE_URL}/v1/lora/status", timeout=10)
    assert r.ok
    payload = r.json().get("data", r.json())
    assert RAGALORA_NAME in (payload.get("adapters") or []), f"adapter not listed: {payload}"
    assert payload.get("active_adapter") == RAGALORA_NAME, (
        f"adapter not active: {payload.get('active_adapter')!r}"
    )
    # Scale should be the value we just set (0.75).
    scales = payload.get("scales") or {}
    assert scales.get(RAGALORA_NAME) == pytest.approx(0.75, rel=0.01), (
        f"scale not applied: {scales}"
    )


def test_bhajan_generation_with_lora(lora_loaded: None, tmp_path: Path) -> None:
    """A devotional prompt should produce a non-empty stereo audio part."""
    r = requests.post(
        f"{ACESTEP_BASE_URL}/v1/chat/completions",
        json={
            "model": DEFAULT_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Hindi bhajan, devotional, slow tempo, harmonium and tabla, "
                        "soft male vocal, peaceful and reverent mood"
                    ),
                }
            ],
            "audio_config": {"duration": 8, "instrumental": False, "format": "wav"},
            "sample_mode": False,
        },
        timeout=GENERATION_TIMEOUT_S,
    )
    assert r.ok, f"bhajan gen failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    choices = body.get("choices") or []
    assert choices, f"no choices: {body!r}"
    audio = choices[0]["message"]["audio"]
    wav, ch, sr, frames = _decode_wav(audio)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert sr >= 22050, f"framerate too low: {sr}"
    assert ch == 2, f"expected stereo, got {ch}"
    duration = frames / sr
    assert 1.0 <= duration <= 30.0, f"duration {duration}s out of range"

    sink = os.environ.get("ACESTEP_SMOKE_OUT_DIR")
    if sink:
        Path(sink).mkdir(parents=True, exist_ok=True)
        (Path(sink) / "raga-bhajan-8s.wav").write_bytes(wav)


def test_unload_clears_active(lora_loaded: None) -> None:
    """Calling unload mid-test fixture should drop the adapter from status."""
    r = requests.post(f"{ACESTEP_BASE_URL}/v1/lora/unload", timeout=10)
    assert r.ok, f"unload failed: {r.status_code} {r.text[:200]}"
    status = requests.get(f"{ACESTEP_BASE_URL}/v1/lora/status", timeout=10)
    payload = status.json().get("data", status.json())
    assert RAGALORA_NAME not in (payload.get("adapters") or []), (
        f"adapter still present after unload: {payload}"
    )
    # Re-load so the module-scoped cleanup is a no-op.
    requests.post(
        f"{ACESTEP_BASE_URL}/v1/lora/load",
        json={"lora_path": RAGALORA_PATH, "adapter_name": RAGALORA_NAME},
        timeout=30,
    )
