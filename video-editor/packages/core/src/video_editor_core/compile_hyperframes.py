"""Compile a :class:`RenderGraph` into one HyperFrames composition plan.

Unlike the historical graphics pipeline — which rendered *every segment* as its
own HyperFrames composition and concatenated the results — this emits a
**single whole-timeline composition**: one base ``video`` layer plus every
timed overlay layer. The renderer feeds HyperFrames the already-cut source
video and this plan.

The plan shape matches ``video_editor_core.hyperframes_cli.lint_plan``:
``{width,height,fps,duration,layers:[{type,name,x,y,width,height,text,style,source,timing}]}``.
The extra ``timing`` field is ignored by older HyperFrames builds and consumed
by newer ones; FFmpeg remains the fallback.
"""

from __future__ import annotations

from typing import Any

from video_editor_core.render_graph import Layer, RenderGraph

_BASE_LAYER_NAME = "base_video"


def _layer_dict(layer: Layer) -> dict[str, Any]:
    return {
        "type": layer.kind,
        "name": layer.name,
        "x": layer.rect.x,
        "y": layer.rect.y,
        "width": layer.rect.width,
        "height": layer.rect.height,
        "text": layer.text,
        "style": layer.style,
        "source": layer.source,
        "timing": {"start": layer.timing.start, "end": layer.timing.end},
    }


def compile_hyperframes(graph: RenderGraph, cut_source: str) -> dict[str, Any]:
    """Compile ``graph`` into a single HyperFrames plan over ``cut_source``.

    Args:
        graph: the render graph (canvas, cuts, overlays, audio).
        cut_source: path to the video-only cut produced by the FFmpeg cut pass.
    """
    layers: list[dict[str, Any]] = [
        {
            "type": "video",
            "name": _BASE_LAYER_NAME,
            "x": 0,
            "y": 0,
            "width": graph.width,
            "height": graph.height,
            "text": "",
            "style": {},
            "source": cut_source,
            "timing": {"start": 0.0, "end": None},
        }
    ]
    layers.extend(_layer_dict(layer) for layer in graph.overlays)

    return {
        "width": graph.width,
        "height": graph.height,
        "fps": graph.fps,
        "duration": graph.duration,
        "layers": layers,
    }
