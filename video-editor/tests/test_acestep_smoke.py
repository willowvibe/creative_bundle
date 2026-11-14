"""Smoke tests for the ACE-Step 1.5 music-generation API on the Vast.ai pod.

These are integration tests: they require a live ``acestep-api`` server reachable
at ``ACESTEP_BASE_URL`` (default ``http://127.0.0.1:8001``). The same URL is used
locally through the SSH tunnel described in
``memory/acestep-1.5-pod-setup.md``.

Run with:

    uv run pytest -m integration tests/test_acestep_smoke.py -v

Skip (the default for normal CI) with:

    uv run pytest -m "not integration"
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
DEFAULT_MODEL = os.environ.get("ACESTEP_MODEL", "acestep-v15-turbo")
SMOKE_TIMEOUT_S = int(os.environ.get("ACESTEP_SMOKE_TIMEOUT", "180"))


def _is_pod_up(base_url: str, timeout: float = 3.0) -> bool:
    try:
        r = requests.get(base_url, timeout=timeout)
    except requests.RequestException:
        return False
    # ACE-Step root returns 404; any HTTP response means the server is bound.
    return r.status_code < 600


def _decode_wav(audio_obj: object) -> tuple[bytes, int, int, int]:
    """Return (wav_bytes, nchannels, framerate, nframes) from an ACE-Step audio part."""
    if isinstance(audio_obj, list):
        if not audio_obj:
            raise AssertionError("audio list is empty")
        first = audio_obj[0]
        if not isinstance(first, dict) or "audio_url" not in first:
            raise AssertionError(f"unexpected audio part shape: {first!r}")
        url = first["audio_url"]["url"]
    elif isinstance(audio_obj, dict) and "audio_url" in audio_obj:
        url = audio_obj["audio_url"]["url"]
    elif isinstance(audio_obj, dict) and "data" in audio_obj:
        url = audio_obj["data"]
    elif isinstance(audio_obj, str):
        url = audio_obj
    else:
        raise AssertionError(f"unsupported audio object: {audio_obj!r}")

    if not url.startswith("data:audio"):
        raise AssertionError(f"audio is not a data: URL: {url[:60]!r}")
    b64 = url.split(",", 1)[1]
    wav = base64.b64decode(b64)
    with wave.open(BytesIO(wav), "rb") as w:
        return wav, w.getnchannels(), w.getframerate(), w.getnframes()


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_pod() -> None:
    if not _is_pod_up(ACESTEP_BASE_URL):
        pytest.skip(f"ACE-Step API not reachable at {ACESTEP_BASE_URL}")


def test_root_responds() -> None:
    r = requests.get(ACESTEP_BASE_URL, timeout=5)
    # Root may legitimately 404; what matters is that the port answered.
    assert r.status_code < 600, f"unexpected root status: {r.status_code}"


def test_models_endpoint_lists_turbo() -> None:
    """``/v1/models`` should expose acestep-v15-turbo after lazy init."""
    r = requests.get(f"{ACESTEP_BASE_URL}/v1/models", timeout=10)
    if r.status_code == 404:
        pytest.skip("/v1/models not present in this ACE-Step build")
    assert r.ok, f"models endpoint failed: {r.status_code} {r.text[:200]}"
    payload = r.json()
    names: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and "id" in entry:
                names.append(entry["id"])
            elif isinstance(entry, dict) and "name" in entry:
                names.append(entry["name"])
    assert any("turbo" in n.lower() for n in names), f"turbo not in model list: {names}"


def test_instrumental_generation_returns_wav(tmp_path: Path) -> None:
    """End-to-end: a short instrumental generation returns a decodable WAV."""
    r = requests.post(
        f"{ACESTEP_BASE_URL}/v1/chat/completions",
        json={
            "model": DEFAULT_MODEL,
            "messages": [{"role": "user", "content": "test ping, short ambient pad"}],
            "audio_config": {"duration": 5, "instrumental": True, "format": "wav"},
            "sample_mode": False,
        },
        timeout=SMOKE_TIMEOUT_S,
    )
    assert r.ok, f"generation request failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    choices = body.get("choices") or []
    assert choices, f"no choices in response: {body!r}"
    audio = choices[0]["message"]["audio"]
    wav, ch, sr, frames = _decode_wav(audio)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", f"bad WAV header: {wav[:12]!r}"
    assert sr > 0, f"non-positive framerate: {sr}"
    assert ch in (1, 2), f"unexpected channel count: {ch}"
    assert frames > 0, "empty audio"
    # Sanity: ~5 s at the returned sample-rate.
    duration = frames / sr
    assert 1.0 <= duration <= 30.0, f"duration {duration}s outside expected range"

    # Persist for human inspection if a sink dir is set.
    sink = os.environ.get("ACESTEP_SMOKE_OUT_DIR")
    if sink:
        Path(sink).mkdir(parents=True, exist_ok=True)
        (Path(sink) / "instrumental_5s.wav").write_bytes(wav)
