"""Models for the requirements verification gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DurationRange(BaseModel):
    """Target duration range in seconds."""

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _validate_range(self) -> DurationRange:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) cannot exceed max ({self.max})")
        return self


class ContentConstraints(BaseModel):
    """Content-level requirements that may need transcript/plan inspection."""

    must_mention: list[str] = Field(default_factory=list)
    must_keep_segments: list[str] = Field(default_factory=list)
    remove_filler_words: bool = False


class RequirementsSpec(BaseModel):
    """Structured requirements captured at job intake."""

    job_id: str = ""
    target_duration_range: DurationRange | None = None
    aspect_ratio: str = ""
    required_elements: list[str] = Field(default_factory=list)
    forbidden_elements: list[str] = Field(default_factory=list)
    style_preset: str = ""
    content_constraints: ContentConstraints = Field(default_factory=ContentConstraints)
    judge_checklist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_something_to_check(self) -> RequirementsSpec:
        if not any(
            [
                self.target_duration_range is not None,
                self.aspect_ratio,
                self.required_elements,
                self.forbidden_elements,
                self.style_preset,
                self.content_constraints.must_mention,
                self.content_constraints.must_keep_segments,
                self.content_constraints.remove_filler_words,
                self.judge_checklist,
            ]
        ):
            raise ValueError("requirements spec must contain at least one check")
        return self


class VerificationCheck(BaseModel):
    """Result of a single requirement check."""

    requirement: str
    result: Literal["PASS", "FAIL", "UNSURE"]
    actual: Any = None
    reason: str = ""


class VerificationReport(BaseModel):
    """Aggregated verification report for a rendered video."""

    job_id: str
    overall: Literal["PASS", "FAIL", "NEEDS_REVIEW"]
    checks: list[VerificationCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_overall(self) -> VerificationReport:
        results = {c.result for c in self.checks}
        if "FAIL" in results:
            self.overall = "FAIL"
        elif "UNSURE" in results:
            self.overall = "NEEDS_REVIEW"
        else:
            self.overall = "PASS"
        return self


def overall_from_checks(checks: list[VerificationCheck]) -> Literal["PASS", "FAIL", "NEEDS_REVIEW"]:
    """Derive an overall verdict from individual checks."""
    results = {c.result for c in checks}
    if "FAIL" in results:
        return "FAIL"
    if "UNSURE" in results:
        return "NEEDS_REVIEW"
    return "PASS"
