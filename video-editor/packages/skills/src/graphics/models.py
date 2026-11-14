"""Pydantic models for the graphics skill."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Rect(BaseModel):
    x: int = 0
    y: int = 0
    width: int
    height: int


class Layer(BaseModel):
    type: Literal["video", "text", "image", "shape", "logo"]
    name: str
    rect: Rect
    timing: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    text: str = ""


class SegmentComposition(BaseModel):
    segment_id: str
    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    duration: float
    width: int
    height: int
    layers: list[Layer] = Field(default_factory=list)


class CompositionPlan(BaseModel):
    job_id: str
    preset_name: str
    width: int
    height: int
    fps: int
    source_video: str
    segments: list[SegmentComposition] = Field(default_factory=list)


class Preset(BaseModel):
    name: str
    description: str = ""
    aspect_ratio: str
    width: int
    height: int
    fps: int = 30
    layout: dict[str, Rect]
    captions: dict[str, Any]
    branding: dict[str, Any]


class Brand(BaseModel):
    brand: str
    colors: dict[str, str]
    fonts: dict[str, str]
    logo: dict[str, Any]
    defaults: dict[str, Any]


def merge_preset_with_brand(preset: Preset, brand: Brand) -> Preset:
    """Overlay brand defaults onto a preset without mutating it."""
    merged_captions = {**preset.captions}
    merged_branding = {**preset.branding}

    if "font" not in merged_captions and "captions" in brand.fonts:
        merged_captions["font"] = brand.fonts["captions"]
    if "font_color" not in merged_captions and "text" in brand.colors:
        merged_captions["font_color"] = brand.colors["text"]
    if "accent_color" not in merged_branding and "primary" in brand.colors:
        merged_branding["accent_color"] = brand.colors["primary"]

    return Preset(
        name=preset.name,
        description=preset.description,
        aspect_ratio=preset.aspect_ratio,
        width=preset.width,
        height=preset.height,
        fps=preset.fps,
        layout=preset.layout,
        captions=merged_captions,
        branding=merged_branding,
    )
