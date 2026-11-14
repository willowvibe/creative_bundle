"""Export skill models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PremiereOptions(BaseModel):
    """Optional Premiere MCP export settings."""

    enabled: bool = False
    project_name: str = ""


class AutoPosterOptions(BaseModel):
    """Optional auto-poster export settings."""

    enabled: bool = False
    platforms: list[str] = Field(default_factory=list)


class ExportStyle(BaseModel):
    """User-facing export configuration."""

    output_name: str = "final.mp4"
    report_name: str = "report.md"
    decision_log_name: str = "decision_log.jsonl"
    include_premiere: bool = False
    include_auto_poster: bool = False


class ExportManifest(BaseModel):
    """Manifest written by the export skill."""

    job_id: str
    style: ExportStyle
    input_video: str = ""
    final_video: str = ""
    report: str = ""
    decision_log: str = ""
    duration: float = 0.0
