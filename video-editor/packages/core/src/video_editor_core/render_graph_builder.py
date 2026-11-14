"""Build a :class:`RenderGraph` from pipeline artifacts.

Skills contribute graph fragments (cuts, overlay layers, audio buses) instead
of each rendering their own intermediate ``with_*.mp4``. The builder turns those
fragments — plus the existing ``graphics`` composition plan and the captions
skill's timed layers — into a single graph that the renderer executes once.

The builder is deliberately duck-typed against the ``graphics.CompositionPlan``
shape (``.segments -> .layers -> .rect/.timing``) so ``video_editor_core`` does
not depend on the skills package.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions

from video_editor_core.render_graph import (
    AudioTrack,
    Cut,
    Layer,
    LayerKind,
    Rect,
    RenderGraph,
    Timing,
    edl_to_cuts,
)

logger = structlog.get_logger(__name__)

# Overlay kinds that the graphics planner emits as the segment's base video;
# the graph already carries the cut video, so these layers are skipped.
_BASE_VIDEO_NAMES = {"base_video", "video"}


class RenderGraphBuilder:
    """Fluent builder for a :class:`RenderGraph`."""

    def __init__(
        self,
        job_id: str,
        *,
        width: int,
        height: int,
        fps: int = 30,
        source: str,
        cuts: list[Cut],
        has_audio: bool = True,
        options: RenderOptions | None = None,
    ) -> None:
        self._job_id = job_id
        self._width = width
        self._height = height
        self._fps = fps
        self._source = source
        self._cuts = cuts
        self._has_audio = has_audio
        self._options = options or RenderOptions()
        self._render_preference: Literal["auto", "ffmpeg", "hyperframes"] = "auto"
        self._overlays: list[Layer] = []
        self._audio: list[AudioTrack] = []

    # -- constructors ----------------------------------------------------
    @classmethod
    def from_source(
        cls,
        job_id: str,
        source: Path | str,
        *,
        width: int,
        height: int,
        fps: int = 30,
        duration: float | None = None,
        has_audio: bool = True,
        options: RenderOptions | None = None,
    ) -> RenderGraphBuilder:
        """Build over a single source video treated as one full cut."""
        source = str(source)
        if duration is None:
            info = probe_media(source)
            duration = info.duration or 0.0
            has_audio = info.has_audio if info.has_audio is not None else has_audio
        cut = Cut(
            source_start=0.0,
            source_end=duration,
            timeline_start=0.0,
            timeline_end=duration,
            label="full",
        )
        return cls(
            job_id,
            width=width,
            height=height,
            fps=fps,
            source=source,
            cuts=[cut],
            has_audio=has_audio,
            options=options,
        )

    @classmethod
    def from_edl(
        cls,
        job_id: str,
        edl: Any,
        *,
        width: int,
        height: int,
        fps: int = 30,
        has_audio: bool = True,
        options: RenderOptions | None = None,
    ) -> RenderGraphBuilder:
        """Build from an EDL (source-relative cuts)."""
        return cls(
            job_id,
            width=width,
            height=height,
            fps=fps,
            source=str(edl.source_path),
            cuts=edl_to_cuts(edl),
            has_audio=has_audio,
            options=options,
        )

    # -- contributions ---------------------------------------------------
    def add_layer(self, layer: Layer) -> RenderGraphBuilder:
        self._overlays.append(layer)
        return self

    def add_overlays(self, layers: Iterable[Layer]) -> RenderGraphBuilder:
        self._overlays.extend(layers)
        return self

    def add_audio(self, track: AudioTrack) -> RenderGraphBuilder:
        self._audio.append(track)
        return self

    def prefer_engine(self, engine: Literal["auto", "ffmpeg", "hyperframes"]) -> RenderGraphBuilder:
        """Set the graph's render-engine preference (``auto``/``ffmpeg``/``hyperframes``)."""
        self._render_preference = engine
        return self

    def extend_graphics_plan(self, plan: Any) -> RenderGraphBuilder:
        """Flatten a graphics ``CompositionPlan`` into timeline-absolute layers.

        Per-segment layers are re-timed onto the output timeline by adding the
        segment's ``timeline_start``. Base-video layers are skipped (the graph
        already carries the cut video).
        """
        for segment in getattr(plan, "segments", []):
            seg_start = float(getattr(segment, "timeline_start", 0.0))
            for layer in getattr(segment, "layers", []):
                layer_kind = getattr(layer, "kind", None) or getattr(layer, "type", "")
                if layer_kind == "video" and getattr(layer, "name", "") in _BASE_VIDEO_NAMES:
                    continue
                self._overlays.append(_graphics_layer_to_overlay(layer, seg_start))
        return self

    def build(self) -> RenderGraph:
        return RenderGraph(
            job_id=self._job_id,
            width=self._width,
            height=self._height,
            fps=self._fps,
            source=self._source,
            has_audio=self._has_audio,
            render_preference=self._render_preference,
            cuts=self._cuts,
            overlays=_uniquify_names(self._overlays),
            audio=self._audio,
            options=self._options,
        )


def _uniquify_names(layers: list[Layer]) -> list[Layer]:
    """Give duplicate overlay names a suffix.

    HyperFrames (and its lint) reject duplicate layer names, and captions emit
    many layers all named ``caption_line``.
    """
    used: dict[str, int] = {}
    out: list[Layer] = []
    for layer in layers:
        count = used.get(layer.name, 0)
        used[layer.name] = count + 1
        if count == 0:
            out.append(layer)
        else:
            out.append(layer.model_copy(update={"name": f"{layer.name}_{count}"}))
    return out


def _graphics_layer_to_overlay(layer: Any, segment_start: float) -> Layer:
    """Convert one graphics plan layer to a timeline-absolute graph overlay."""
    rect = getattr(layer, "rect", None)
    graph_rect = Rect(
        x=int(getattr(rect, "x", 0)),
        y=int(getattr(rect, "y", 0)),
        width=int(getattr(rect, "width", 1)),
        height=int(getattr(rect, "height", 1)),
    )
    timing = getattr(layer, "timing", None) or {}
    start = segment_start + float(timing.get("start") or 0.0)
    raw_end = timing.get("end")
    end = segment_start + float(raw_end) if raw_end is not None else None
    kind = getattr(layer, "kind", None) or getattr(layer, "type", "text")
    return Layer(
        kind=cast("LayerKind", kind),
        name=str(getattr(layer, "name", "overlay")),
        rect=graph_rect,
        timing=Timing(start=start, end=end),
        style=dict(getattr(layer, "style", {}) or {}),
        source=str(getattr(layer, "source", "") or ""),
        text=str(getattr(layer, "text", "") or ""),
    )
