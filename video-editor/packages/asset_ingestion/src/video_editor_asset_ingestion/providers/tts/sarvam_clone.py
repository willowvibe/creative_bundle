"""Manage Sarvam Instant Voice Cloning voices (#312).

Thin wrapper around the documented endpoints: create from a reference clip,
list workspace voices, delete one. Generation lives in ``SarvamTTSProvider``
under the ``sarvam_clone:<voice_id>`` prefix.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from video_editor_asset_ingestion.providers.tts.sarvam_tts import (
    API_KEY_ENVS,
    DEFAULT_BASE_URL,
)

__all__ = [
    "MAX_REFERENCE_BYTES",
    "SarvamCloneError",
    "create_voice",
    "delete_voice",
    "list_voices",
]

MAX_REFERENCE_BYTES = 50 * 1024 * 1024


class SarvamCloneError(RuntimeError):
    """Voice-clone management could not complete."""


def _api_key() -> str:
    key = next((value for name in API_KEY_ENVS if (value := os.getenv(name))), None)
    if not key:
        raise SarvamCloneError("SARVAM_API is not set")
    return key


def _payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    return nested if isinstance(nested, dict) else data


async def create_voice(
    audio_path: Path | str,
    *,
    name: str,
    language: str = "en-IN",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Upload a 10–15 s reference clip and return ``voice_id`` + transcript."""
    path = Path(audio_path)
    if not path.is_file():
        raise SarvamCloneError(f"reference audio not found: {path}")
    size = path.stat().st_size
    if size > MAX_REFERENCE_BYTES:
        raise SarvamCloneError(
            f"reference clip is {size} bytes; the limit is {MAX_REFERENCE_BYTES}"
        )
    owns = client is None
    http = client or httpx.AsyncClient(base_url=DEFAULT_BASE_URL, timeout=120.0)
    try:
        with path.open("rb") as handle:
            # Sarvam answers 400 to a generic octet-stream part; send the audio type.
            content_type = mimetypes.guess_type(path.name)[0] or "audio/wav"
            files = {"file": (path.name, handle, content_type)}
            data = {"name": name, "language": language}
            response = await http.post(
                "/voices/create",
                files=files,
                data=data,
                headers={"api-subscription-key": _api_key()},
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SarvamCloneError(f"voice creation failed: {exc}") from exc
    finally:
        if owns:
            await http.aclose()

    payload = _payload(response.json() or {})
    voice_id = payload.get("voice_id") or payload.get("id")
    if not voice_id:
        raise SarvamCloneError(f"voice creation returned no voice_id: {payload}")
    return {
        "voice_id": str(voice_id),
        "reference_text": str(payload.get("reference_text") or ""),
        "name": name,
        "language": language,
    }


async def list_voices(*, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """List the cloned voices in the workspace."""
    owns = client is None
    http = client or httpx.AsyncClient(base_url=DEFAULT_BASE_URL, timeout=30.0)
    try:
        response = await http.get("/voices", headers={"api-subscription-key": _api_key()})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SarvamCloneError(f"voice list failed: {exc}") from exc
    finally:
        if owns:
            await http.aclose()
    payload = _payload(response.json() or {})
    items = payload.get("items") or payload.get("voices") or []
    return [dict(item) for item in items if isinstance(item, dict)]


async def delete_voice(voice_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Delete a cloned voice and free its slot."""
    owns = client is None
    http = client or httpx.AsyncClient(base_url=DEFAULT_BASE_URL, timeout=30.0)
    try:
        response = await http.delete(
            f"/voices/delete/{voice_id}",
            headers={"api-subscription-key": _api_key()},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SarvamCloneError(f"voice delete failed: {exc}") from exc
    finally:
        if owns:
            await http.aclose()
