"""Media-related models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class IngestManifest(BaseModel):
    """Output of the ingest stage."""

    job_id: str
    sources: list[dict[str, Any]] = []
    audio_extracted: bool = False
