"""Stub forced-alignment provider for tests and offline demos.

Mirrors the ``StubTTSProvider`` / ``StubSFXProvider`` pattern: takes
the audio path + text and writes a synthetic alignment JSON with
evenly-spaced word timings. The result is not phonetically accurate
(it assumes a constant 0.5s-per-word speaking rate) but the JSON
shape matches ElevenLabs' real response, so the rest of the pipeline
can be tested without an API key.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider

logger = structlog.get_logger(__name__)

DEFAULT_SECONDS_PER_WORD = 0.5


class StubForcedAlignmentProvider(GenerativeProvider):
    """Generate a placeholder alignment JSON for offline alignment tests."""

    provider_name = "stub_alignment"

    def __init__(self, seconds_per_word: float = DEFAULT_SECONDS_PER_WORD):
        self.seconds_per_word = seconds_per_word

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Write a synthetic alignment JSON to ``output_path``."""
        text = request.params.get("text") or request.prompt
        if not text:
            raise ValueError(
                "StubForcedAlignmentProvider requires params['text'] or request.prompt."
            )

        # Build the same shape ElevenLabs returns: words with
        # start/end timestamps, characters with start/end, and
        # the full transcript string. We don't include the
        # `enabled_speech_timestamps` markers — that field is
        # only relevant to the real API.
        words_list = text.split()
        words_payload: list[dict[str, object]] = []
        characters_payload: list[dict[str, object]] = []
        char_offset = 0

        for idx, word in enumerate(words_list):
            start = idx * self.seconds_per_word
            end = start + self.seconds_per_word
            words_payload.append(
                {
                    "text": word,
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "alignment": {
                        "character_start": char_offset,
                        "character_end": char_offset + len(word),
                        "characters": [
                            {
                                "text": ch,
                                "start": round(start + (i * self.seconds_per_word) / len(word), 3),
                                "end": round(
                                    start + ((i + 1) * self.seconds_per_word) / len(word), 3
                                ),
                            }
                            for i, ch in enumerate(word)
                        ],
                    },
                }
            )
            char_offset += len(word) + 1  # +1 for the space between words
            # Emit the space as a character gap (not in the response,
            # but helpful for downstream character-level consumers).
            for ch in word:
                characters_payload.append(
                    {"text": ch, "start": 0.0, "end": 0.0}  # not used in stub
                )

        payload = {
            "words": words_payload,
            "characters": characters_payload,
            "transcript": text,
            "language": "en",
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        duration_ms = int(len(words_list) * self.seconds_per_word * 1000) if words_list else 0

        logger.info(
            "stub_alignment_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            word_count=len(words_list),
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.ALIGNMENT,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "word_count": len(words_list),
                "synthetic": True,
                "alignment": payload,
            },
        )
