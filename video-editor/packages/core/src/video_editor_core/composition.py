"""The single intermediate representation behind every HyperFrames render.

A :class:`Composition` is what an adapter produces and what
:func:`video_editor_core.compile_composition.compile_composition` consumes.
Times are absolute timeline seconds; rects are canvas pixels, never
scene-relative. Scenes are a compile-time grouping only — every visual layer
is emitted as a root-level clip — so a later modular emitter can change the
output without changing this model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from video_editor_core.designs import DesignTokens
from video_editor_core.motion import PRIMITIVES
from video_editor_core.motion.spec import DEFAULT_EASE
from video_editor_core.render_graph import Rect

LayerKind = Literal["media", "text", "rich_text", "shape", "svg", "html"]

_ASPECT_CANVAS: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}


def canvas_for_aspect(aspect_ratio: str | None) -> tuple[int, int]:
    """Canvas pixels for an aspect ratio; unknown ratios fall back to 16:9."""
    return _ASPECT_CANVAS.get((aspect_ratio or "16:9").strip(), (1920, 1080))


def quantise(seconds: float, fps: int) -> float:
    """Snap a time to the frame grid so CSS time and encoded frames agree."""
    return round(float(seconds) * fps) / fps


class Canvas(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)


class LayerTime(BaseModel):
    start: float = Field(ge=0)
    end: float | None = None

    @model_validator(mode="after")
    def _window(self) -> LayerTime:
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"layer time ends at {self.end:.3f} but starts at {self.start:.3f}")
        return self


class WordSpan(BaseModel):
    """One caption word, timed relative to its clip's ``data-start``."""

    text: str
    start: float = 0.0
    end: float = 0.0
    highlight: bool = False

    @model_validator(mode="after")
    def _highlight_is_not_emitted(self) -> WordSpan:
        # The field exists so a caller can express per-word emphasis, but
        # ``compile_composition`` gives every word its highlight from the
        # layer's ``highlight_color`` and never reads this flag. Accepting a
        # True here would silently drop the caller's intent, so reject it
        # until the per-word emphasis lands.
        if self.highlight:
            raise ValueError(
                f"word span {self.text!r}: per-word highlight is not emitted "
                "until slice 2; set the layer's highlight_color instead"
            )
        return self


class MotionSpec(BaseModel):
    name: str
    params: dict[str, float | str] = Field(default_factory=dict)
    delay: float = Field(default=0.0, ge=0)
    duration: float | None = Field(default=None, gt=0)
    ease: str = DEFAULT_EASE


class LayerContent(BaseModel):
    """Exactly one of these is meaningful per layer kind."""

    text: str = ""  # text | html | placeholder
    spans: list[WordSpan] = Field(default_factory=list)  # rich_text
    source: str = ""  # media
    gradient: str = ""  # shape / scrim
    path: str = ""  # svg
    view_box: str = "0 0 100 100"  # svg
    alt: str = ""  # media accessibility text


class CompositionLayer(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    kind: LayerKind
    preset: str = (
        ""  # chip | pill | scrim | end_card | caption_line | spotlight | placeholder | base
    )
    rect: Rect
    time: LayerTime
    content: LayerContent = Field(default_factory=LayerContent)
    style: dict[str, Any] = Field(default_factory=dict)
    motions: list[MotionSpec] = Field(default_factory=list)
    media_kind: Literal["image", "video"] | None = None
    track: int = 20
    z: int = 0

    @model_validator(mode="after")
    def _media_is_complete(self) -> CompositionLayer:
        if self.kind == "media":
            if not self.content.source:
                raise ValueError(f"media layer {self.id!r} has no source")
            if self.media_kind is None:
                raise ValueError(f"media layer {self.id!r} has no media_kind")
        return self


class CompositionScene(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    start: float
    end: float
    layers: list[CompositionLayer] = Field(default_factory=list)
    #: The entrance this scene asks for; ``scene_transitions.apply_scene_transitions``
    #: turns it into per-layer motions. The model keeps the field so an adapter
    #: can build the IR incrementally.
    transition_in: MotionSpec | None = None

    @model_validator(mode="after")
    def _window(self) -> CompositionScene:
        if self.end <= self.start:
            raise ValueError(
                f"scene {self.id!r} ends at {self.end:.3f} but starts at {self.start:.3f}"
            )
        return self


class AudioCue(BaseModel):
    id: str
    source: str
    start: float = 0.0
    duration: float | None = None
    track: int = 150
    volume: float = 1.0


class Composition(BaseModel):
    job_id: str
    canvas: Canvas
    duration: float = Field(gt=0)
    scenes: list[CompositionScene] = Field(default_factory=list)
    audio: list[AudioCue] = Field(default_factory=list)
    audio_mode: Literal["embed", "external"] = "embed"
    design: DesignTokens | None = None

    @model_validator(mode="after")
    def _scenes_are_contiguous(self) -> Composition:
        cursor = 0.0
        for scene in self.scenes:
            if abs(scene.start - cursor) > 1e-6:
                raise ValueError(
                    f"scene {scene.id!r} starts at {scene.start:.3f}, expected {cursor:.3f}"
                )
            cursor = scene.end
        if abs(cursor - self.duration) > 1e-6:
            raise ValueError(
                f"scenes cover {cursor:.3f}s but the composition duration is {self.duration:.3f}s"
            )
        return self

    @model_validator(mode="after")
    def _layers_are_valid(self) -> Composition:
        seen: set[str] = set()
        for scene in self.scenes:
            for layer in scene.layers:
                if layer.id in seen:
                    raise ValueError(f"duplicate layer id {layer.id!r}")
                seen.add(layer.id)
                start = layer.time.start
                end = layer.time.end
                if start > self.duration + 1e-6:
                    raise ValueError(
                        f"layer {layer.id!r} starts at {start:.3f}s past the "
                        f"composition duration {self.duration:.3f}s"
                    )
                if end is not None and end > self.duration + 1e-6:
                    raise ValueError(
                        f"layer {layer.id!r} ends at {end:.3f}s past the composition "
                        f"duration {self.duration:.3f}s"
                    )
                rect = layer.rect
                if (
                    rect.x < 0
                    or rect.y < 0
                    or rect.width <= 0
                    or rect.height <= 0
                    or rect.x + rect.width > self.canvas.width
                    or rect.y + rect.height > self.canvas.height
                ):
                    raise ValueError(
                        f"layer {layer.id!r} rect "
                        f"({rect.x},{rect.y},{rect.width},{rect.height}) is outside "
                        f"the {self.canvas.width}x{self.canvas.height} canvas"
                    )
        return self

    @model_validator(mode="after")
    def _motions_exist(self) -> Composition:
        for scene in self.scenes:
            for layer in scene.layers:
                for motion in layer.motions:
                    if motion.name not in PRIMITIVES:
                        raise ValueError(
                            f"unknown motion {motion.name!r} on layer {layer.id!r}; "
                            f"known: {', '.join(sorted(PRIMITIVES))}"
                        )
        return self
