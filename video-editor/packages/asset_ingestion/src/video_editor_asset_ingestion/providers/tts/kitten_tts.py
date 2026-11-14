"""KittenTTS local text-to-speech provider.

KittenTTS is an ultra-lightweight TTS model (15M params, ~25 MB on disk)
that runs on CPU with ONNX. Apache 2.0 licensed. 24 kHz mono output.

Install: ``pip install kittentts``

The published package (``kittentts`` 0.1.x) exports ``KittenTTS.generate()``,
which returns a float32 NumPy array. Older builds exported a ``TTS`` class
whose ``synthesize()`` returned encoded bytes and used language-coded voice
names (``en_us_female_1``). Both shapes are accepted so an existing script or
provider config keeps working after the package upgrade.
"""

from __future__ import annotations

import re
import wave
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

DEFAULT_VOICE = "expr-voice-2-f"
DEFAULT_SAMPLE_RATE = 24000

# Voices shipped with the 0.1.x nano model.
_VOICES = frozenset(
    {f"expr-voice-{number}-{gender}" for number in (2, 3, 4, 5) for gender in ("m", "f")}
)

# Language-coded aliases from older builds. They still name a real voice, so
# keep accepting them; the model is English-only, so the Japanese and Korean
# aliases are not mapped and fail with a clear error instead of silently
# speaking English with a "ja" label.
_LEGACY_VOICES = {
    "en_us_female_1": "expr-voice-2-f",
    "en_us_female_2": "expr-voice-3-f",
    "en_us_male_1": "expr-voice-2-m",
    "en_us_male_2": "expr-voice-3-m",
    "en_gb_female_1": "expr-voice-4-f",
    "en_gb_male_1": "expr-voice-4-m",
}


def _resolve_voice(voice: str) -> str:
    """Map a legacy voice alias to the model's current voice id."""
    if voice in _VOICES:
        return voice
    mapped = _LEGACY_VOICES.get(voice)
    if mapped is not None:
        return mapped
    supported = ", ".join(sorted(_VOICES))
    raise RuntimeError(
        f"unknown KittenTTS voice {voice!r}; the installed model supports: {supported}"
    )


#: The nano model's ONNX graph fails with "invalid expand shape" once a single
#: request grows past roughly 400 characters. Split well below that so an
#: ordinary paragraph is still spoken in one breath, not one sentence at a time.
MAX_CHUNK_CHARS = 320

#: Silence between synthesized chunks, so two sentences do not run together.
CHUNK_GAP_SECONDS = 0.12


def _split_for_model(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split narration into sentence-sized chunks the nano model accepts.

    Sentence boundaries are preferred; a single over-long sentence is split on
    whitespace. Every chunk is non-empty and at most ``limit`` characters, and
    the concatenation preserves every word in order.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= limit:
        return [stripped]

    chunks: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", stripped):
        pieces = sentence.split() if len(sentence) > limit else [sentence]
        for piece in pieces:
            if len(piece) > limit:
                # A single "word" longer than the limit (a URL, say): hard split.
                current = _flush(chunks, current)
                chunks.extend(piece[i : i + limit] for i in range(0, len(piece), limit))
                continue
            candidate = f"{current} {piece}".strip()
            if len(candidate) > limit:
                current = _flush(chunks, current)
            current = f"{current} {piece}".strip() if current else piece
    _flush(chunks, current)
    return chunks


def _flush(chunks: list[str], current: str) -> str:
    """Append ``current`` if it holds text and reset the accumulator."""
    if current:
        chunks.append(current)
    return ""


def _write_wav(path: Path, samples: Any, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
    """Write a float32/float64 mono waveform as 16-bit PCM WAV.

    The published model returns unnormalised floats; clipping (rather than
    peak-normalising) preserves the relative loudness the model intended.
    """
    import numpy as np

    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    if array.size == 0:
        raise RuntimeError("KittenTTS returned no audio samples")
    pcm = (np.clip(array, -1.0, 1.0) * 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


class KittenTTSProvider(GenerativeProvider):
    """Generate speech locally via KittenTTS ONNX inference."""

    provider_name = "kitten_tts"

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig()
        self._voice = self._config.provider_options.get("voice", DEFAULT_VOICE)
        self._speed = float(self._config.provider_options.get("speed", 1.0))

    async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
        text = request.prompt
        voice = request.params.get("voice_id") or self._voice
        language = request.params.get("language") or "en"

        if has_break_tags(request.prompt):
            return await synthesize_with_breaks(self, request, output_path)

        try:
            import kittentts
        except ImportError as exc:
            raise RuntimeError(
                "kittentts package not installed. Install with: pip install kittentts"
            ) from exc

        voice = _resolve_voice(voice)

        if hasattr(kittentts, "TTS"):
            # kittentts < 0.1: encoded bytes from a per-voice instance.
            tts = kittentts.TTS(voice=voice)
            audio = tts.synthesize(text, speed=self._speed)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio)
        else:
            # kittentts >= 0.1: one model instance, a NumPy waveform out.
            try:
                from kittentts import KittenTTS
            except ImportError as exc:
                raise RuntimeError(
                    "kittentts does not export TTS or KittenTTS; "
                    "install a supported release with: pip install kittentts"
                ) from exc
            import numpy as np

            tts = KittenTTS()
            chunks = _split_for_model(text)
            if not chunks:
                raise RuntimeError("KittenTTS received no text to speak")
            gap = np.zeros(int(DEFAULT_SAMPLE_RATE * CHUNK_GAP_SECONDS), dtype=np.float32)
            pieces: list[Any] = []
            for index, chunk in enumerate(chunks):
                if index:
                    pieces.append(gap)
                pieces.append(tts.generate(chunk, voice=voice, speed=self._speed))
            samples = np.concatenate([np.asarray(piece, dtype=np.float32) for piece in pieces])
            _write_wav(output_path, samples)

        duration_ms = 0
        try:
            info = probe_media(output_path)
            duration_ms = info.duration_ms()
        except Exception:  # noqa: BLE001, S110 - optional voice metadata probe
            pass

        logger.info(
            "kitten_tts_complete",
            job_id=request.job_id,
            asset_id=request.asset_id,
            path=str(output_path),
            duration_ms=duration_ms,
            voice=voice,
        )

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            scene_id=request.scene_id,
            duration_ms=duration_ms,
            metadata={
                "provider": self.provider_name,
                "voice": voice,
                "language": language,
                "text_length": len(text),
            },
        )
