"""ElevenLabs forced-alignment provider.

Aligns a known transcript text to an audio file via
``POST /v1/forced-alignment``. The endpoint accepts a
multipart form with the audio file and the text, plus
optional ``enabled_speech_timestamps`` (a JSON list of
words to align at character-level granularity) and a
``with_alignment`` boolean.

The response is JSON:

    {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5,
             "alignment": {"character_start": 0, "character_end": 5, "characters": [...]}},
            ...
        ],
        "characters": [{"text": "h", "start": 0.0, "end": 0.05}, ...],
        "transcript": "hello world",
        "language": "en"
    }

We write the response JSON to ``output_path`` (``output_ext``
should be ``json``) and stash the parsed payload in
``AssetResult.metadata`` so downstream consumers (e.g. the
CaptionsSkill) can pick it up without re-parsing the file.

Cost: free (ElevenLabs creator tier, 2026).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import structlog

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.elevenlabs_policy import ensure_voice_only

logger = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io"


class ElevenLabsForcedAlignmentProvider(GenerativeProvider):
    """Align a known transcript to an audio file via ``/v1/forced-alignment``."""

    provider_name = "elevenlabs_forced_alignment"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.base_url = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = self.config.api_key()
        self.timeout = self.config.timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Multipart form upload: do NOT set Content-Type here —
            # httpx will fill in the correct multipart boundary.
            headers: dict[str, str] = {}
            if self.api_key:
                headers["xi-api-key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """POST the audio + text to ElevenLabs and write the alignment JSON.

        ``request.params`` must contain ``audio_path`` (the audio
        file to align against) and ``text`` (the known transcript).
        The text can also be supplied as ``request.prompt`` for
        symmetry with other providers — we prefer ``params['text']``
        when both are present.
        """
        ensure_voice_only(self.provider_name)
        if not self.api_key:
            raise RuntimeError("ElevenLabs API key is not configured.")

        audio_path_str = request.params.get("audio_path")
        if not audio_path_str:
            raise ValueError("ElevenLabsForcedAlignmentProvider requires params['audio_path'].")
        text = request.params.get("text") or request.prompt
        if not text:
            raise ValueError(
                "ElevenLabsForcedAlignmentProvider requires params['text'] "
                "or request.prompt to be non-empty."
            )

        audio_path = Path(audio_path_str)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        with_alignment = bool(request.params.get("with_alignment", True))
        enabled_timestamps = request.params.get("enabled_speech_timestamps")

        # Build the multipart form. ``enabled_speech_timestamps`` is
        # expected as a JSON string in the form field, per the
        # ElevenLabs API docs.
        form_data: dict[str, str] = {"text": text, "with_alignment": str(with_alignment).lower()}
        if enabled_timestamps is not None:
            form_data["enabled_speech_timestamps"] = json.dumps(enabled_timestamps)

        with open(audio_path, "rb") as audio_file:
            files = {"file": (audio_path.name, audio_file, "application/octet-stream")}

            client = await self._get_client()
            logger.info(
                "elevenlabs_alignment_request",
                job_id=request.job_id,
                asset_id=request.asset_id,
                audio_path=str(audio_path),
                text_chars=len(text),
            )

            response = await client.post(
                "/v1/forced-alignment",
                data=form_data,
                files=files,
            )
        response.raise_for_status()

        # Parse and validate the response shape before writing.
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Forced-alignment response was not valid JSON: {exc}") from exc

        words = payload.get("words", [])
        if not isinstance(words, list) or not words:
            raise RuntimeError(f"Forced-alignment response missing 'words' array: {payload!r}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Compute the audio duration from the last word's end time.
        # We avoid probing the audio file because the user may pass
        # any audio format and we don't want to require ffmpeg here.
        last_word_end = float(words[-1].get("end", 0.0))
        duration_ms = int(last_word_end * 1000)

        logger.info(
            "elevenlabs_alignment_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            word_count=len(words),
            char_count=len(payload.get("characters", [])),
            duration_ms=duration_ms,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.ALIGNMENT,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "word_count": len(words),
                "char_count": len(payload.get("characters", [])),
                "language": payload.get("language", ""),
                "transcript_chars": len(text),
                "alignment": payload,  # full response, so downstream can pick it up
            },
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ElevenLabsForcedAlignmentProvider:
        await self._get_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
