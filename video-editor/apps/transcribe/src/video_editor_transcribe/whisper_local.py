"""faster-whisper wrapper with word-level output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

# faster_whisper doesn't ship type stubs (no py.typed marker) and the
# project doesn't vendor types for it. Suppress the import-untyped
# warning; the runtime is correct and the public API is small.
from faster_whisper import WhisperModel

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "base"

# Bug #87: cache loaded WhisperModel instances so multiple transcription
# calls in the same process (e.g. a worker draining many jobs) do not
# repeatedly reload the model from disk. The key is (model_size, device,
# compute_type).
_MODEL_CACHE: dict[tuple[str, str, str], WhisperModel] = {}


def _load_model(
    model_size: str = DEFAULT_MODEL,
    device: str | None = None,
    compute_type: str | None = None,
) -> WhisperModel:
    """Load a faster-whisper model, reusing a cached instance when available.

    ``compute_type`` defaults from the device: ``float16`` on CUDA, ``int8`` on
    CPU. Callers that resolved config-driven settings pass it explicitly.
    """
    device = device or "cpu"
    compute_type = compute_type or ("float16" if device.startswith("cuda") else "int8")
    cache_key = (model_size, device, compute_type)
    if cache_key in _MODEL_CACHE:
        logger.info("whisper_model_cache_hit", model=model_size, device=device)
        return _MODEL_CACHE[cache_key]

    logger.info(
        "loading_whisper_model",
        model=model_size,
        device=device,
        compute_type=compute_type,
    )
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    _MODEL_CACHE[cache_key] = model
    return model


def transcribe_audio(
    audio_path: Path | str,
    output_path: Path | str,
    model_size: str = DEFAULT_MODEL,
    language: str | None = "en",
    word_timestamps: bool = True,
    device: str | None = None,
    compute_type: str | None = None,
    initial_prompt: str | None = None,
    vad_filter: bool = True,
) -> dict[str, Any]:
    """Transcribe audio and write a JSON transcript with word timestamps.

    ``initial_prompt`` primes the decoder with words the audio is expected to
    contain (a proper noun, a URL); ``vad_filter=False`` stops Silero VAD from
    trimming a quiet tail. Both matter for synthetic narration: a VAD-dropped
    final sentence leaves the caller aligning text that has no timings (#268).
    """
    audio_path = Path(audio_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    model = _load_model(model_size, device=device, compute_type=compute_type)
    options: dict[str, Any] = {
        "language": language,
        "word_timestamps": word_timestamps,
        "vad_filter": vad_filter,
    }
    if initial_prompt is not None:
        # Only when asked for, so a caller (or a test double) with the older
        # signature keeps working.
        options["initial_prompt"] = initial_prompt
    segments, info = model.transcribe(str(audio_path), **options)

    result = {
        "source_audio": str(audio_path),
        "language": info.language,
        "language_probability": info.language_probability,
        "word_timestamps": word_timestamps,
        "segments": [],
        "words": [],
    }

    for segment in segments:
        seg_dict = {
            "id": segment.id,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
            "words": [],
        }
        if segment.words:
            for word in segment.words:
                word_dict = {
                    "word": word.word.strip(),
                    "start": word.start,
                    "end": word.end,
                    "probability": word.probability,
                }
                seg_dict["words"].append(word_dict)
                result["words"].append(word_dict)
        result["segments"].append(seg_dict)

    with open(output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    logger.info("transcription_complete", output=str(output), segments=len(result["segments"]))
    return result
