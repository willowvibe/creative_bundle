"""Pydantic models for the ComfyStudio project file format.

These models follow the inferred schema from ComfyStudio v0.1.3
(``project.comfystudio`` JSON). They are intentionally additive: fields
unknown to this package are preserved via ``model_config = ConfigDict(extra='allow')``
so the bridge does not discard custom ComfyStudio data on import.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ComfyStudioClipTransform(BaseModel):
    """2-D transform attached to a clip."""

    model_config = ConfigDict(extra="allow")

    positionX: float = 0.0
    positionY: float = 0.0
    scaleX: float = 100.0
    scaleY: float = 100.0
    scaleLinked: bool = True
    rotation: float = 0.0
    anchorX: float = 0.0
    anchorY: float = 0.0
    opacity: float = 100.0
    flipH: bool = False
    flipV: bool = False
    cropTop: float = 0.0
    cropBottom: float = 0.0
    cropLeft: float = 0.0
    cropRight: float = 0.0
    blendMode: str = "normal"
    blur: float = 0.0


class ComfyStudioTextProperties(BaseModel):
    """Text clip properties (e.g. for captions)."""

    model_config = ConfigDict(extra="allow")

    text: str = ""
    fontFamily: str = "Noto Sans Devanagari"
    fontSize: int = 40
    alignment: str = "center"
    color: str = "#FFFFFF"
    strokeColor: str = "#000000"
    strokeWidth: int = 0
    backgroundColor: str = "#000000"
    backgroundOpacity: float = 0.55
    shadowColor: str = "#000000"
    shadowBlur: int = 0


class ComfyStudioKeyFrame(BaseModel):
    """A single keyframe entry."""

    model_config = ConfigDict(extra="allow")

    time: float = 0.0
    value: float | str = 0.0
    easing: str = "linear"


class ComfyStudioClip(BaseModel):
    """One clip on a ComfyStudio timeline track."""

    model_config = ConfigDict(extra="allow")

    id: str
    trackId: str
    type: str  # "video" | "image" | "audio" | "text"
    assetId: str | None = None
    name: str = ""
    startTime: float = 0.0
    duration: float = 0.0
    sourceDuration: float = 0.0
    trimStart: float = 0.0
    trimEnd: float = 0.0
    color: str = "#3B82F6"
    url: str = ""
    thumbnail: str = ""
    transform: ComfyStudioClipTransform = Field(default_factory=ComfyStudioClipTransform)
    textProperties: ComfyStudioTextProperties | None = None
    keyframes: dict[str, list[ComfyStudioKeyFrame]] = Field(default_factory=dict)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    speed: float = 1.0
    reverse: bool = False
    fadeIn: float = 0.0
    fadeOut: float = 0.0
    cacheStatus: str = "none"
    cacheProgress: float = 0.0
    cacheUrl: str = ""
    cachePath: str = ""


class ComfyStudioTrack(BaseModel):
    """A horizontal track inside a ComfyStudio timeline."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    type: str  # "video" | "audio"
    channels: str = "stereo"
    clips: list[ComfyStudioClip] = Field(default_factory=list)


class ComfyStudioTimeline(BaseModel):
    """One timeline inside a project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    resolution: dict[str, int]
    fps: int
    duration: float = 0.0
    tracks: list[ComfyStudioTrack] = Field(default_factory=list)


class ComfyStudioAsset(BaseModel):
    """A media asset referenced by the project."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str  # "video" | "image" | "audio" | "mask"
    name: str = ""
    url: str = ""
    thumbnail: str = ""
    folderId: str | None = None
    source: str = "IMP"  # imported; ComfyStudio also uses "AI" for generated.
    playbackCacheUrl: str = ""
    playbackCachePath: str = ""


class ComfyStudioProject(BaseModel):
    """Top-level ``project.comfystudio`` document."""

    model_config = ConfigDict(extra="allow")

    name: str
    version: str = "1.0"
    resolution: dict[str, int]
    fps: int
    assets: list[ComfyStudioAsset] = Field(default_factory=list)
    timelines: list[ComfyStudioTimeline] = Field(default_factory=list)
    folders: list[dict[str, Any]] = Field(default_factory=list)
    folderCounter: int = 1
