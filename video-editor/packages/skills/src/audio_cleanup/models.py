"""Pydantic models for the audio cleanup skill."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AudioCleanupConfig(BaseModel):
    """User-facing options for the cleanup stage."""

    isolate_voice: bool = True
    denoise: bool = True
    normalize: bool = True
    loudness_target_lufs: float = -16.0
    true_peak_dbfs: float = -1.5
    loudness_range_lu: float = 11.0


class AudioCleanupManifest(BaseModel):
    """Metadata written after the cleanup stage completes."""

    job_id: str
    input_audio: str
    cleaned_audio: str
    config: AudioCleanupConfig
    stages_applied: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
