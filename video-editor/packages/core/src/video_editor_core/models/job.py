"""Engine-level job models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SourceMedia(BaseModel):
    """A source media file discovered during ingest."""

    file_id: str
    path: str
    proxy_path: str | None = None
    audio_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
