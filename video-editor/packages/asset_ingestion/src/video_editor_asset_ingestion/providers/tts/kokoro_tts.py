"""Kokoro local/free text-to-speech provider.

Kokoro is an open-weights TTS model that runs locally via the ``kokoro`` Python
package. It requires no cloud API key and is suitable for offline voiceover or
for cutting prototype cost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.base import GenerativeProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    has_break_tags,
    synthesize_with_breaks,
)

logger = structlog.get_logger(__name__)

DEFAULT_VOICE = "af_bella"
DEFAULT_LANG_CODE = "a"
DEFAULT_SPEED = 1.0


def _parse_voice_id(voice_id: str) -> str:
    """Strip a ``kokoro:`` prefix if present."""
    if voice_id.startswith("kokoro:"):
        return voice_id.split(":", 1)[1]
    return voice_id


def _lang_code_for_request(language: str | None) -> str:
    """Map an ISO-ish language code to a Kokoro pipeline language code.

    Kokoro ``KPipeline`` uses single-letter codes for pipeline variants.
    We only map the common cases; unknown languages fall back to the
    American-English pipeline so the provider stays usable.
    """
    if not language:
        return DEFAULT_LANG_CODE
    lang = language.lower().strip()
    # Kokoro pipeline codes (as of the public package): a, b, j, z, e, f, h, i, p, …
    mapping = {
        "en": "a",
        "en-us": "a",
        "en-gb": "b",
        "ja": "j",
        "zh": "z",
        "es": "e",
        "fr": "f",
        "hi": "h",
        "it": "i",
        "pt": "p",
    }
    return mapping.get(lang, DEFAULT_LANG_CODE)


class KokoroTTSProvider(GenerativeProvider):
    """Generate TTS audio locally with Kokoro.

    The ``kokoro`` package is imported lazily inside :meth:`generate` so that
    environments without it do not crash at import time. Callers that want to
    use Kokoro must install the package separately (``pip install kokoro``) and
    ensure the required voicepack is downloaded.
    """

    provider_name = "kokoro_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or ProviderConfig()
        self.voice = self.config.default_voice or DEFAULT_VOICE
        self.lang_code = self.config.provider_options.get("lang_code", DEFAULT_LANG_CODE)
        self.speed = float(self.config.provider_options.get("speed", DEFAULT_SPEED))

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        """Synthesize speech locally and write it to ``output_path``."""
        try:
            # Lazy imports keep the engine usable when Kokoro is not installed.
            import kokoro  # type: ignore[import-not-found]
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError(
                "Kokoro TTS requires the 'kokoro' package and its dependencies "
                "(torch, soundfile). Install with: pip install kokoro soundfile"
            ) from exc

        if has_break_tags(request.prompt):
            return await synthesize_with_breaks(self, request, output_path)

        voice_id = _parse_voice_id(request.params.get("voice_id") or self.voice)
        lang_code = _lang_code_for_request(request.params.get("language") or self.lang_code)
        speed = float(request.params.get("speed", self.speed))
        text = request.prompt

        logger.info(
            "kokoro_tts_request",
            job_id=request.job_id,
            asset_id=request.asset_id,
            voice_id=voice_id,
            lang_code=lang_code,
            speed=speed,
            text_preview=text[:80],
        )

        pipeline = kokoro.KPipeline(lang_code=lang_code)
        generator = pipeline(text, voice=voice_id, speed=speed, split_pattern=r"\n+")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Kokoro returns a generator of (graphemes, phonemes, audio_tensor).
        # Collect all chunks and concatenate into a single waveform.
        np = kokoro.np
        chunks: list[Any] = []
        for _graphemes, _phonemes, audio in generator:
            if audio is None:
                continue
            # Convert torch tensor (or list) to a 1-D numpy float32 array.
            arr = np.asarray(audio)
            if arr.ndim > 1:
                arr = arr.squeeze()
            chunks.append(arr)

        if not chunks:
            raise RuntimeError("Kokoro produced no audio chunks")

        waveform = chunks[0] if len(chunks) == 1 else np.concatenate(chunks)

        # Kokoro sample rate is exposed on the pipeline.
        sample_rate = getattr(pipeline, "sr", 24000)
        sf.write(str(output_path), waveform, sample_rate)

        info = probe_media(output_path)
        duration_ms = info.duration_ms()

        logger.info(
            "kokoro_tts_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
            voice_id=voice_id,
        )
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            scene_id=request.scene_id,
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "voice_id": voice_id,
                "lang_code": lang_code,
                "speed": speed,
            },
        )
