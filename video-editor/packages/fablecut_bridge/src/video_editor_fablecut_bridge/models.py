"""Pydantic models for the FableCut ``project.json`` file format.

These models follow the inferred schema from FableCut v1.5.0
(see https://github.com/ronak-create/FableCut). They are intentionally
additive: fields unknown to this package are preserved via
``model_config = ConfigDict(extra='allow')`` so the bridge does not discard
custom FableCut data on import.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FableCutKeyframe(BaseModel):
    """A single keyframe entry inside a clip's keyframes map."""

    model_config = ConfigDict(extra="allow")

    t: float = 0.0
    v: float | str = 0.0
    ease: str = "ease-in-out"


class FableCutTransition(BaseModel):
    """A head/tail transition on a clip."""

    model_config = ConfigDict(extra="allow")

    type: str  # fade, slide-left, wipe, zoom, iris, etc.
    duration: float


class FableCutProps(BaseModel):
    """Visual/audio/text properties attached to a clip.

    All props are optional in FableCut; defaults are applied by the editor.
    """

    model_config = ConfigDict(extra="allow")

    # Transform & compositing
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    blend: str = "normal"

    # Layout
    fit: str = "contain"
    cropL: float = 0.0
    cropR: float = 0.0
    cropT: float = 0.0
    cropB: float = 0.0
    cornerRadius: float = 0.0
    flipH: bool = False
    flipV: bool = False

    # Filter / color
    filterPreset: str = "none"
    brightness: float = 100.0
    contrast: float = 100.0
    saturation: float = 100.0
    hue: float = 0.0
    temperature: float = 0.0
    tint: float = 0.0
    blur: float = 0.0
    grayscale: float = 0.0
    sepia: float = 0.0
    invert: float = 0.0
    vignette: float = 0.0

    # Motion FX
    shake: float = 0.0
    shakeSpeed: float = 8.0
    rgbSplit: float = 0.0
    grain: float = 0.0

    # Keying
    chromaKey: str = ""
    chromaTolerance: float = 26.0
    chromaSoftness: float = 12.0
    bgRemove: bool = False

    # Audio / time
    volume: float = 1.0
    speed: float = 1.0

    # Text only
    text: str = "Title"
    fontSize: float = 72.0
    color: str = "#ffffff"
    color2: str = ""
    font: str = "Segoe UI"
    bold: bool = True
    weight: float = 0.0
    italic: bool = False
    uppercase: bool = False
    align: str = "center"
    letterSpacing: float = 0.0
    lineHeight: float = 1.2
    textShadow: float = 12.0
    glow: float = 0.0
    glowColor: str = ""
    strokeWidth: float = 0.0
    strokeColor: str = "#000000"
    bgColor: str = "#000000"
    bgOpacity: float = 0.0
    textAnim: str = "none"
    wordRate: float = 0.15
    fontCutSet: list[str] = Field(default_factory=list)


class FableCutMedia(BaseModel):
    """A media asset referenced by the project."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    kind: str  # video | audio | image | svg
    src: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None


class FableCutClip(BaseModel):
    """One clip on a FableCut timeline track."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    mediaId: str | None = None
    kind: str  # video | audio | image | svg | text | adjust
    track: str  # V1-V4 or A1-A3
    start: float = 0.0
    in_: float = Field(default=0.0, alias="in")
    duration: float = 0.0
    name: str = ""
    props: FableCutProps = Field(default_factory=FableCutProps)
    keyframes: dict[str, list[FableCutKeyframe]] = Field(default_factory=dict)
    transitionIn: FableCutTransition | None = None
    transitionOut: FableCutTransition | None = None


class FableCutMarker(BaseModel):
    """A beat/cue marker on the ruler."""

    model_config = ConfigDict(extra="allow")

    t: float
    label: str = ""


class FableCutProject(BaseModel):
    """Top-level FableCut ``project.json`` document."""

    model_config = ConfigDict(extra="allow")

    name: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    background: str = "#000000"
    revision: int = 1
    markers: list[FableCutMarker] = Field(default_factory=list)
    media: list[FableCutMedia] = Field(default_factory=list)
    clips: list[FableCutClip] = Field(default_factory=list)
