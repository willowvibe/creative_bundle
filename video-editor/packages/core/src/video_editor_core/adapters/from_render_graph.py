"""Adapt a :class:`RenderGraph` into a :class:`Composition`.

The render graph is the engine's cut + overlay description for a finished
base video. This adapter is the historical home of the HyperFrames overlay
compiler; keeping it separate means the compiler never learns what a
``RenderGraph`` is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    WordSpan,
    quantise,
)
from video_editor_core.render_graph import Layer, Rect, RenderGraph

logger = structlog.get_logger(__name__)

OVERLAY_TRACK_BASE = 20


def layer_id(name: str, index: int) -> str:
    """A DOM id from a layer name, scoped by its overlay index."""
    safe = (
        "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in name)
        or "layer"
    )
    return f"{safe}_{index}"


def from_render_graph(graph: RenderGraph, base_video: str | Path | None = None) -> Composition:
    """Build the Composition for ``graph``, with the base video at track 0."""
    source = str(base_video if base_video is not None else graph.source)
    duration = quantise(float(graph.duration), graph.fps)
    layers: list[CompositionLayer] = [
        CompositionLayer(
            id="base_video",
            kind="media",
            preset="base",
            media_kind="video",
            rect=_full_canvas(graph),
            time=LayerTime(start=0.0, end=duration),
            content=LayerContent(source=source),
            style={"object_fit": "cover"},
            track=0,
            z=0,
        )
    ]
    for index, layer in enumerate(graph.overlays):
        built = _overlay_layer(graph, layer, index, duration)
        if built is not None:
            layers.append(built)

    return Composition(
        job_id=graph.job_id,
        canvas=Canvas(width=graph.width, height=graph.height, fps=graph.fps),
        duration=duration,
        scenes=[CompositionScene(id="main", start=0.0, end=duration, layers=layers)],
        audio=[],
        audio_mode="external",
    )


def _word_spans(words: list[Any]) -> list[WordSpan]:
    """Timed spans from a layer's ``style["words"]`` dicts."""
    spans: list[WordSpan] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        start = float(word.get("start", 0.0))
        spans.append(
            WordSpan(
                text=str(word.get("text", "")),
                start=start,
                end=float(word.get("end", start)),
            )
        )
    return spans


def _full_canvas(graph: RenderGraph) -> Rect:
    return Rect(x=0, y=0, width=graph.width, height=graph.height)


def _layer_time(graph: RenderGraph, layer: Layer, duration: float) -> LayerTime:
    """The overlay's window, bounded to the composition.

    A clip that starts on the last frame or ends past the composition would
    be rejected by the model; the legacy writer floored its duration at 1 ms
    and emitted it, so clamp to the window that fits rather than fail.
    """
    frame = 1.0 / graph.fps
    start = quantise(layer.timing.start, graph.fps)
    end = duration if layer.timing.end is None else quantise(layer.timing.end, graph.fps)
    start = min(max(0.0, start), max(0.0, duration - frame))
    end = min(duration, max(end, start + frame))
    return LayerTime(start=start, end=end)


def _overlay_layer(
    graph: RenderGraph, layer: Layer, index: int, duration: float
) -> CompositionLayer | None:
    common: dict[str, Any] = {
        "id": layer_id(layer.name, index),
        "rect": _clamped_rect(layer, graph),
        "time": _layer_time(graph, layer, duration),
        "style": dict(layer.style),
        "track": OVERLAY_TRACK_BASE + index,
        "z": index + 1,
    }

    if layer.kind == "text":
        words = layer.style.get("words")
        if layer.style.get("highlight_color") and isinstance(words, list) and words:
            return CompositionLayer(
                kind="rich_text",
                content=LayerContent(spans=_word_spans(words)),
                **common,
            )
        return CompositionLayer(kind="text", content=LayerContent(text=layer.text), **common)

    if layer.kind == "shape":
        preset = "spotlight" if layer.style.get("effect") == "spotlight" else ""
        return CompositionLayer(kind="shape", preset=preset, **common)

    if layer.kind in {"image", "logo"} and layer.source:
        common["style"] = {**common["style"], "object_fit": "contain"}
        return CompositionLayer(
            kind="media",
            media_kind="image",
            content=LayerContent(source=layer.source),
            **common,
        )

    if layer.kind == "video" and layer.source:
        common["style"] = {**common["style"], "object_fit": "cover"}
        return CompositionLayer(
            kind="media",
            media_kind="video",
            content=LayerContent(source=layer.source),
            **common,
        )

    logger.warning("render_graph_layer_skipped", name=layer.name, kind=layer.kind)
    return None


def _clamped_rect(layer: Layer, graph: RenderGraph) -> Rect:
    rect = layer.rect
    width = max(1, min(rect.width, graph.width))
    height = max(1, min(rect.height, graph.height))
    x = max(0, min(rect.x, graph.width - width))
    y = max(0, min(rect.y, graph.height - height))
    return Rect(x=x, y=y, width=width, height=height)
