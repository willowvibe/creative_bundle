"""QA report models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QACheck(BaseModel):
    """Result of a single quality check."""

    name: str
    passed: bool
    value: Any = None
    threshold: Any = None
    message: str = ""


class QAReport(BaseModel):
    """Aggregated QA report for a rendered video."""

    job_id: str
    video_path: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool
    checks: list[QACheck]
    passed: bool
    warnings: list[str] = Field(default_factory=list)
    tool_versions: dict[str, str | None] = Field(default_factory=dict)


class QASelfEvalError(Exception):
    """Raised when a per-segment QA check fails at a cut boundary.

    Carries the failing segment ID, a ``reason`` (``"visual_jump"``,
    ``"audio_pop"``, or ``"measurements_incomplete"``), and the raw
    measurements that triggered the failure so the caller can include
    them in a report.
    """

    def __init__(
        self,
        segment_id: str,
        reason: str,
        psnr_db: float | None = None,
        peak_rms: float | None = None,
    ) -> None:
        self.segment_id = segment_id
        self.reason = reason
        self.psnr_db = psnr_db
        self.peak_rms = peak_rms
        super().__init__(
            f"QASelfEvalError(segment_id={segment_id!r}, reason={reason!r}, "
            f"psnr_db={psnr_db}, peak_rms={peak_rms})"
        )
