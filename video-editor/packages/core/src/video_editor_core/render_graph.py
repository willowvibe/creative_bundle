"""The render graph: a single declarative description of a job's final render.

Skills are *plan contributors*: instead of each materialising its own
intermediate ``with_*.mp4`` by re-encoding, they append to a ``RenderGraph``
(cuts from the EDL, overlay ``Layer``s, audio ``AudioTrack``s). A compiler then
turns the graph into renderer commands — see ``compile_ffmpeg`` and
``compile_hyperframes`` — so a polished job costs one or two encodes instead of
four to nine.

This module holds the data model only; it never shells out.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from video_editor_ffmpeg.render_options import RenderOptions

from video_editor_core.ticks import frames_for_ticks, seconds_to_ticks

LayerKind = Literal["video", "text", "image", "shape", "logo"]
AudioKind = Literal["voiceover", "music", "sfx"]

# Timeline gaps smaller than this are treated as contiguous. Cuts derived from
# a kept-region EDL are contiguous by construction, but float rounding and
# hand-authored graphs can introduce dust.
_TIMELINE_EPSILON = 1e-3


class Rect(BaseModel):
    """A pixel rectangle on the output canvas."""

    x: int = 0
    y: int = 0
    width: int
    height: int

    @model_validator(mode="after")
    def _validate(self) -> Rect:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"rect width/height must be > 0, got {self.width}x{self.height}")
        if self.x < 0 or self.y < 0:
            raise ValueError(f"rect origin must be >= 0, got ({self.x}, {self.y})")
        return self


class Timing(BaseModel):
    """When a layer is visible, in timeline seconds.

    ``end=None`` means "until the end of the composition".
    """

    start: float = 0.0
    end: float | None = None

    @model_validator(mode="after")
    def _validate(self) -> Timing:
        if self.start < 0:
            raise ValueError(f"timing.start must be >= 0, got {self.start}")
        if self.end is not None and self.end <= self.start:
            raise ValueError(f"timing.end must be > start, got end={self.end}, start={self.start}")
        return self


class Cut(BaseModel):
    """A kept segment, mapped onto the output timeline."""

    source_start: float
    source_end: float
    timeline_start: float
    timeline_end: float
    label: str = ""

    @model_validator(mode="after")
    def _validate(self) -> Cut:
        if self.source_start < 0:
            raise ValueError(f"source_start must be >= 0, got {self.source_start}")
        if self.source_end <= self.source_start:
            raise ValueError(
                f"source_end must be > source_start, got "
                f"end={self.source_end}, start={self.source_start}"
            )
        if self.timeline_end <= self.timeline_start:
            raise ValueError(
                f"timeline_end must be > timeline_start, got "
                f"end={self.timeline_end}, start={self.timeline_start}"
            )
        return self

    @property
    def duration(self) -> float:
        return self.timeline_end - self.timeline_start

    @property
    def timeline_start_ticks(self) -> int:
        """Timeline start on the integer tick base."""
        return seconds_to_ticks(self.timeline_start)

    @property
    def timeline_end_ticks(self) -> int:
        """Timeline end on the integer tick base."""
        return seconds_to_ticks(self.timeline_end)

    @property
    def duration_ticks(self) -> int:
        """Duration in ticks (end minus start, so no drift accumulates)."""
        return self.timeline_end_ticks - self.timeline_start_ticks


class Layer(BaseModel):
    """A visual overlay timed on the output timeline.

    ``rect`` is the destination rectangle. ``source`` is a media path for
    ``video``/``image``/``logo``; ``text`` carries content for ``text``.
    ``style`` is intentionally free-form (font, colour, border, animation)
    because the HyperFrames and FFmpeg compilers consume different keys.
    """

    kind: LayerKind
    name: str
    rect: Rect
    timing: Timing = Field(default_factory=Timing)
    style: dict[str, object] = Field(default_factory=dict)
    source: str = ""
    text: str = ""

    @model_validator(mode="after")
    def _validate(self) -> Layer:
        if self.kind in {"video", "image", "logo"} and not self.source:
            raise ValueError(f"layer {self.name!r} ({self.kind}) requires a source")
        return self


class AudioTrack(BaseModel):
    """An extra audio bus mixed over the cut's original audio."""

    kind: AudioKind
    source: str
    start: float = 0.0
    gain_db: float = 0.0
    # ``duck`` lowers this track while speech is present (music beds). The
    # compiler sidechain-compresses it against the cut's speech audio.
    duck: bool = False
    loop: bool = False

    @model_validator(mode="after")
    def _validate(self) -> AudioTrack:
        if not self.source:
            raise ValueError(f"audio track {self.kind} requires a source")
        if self.start < 0:
            raise ValueError(f"audio start must be >= 0, got {self.start}")
        return self


