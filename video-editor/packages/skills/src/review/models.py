"""Models for the post-render review gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ReviewCheck(BaseModel):
    """Result of a single post-render review check."""

    name: str
    category: Literal["critical", "quality"]
    result: Literal["PASS", "FAIL", "UNSURE"]
    actual: Any = None
    threshold: Any = None
    reason: str = ""


class ReviewReport(BaseModel):
    """Aggregated post-render review report for a final exported video."""

    job_id: str
    video_path: str
    overall: Literal["PASS", "FAIL", "NEEDS_REVIEW"] = "PASS"
    checks: list[ReviewCheck] = Field(default_factory=list)
    passed: bool = True

    @model_validator(mode="after")
    def _derive_overall(self) -> ReviewReport:
        critical_results = {c.result for c in self.checks if c.category == "critical"}
        quality_results = {c.result for c in self.checks if c.category == "quality"}

        if "FAIL" in critical_results:
            self.overall = "FAIL"
            self.passed = False
        elif "FAIL" in quality_results or "UNSURE" in critical_results:
            self.overall = "NEEDS_REVIEW"
            self.passed = True
        else:
            self.overall = "PASS"
            self.passed = True
        return self


def overall_from_checks(checks: list[ReviewCheck]) -> Literal["PASS", "FAIL", "NEEDS_REVIEW"]:
    """Derive an overall verdict from individual review checks."""
    critical_results = {c.result for c in checks if c.category == "critical"}
    quality_results = {c.result for c in checks if c.category == "quality"}

    if "FAIL" in critical_results:
        return "FAIL"
    if "FAIL" in quality_results or "UNSURE" in critical_results:
        return "NEEDS_REVIEW"
    return "PASS"
