"""Edit plan models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class Cut(BaseModel):
    """A keep segment in the final edit."""

    start: float
    end: float
    reason: str = ""

    @model_validator(mode="after")
    def _validate_geometry(self) -> Cut:
        if self.start < 0:
            raise ValueError(f"cut start must be >= 0, got {self.start}")
        if self.end <= self.start:
            raise ValueError(f"cut end must be > start, got end={self.end}, start={self.start}")
        return self


class BRollInsert(BaseModel):
    """A b-roll insertion request."""

    start: float
    end: float
    description: str = ""
    asset_query: str = ""
    provider: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    output_ext: str = "mp4"

    @model_validator(mode="after")
    def _validate_geometry(self) -> BRollInsert:
        if self.start < 0:
            raise ValueError(f"broll start must be >= 0, got {self.start}")
        if self.end <= self.start:
            raise ValueError(f"broll end must be > start, got end={self.end}, start={self.start}")
        return self


class CaptionStyle(BaseModel):
    font_size: int = 48
    font_color: str = "white"
    border_width: int = 4
    position: str = "bottom"

    @field_validator("position")
    @classmethod
    def _validate_position(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"top", "bottom", "center"}
        if value not in allowed:
            raise ValueError(f"caption position must be one of {allowed}, got {value!r}")
        return value


class EditPlan(BaseModel):
    """LLM-proposed edit plan."""

    job_id: str
    cuts: list[Cut] = Field(default_factory=list)
    broll_inserts: list[BRollInsert] = Field(default_factory=list)
    caption_style: CaptionStyle = Field(default_factory=CaptionStyle)
    summary: str = ""

    @model_validator(mode="after")
    def _validate_cuts(self) -> EditPlan:
        cuts = self.cuts
        if not cuts:
            return self
        for i, cut in enumerate(cuts):
            if i > 0 and cut.start < cuts[i - 1].end:
                raise ValueError(
                    f"overlapping cuts: cut {i} starts at {cut.start} before cut {i - 1} ends at {cuts[i - 1].end}"
                )
        return self