class RenderGraph(BaseModel):
    """Declarative description of everything needed to render one job."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    job_id: str
    width: int
    height: int
    fps: int = 30
    source: str
    has_audio: bool = True
    # Routing hint: "auto" lets the composition router decide, "ffmpeg" forces
    # the single-pass FFmpeg renderer, "hyperframes" requests the HTML renderer.
    render_preference: Literal["auto", "ffmpeg", "hyperframes"] = "auto"
    # Compositing colour space for FFmpeg overlays. "gamma" (the default) keeps
    # FFmpeg's historical gamma / limited-range ``overlay=`` blend, so existing
    # renders are byte-identical. "linear" opts into a linear-light float
    # composite of image/logo/video overlays (text stays in gamma); it assumes
    # a bt709-tagged source and is only applied when such an overlay exists.
    color_mode: Literal["gamma", "linear"] = "gamma"
    cuts: list[Cut] = Field(default_factory=list)
    overlays: list[Layer] = Field(default_factory=list)
    audio: list[AudioTrack] = Field(default_factory=list)
    options: RenderOptions = Field(default_factory=RenderOptions)

    @model_validator(mode="after")
    def _validate(self) -> RenderGraph:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"canvas must be positive, got {self.width}x{self.height}")
        if self.fps <= 0:
            raise ValueError(f"fps must be > 0, got {self.fps}")
        if not self.source:
            raise ValueError("render graph requires a source")
        if not self.cuts:
            raise ValueError("render graph requires at least one cut")
        self._validate_cuts()
        return self

    def _validate_cuts(self) -> None:
        ordered = sorted(self.cuts, key=lambda c: c.timeline_start)
        for prev, current in zip(ordered, ordered[1:], strict=False):
            if current.timeline_start < prev.timeline_end - _TIMELINE_EPSILON:
                raise ValueError(
                    f"cuts overlap on the timeline: {prev.timeline_end} > {current.timeline_start}"
                )
            if current.timeline_start > prev.timeline_end + _TIMELINE_EPSILON:
                raise ValueError(
                    f"cuts are not contiguous on the timeline: gap "
                    f"{prev.timeline_end} -> {current.timeline_start}"
                )
        if ordered[0].timeline_start > _TIMELINE_EPSILON:
            raise ValueError(f"timeline must start at 0, got {ordered[0].timeline_start}")

    @property
    def duration(self) -> float:
        return self.cuts[-1].timeline_end

    @property
    def emits_audio(self) -> bool:
        """True when the render has an audio track to output.

        ``has_audio`` only says whether the *source* carries audio; a
        video-only source with voiceover/music/SFX buses still emits audio
        (mixed over a silent bed).
        """
        return self.has_audio or bool(self.audio)

    @property
    def duration_ticks(self) -> int:
        """Total graph duration on the integer tick base."""
        return seconds_to_ticks(self.duration)

    def frame_count(self, *, mode: str = "round") -> int:
        """Total output frames at ``self.fps``, computed drift-free on ticks."""
        return frames_for_ticks(self.duration_ticks, self.fps, mode=mode)

    def cuts_contiguous_exact(self) -> bool:
        """True when the cuts meet exactly on the tick grid (no float dust).

        Unlike :meth:`_validate_cuts`, which tolerates ``_TIMELINE_EPSILON`` of
        float slop, this compares the tick-quantised boundaries for equality —
        useful when a caller needs a provably gap-free, drift-free timeline.
        """
        ordered = self.ordered_cuts()
        if seconds_to_ticks(ordered[0].timeline_start) != 0:
            return False
        for prev, current in zip(ordered, ordered[1:], strict=False):
            if seconds_to_ticks(current.timeline_start) != seconds_to_ticks(prev.timeline_end):
                return False
        return True

    def ordered_cuts(self) -> list[Cut]:
        return sorted(self.cuts, key=lambda c: c.timeline_start)

    def overlays_of(self, kind: LayerKind) -> list[Layer]:
        return [layer for layer in self.overlays if layer.kind == kind]

    def audio_of(self, kind: AudioKind) -> list[AudioTrack]:
        return [track for track in self.audio if track.kind == kind]


def edl_to_cuts(edl: object) -> list[Cut]:
    """Convert a ``video_editor_core.timeline.EDL`` into graph ``Cut``s.

    Typed loosely (``object``) to avoid a hard import cycle at module load;
    the EDL is duck-typed via its ``segments`` attribute.
    """
    segments = getattr(edl, "segments", None)
    if segments is None:
        raise TypeError("edl_to_cuts expects an EDL-like object with .segments")
    return [
        Cut(
            source_start=float(seg.source_start),
            source_end=float(seg.source_end),
            timeline_start=float(seg.timeline_start),
            timeline_end=float(seg.timeline_end),
            label=str(getattr(seg, "label", "")),
        )
        for seg in segments
    ]
