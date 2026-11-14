"""Resolve transcription settings from config + environment.

``config.yaml`` has always had a ``transcription:`` section
(``engine``/``model``/``language``/``device``/``compute_type``), but nothing
read it — the code defaulted to ``small`` on CPU. This module is the single
place that turns that config (plus ``VE_WHISPER_*`` env overrides) into the
arguments the transcription wrappers use.

When CUDA is requested but unavailable (no GPU, or CTranslate2 has no CUDA
build), the settings degrade to a CPU-safe default (``cpu`` device, ``int8``
compute) so a job never fails just because the GPU is missing. Turbo-class
large models (``large-v3-turbo``, the ``turbo`` alias, and ``distil-*``
variants) are kept as configured: on CPU/int8 ``large-v3-turbo`` transcribed a
78 s narration in 71 s with every word, where ``small`` dropped a sentence. Plain
``large*`` checkpoints map to ``large-v3-turbo``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from video_editor_tools.gpu import cuda_available

logger = structlog.get_logger(__name__)

CPU_FALLBACK_MODEL = "small"
CPU_FALLBACK_COMPUTE = "int8"
CUDA_DEFAULT_COMPUTE = "float16"

# Plain large checkpoints map to the turbo variant on CPU; turbo-class models
# (``large-v3-turbo``, ``turbo``, ``distil-*``) and base/small/medium are kept
# as configured.
PLAIN_LARGE_MODELS = frozenset({"large", "large-v1", "large-v2", "large-v3"})
CPU_TURBO_MODEL = "large-v3-turbo"


@dataclass(frozen=True)
class TranscriptionSettings:
    """Resolved transcription configuration."""

    model: str
    language: str
    device: str
    compute_type: str


def _load_transcription_config() -> tuple[str, str, str, str]:
    """Return (model, language, device, compute_type) from ProjectConfig."""
    try:
        from video_editor_config import ProjectConfig

        transcription = ProjectConfig.load().transcription
        return (
            transcription.model,
            transcription.language,
            transcription.device,
            transcription.compute_type,
        )
    except Exception as exc:  # noqa: BLE001 - config is optional at runtime
        logger.debug("transcription_config_unavailable", error=str(exc))
        return (CPU_FALLBACK_MODEL, "en", "cpu", CPU_FALLBACK_COMPUTE)


def resolve_transcription_settings(
    config_path: Path | str | None = None,
) -> TranscriptionSettings:
    """Resolve model/language/device/compute_type from config + env.

    Precedence: ``VE_WHISPER_*`` env var > ``config.yaml`` ``transcription:``
    block > built-in defaults. A requested CUDA device is downgraded to CPU
    when no CUDA device is available; in that fallback plain ``large*``
    models map to ``large-v3-turbo`` and other models are kept as configured.
    """
    model, language, device, compute_type = _load_transcription_config()
    model = os.getenv("VE_WHISPER_MODEL") or model
    language = os.getenv("VE_WHISPER_LANGUAGE") or language
    device = os.getenv("VE_WHISPER_DEVICE") or device
    compute_type = os.getenv("VE_WHISPER_COMPUTE_TYPE") or compute_type

    if device.startswith("cuda") and not cuda_available():
        # Turbo-class large models stay accurate on CPU/int8 at about real time
        # (measured: 71 s for a 78 s narration, where ``small`` dropped a whole
        # sentence), so keep them as configured; plain ``large*`` checkpoints
        # map to the turbo variant.
        if model in PLAIN_LARGE_MODELS:
            model = CPU_TURBO_MODEL
        logger.info(
            "transcription_gpu_unavailable_fallback",
            requested_device=device,
            fallback_device="cpu",
            model=model,
        )
        device = "cpu"
        compute_type = CPU_FALLBACK_COMPUTE

    if not compute_type:
        compute_type = CUDA_DEFAULT_COMPUTE if device.startswith("cuda") else CPU_FALLBACK_COMPUTE

    return TranscriptionSettings(
        model=model,
        language=language,
        device=device,
        compute_type=compute_type,
    )


def transcribe(
    audio_path: Path | str,
    output_path: Path | str,
    *,
    model: str | None = None,
    language: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> dict[str, Any]:
    """Transcribe audio using the resolved settings — the single ASR entry point.

    Any argument overrides the resolved value; omitted arguments fall back to
    ``config.yaml`` ``transcription:`` / ``VE_WHISPER_*`` / built-in defaults.
    Replaces the retired WhisperX CLI wrapper.
    """
    from video_editor_transcribe.whisper_local import transcribe_audio

    settings = resolve_transcription_settings()
    logger.info(
        "transcribe_dispatch",
        model=model or settings.model,
        device=device or settings.device,
        compute_type=compute_type or settings.compute_type,
    )
    return transcribe_audio(
        audio_path,
        output_path,
        model_size=model or settings.model,
        language=language if language is not None else settings.language,
        device=device or settings.device,
        compute_type=compute_type or settings.compute_type,
    )
