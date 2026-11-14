"""Film-look overlay layers that are not part of ``data-color-grading``.

A light leak is a full-canvas ``html`` layer with its gradient and animation in
``extra_css``. The animation only keyframes ``opacity`` and ``transform``, so a
HyperFrames seek reproduces the same frame instead of freezing or restarting.
The placement policy is video-level (the design's ``light_leak.placement``),
not per scene: one leak at the first cut, one at the last content cut, or both.
"""

from __future__ import annotations

from collections.abc import Sequence

from video_editor_core.composition import Canvas, CompositionLayer, LayerContent, LayerTime
from video_editor_core.designs import DesignTokens, LightLeak
from video_editor_core.render_graph import Rect

#: Above the pills, below the captions: a wash over the picture, not the text.
Z_LIGHT_LEAK = 35

#: ``(scene id, start, end)`` for the scenes that can host a leak.
SceneWindow = tuple[str, float, float]


def _leak_css(layer_id: str, leak: LightLeak, duration: float) -> str:
    name = f"ve_leak_{layer_id}"
    return (
        f"@keyframes {name} {{"
        " from { opacity:0; transform:scale(1) translate3d(0,0,0); }"
        f" 40% {{ opacity:{leak.intensity:g}; }}"
        " to { opacity:0; transform:scale(1.08) translate3d(-2%,1%,0); }"
        f" }}\n#{layer_id} {{"
        f" background:radial-gradient(circle at 16% 10%, {leak.color} 0%, transparent 55%);"
        " mix-blend-mode:screen; pointer-events:none;"
        f" animation:{name} {duration:.3f}s ease-out both; }}"
    )


def _host_indexes(placement: str, count: int) -> list[int]:
    indexes: list[int] = []
    if placement in ("first_cut", "both"):
        indexes.append(1)
    if placement in ("last_cut", "both"):
        indexes.append(count - 1)
    # ``both`` on a two-scene pack names the same cut twice.
    return list(dict.fromkeys(index for index in indexes if 0 < index < count))


def light_leak_layers(
    tokens: DesignTokens, windows: Sequence[SceneWindow], canvas: Canvas
) -> list[tuple[str, CompositionLayer]]:
    """The leak layers to attach, each with the scene id that hosts it."""
    leak = tokens.light_leak
    if leak.placement == "off" or len(windows) < 2:
        return []
    layers: list[tuple[str, CompositionLayer]] = []
    for index in _host_indexes(leak.placement, len(windows)):
        scene_id, start, end = windows[index]
        duration = min(leak.duration, end - start)
        if duration <= 0.05:
            continue
        layer_id = f"light_leak_{scene_id}"
        layers.append(
            (
                scene_id,
                CompositionLayer(
                    id=layer_id,
                    kind="html",
                    preset="light_leak",
                    rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
                    time=LayerTime(start=start, end=start + duration),
                    content=LayerContent(),
                    style={
                        "extra_css": _leak_css(layer_id, leak, duration),
                        "flourish": "light_leak",
                    },
                    track=1,
                    z=Z_LIGHT_LEAK,
                ),
            )
        )
    return layers
