"""Asset request/result types shared by the core and asset ingestion layers."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AssetType(StrEnum):
    """Supported generative asset types."""

    TTS = "tts"
    IMAGE = "image"
    VIDEO = "video"
    DATA_VIZ = "data_viz"
    MUSIC = "music"
    SFX = "sfx"
    ALIGNMENT = "alignment"
    ISOLATION = "isolation"
    SPEECH_TO_SPEECH = "speech_to_speech"


class AssetResult(BaseModel):
    """Result of a successful asset generation, suitable for the renderer."""

    asset_id: str
    asset_type: AssetType
    local_path: str
    scene_id: str = ""
    duration_ms: int = 0
    cached: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("local_path")
    @classmethod
    def _posix_path(cls, value: str) -> str:
        """Normalize every stored path to POSIX so plans are portable."""
        return Path(value).resolve().as_posix()

    def resolved_path(self) -> Path:
        """Return a pathlib Path for local filesystem operations."""
        return Path(self.local_path)


class BatchResult(BaseModel):
    """Outcome of a batch of asset requests."""

    results: list[AssetResult] = Field(default_factory=list)
    failures: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def success_asset_ids(self) -> set[str]:
        return {r.asset_id for r in self.results}

    @property
    def failed_asset_ids(self) -> set[str]:
        return {asset_id for asset_id, _ in self.failures}
