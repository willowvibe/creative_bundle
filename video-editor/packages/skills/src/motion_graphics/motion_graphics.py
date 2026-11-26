"""Motion graphics skill: code highlights, diagrams, and callouts.

The skill converts structured data into HyperFrames composition plans, then
either:

1. Compiles the plan to a HyperFrames **HTML project** and renders it with
   ``hyperframes render <dir>`` (when the CLI is available), or
2. Falls back to a deterministic ffmpeg render (always available).

The HTML path supports entrance animations (``enter``/``delay`` layer keys),
circle shapes, SVG edges, and animated ``orbit`` layers. So that no frame is
ever a freeze-frame, diagrams and code highlights also get an ``ambient``
background layer (``"glow"`` drifting light or ``"stars"`` twinkling parallax
field) and a slow ``camera_drift`` across every content layer. Any node, edge
or orbit can be timed to the narration with ``at`` (seconds from the clip
start — resolve phrases to times with :mod:`video_editor_core.cues`) and pulse
on cue with ``emphasize`` (a list of times). The fall-back path
draws rect layers with ``drawbox`` and text layers with ``drawtext`` (reading
text from files, so no escaping is involved); lines and orbits are skipped.
The plan data and lint output are always emitted regardless of which renderer
runs, and the manifest records the renderer that actually produced each clip.

The skill supports three template kinds:

- :class:`CodeHighlightSpec` — animated code snippet with line highlighting
- :class:`DiagramSpec` — node/edge diagram with optional title
- :class:`CalloutSpec` — lower-third banner (name + role + duration)

All plan builders produce a dict that matches the HyperFrames composition
schema consumed by :func:`video_editor_core.hyperframes_cli.lint_plan`.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.fonts import font_face_css, with_indic_fallbacks
from video_editor_core.hyperframes_cli import (
    HyperFramesLintError,
    RenderSegmentError,
    hyperframes_available,
    lint_plan,
    render_project,
)
from video_editor_core.shaped_text import (
    PngOverlay,
    linear_graph,
    needs_shaping,
    render_shaped_text,
)
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, FFmpegError, run_ffmpeg
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

# Default canvas — 1920x1080 matches the engine's other composition outputs.
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30

# Brand-friendly defaults.
DEFAULT_BG_COLOR = "#0F172A"  # slate-900
DEFAULT_TEXT_COLOR = "#F8FAFC"  # slate-50
DEFAULT_ACCENT_COLOR = "#38BDF8"  # sky-400
DEFAULT_TITLE_COLOR = "#FBBF24"  # amber-400


@dataclass
class CodeHighlightSpec:
    """Specification for a code-highlight callout."""

    code: str
    title: str = ""
    highlight_lines: list[int] = field(default_factory=list)
    language: str = "python"
    duration: float = 4.0
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    bg_color: str = DEFAULT_BG_COLOR
    text_color: str = DEFAULT_TEXT_COLOR
    accent_color: str = DEFAULT_ACCENT_COLOR
    title_color: str = DEFAULT_TITLE_COLOR
    ambient: str = "glow"
    camera_drift: bool = True


@dataclass
class DiagramSpec:
    """Specification for a node/edge diagram callout.

    Nodes accept ``id``, ``label``, ``x``, ``y``, ``width``, ``height`` and
    optionally ``shape`` (``"rect"`` or ``"circle"``), ``color``,
    ``text_color``, ``ring`` (a colour, or ``True``, for a planetary ring on a
    circle), ``shading`` (sphere highlight on circles, default on),
    ``at`` (reveal time in seconds) and ``emphasize`` (pulse times).
    Edges accept ``from``, ``to`` and optionally ``arrow``, ``color`` and
    ``at``; they draw on from source to target. Orbits accept ``cx``, ``cy``,
    ``radius`` and optionally ``label``, ``body_size``, ``color``, ``period``
    (seconds per revolution), ``start_angle`` (degrees, clockwise from
    12 o'clock) and ``at``.

    ``ambient`` is ``"glow"`` (default), ``"stars"`` or ``"none"``;
    ``camera_drift`` slowly pans every content layer so held shots keep moving.
    """

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = field(default_factory=list)
    orbits: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""
    duration: float = 3.0
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    bg_color: str = DEFAULT_BG_COLOR
    text_color: str = DEFAULT_TEXT_COLOR
    accent_color: str = DEFAULT_ACCENT_COLOR
    title_color: str = DEFAULT_TITLE_COLOR
    ambient: str = "glow"
    camera_drift: bool = True


@dataclass
class CalloutSpec:
    """Specification for a lower-third callout."""

    name: str
    role: str = ""
    duration: float = 2.5
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT
    fps: int = DEFAULT_FPS
    bg_color: str = DEFAULT_ACCENT_COLOR
    text_color: str = "#0F172A"
    accent_color: str = DEFAULT_ACCENT_COLOR


class MotionGraphicsError(RuntimeError):
    """Raised when motion graphics composition/rendering fails."""


def _ambient_layer(ambient: str, width: int, height: int, color: str) -> list[dict[str, Any]]:
    """A full-canvas ambient layer (drawn just above the background), or none."""
    if ambient not in _AMBIENT_KINDS:
        return []
    return [
        {
            "name": "ambient",
            "type": "ambient",
            "style": ambient,
            "x": 0,
            "y": 0,
            "width": width,
            "height": height,
            "color": color,
        }
    ]


def _code_highlight_layers(spec: CodeHighlightSpec) -> list[dict[str, Any]]:
    """Build HyperFrames layers for a code highlight plan."""
    lines = spec.code.splitlines() or [""]
    layers: list[dict[str, Any]] = [
        {
            "name": "background",
            "type": "rect",
            "x": 0,
            "y": 0,
            "width": spec.width,
            "height": spec.height,
            "color": spec.bg_color,
        }
    ]
    layers += _ambient_layer(spec.ambient, spec.width, spec.height, spec.accent_color)
    if spec.title:
        layers.append(
            {
                "name": "title",
                "type": "text",
                "x": 80,
                "y": 60,
                "width": spec.width - 160,
                "height": 80,
                "text": spec.title,
                "color": spec.title_color,
                "size": 48,
                "font": "Inter",
            }
        )
    code_top = 180 if spec.title else 120
    line_height = 48
    for idx, line in enumerate(lines):
        is_highlighted = (idx + 1) in spec.highlight_lines
        if is_highlighted:
            layers.append(
                {
                    "name": f"highlight_{idx}",
                    "type": "rect",
                    "x": 60,
                    "y": code_top + idx * line_height - 6,
                    "width": spec.width - 120,
                    "height": line_height,
                    "color": spec.accent_color,
                    "opacity": 0.25,
                }
            )
        if not line.strip():
            continue  # skip empty lines — the lint flags empty text layers
        layers.append(
            {
                "name": f"line_{idx}",
                "type": "text",
                "x": 80,
                "y": code_top + idx * line_height,
                "width": spec.width - 160,
                "height": line_height,
                "text": line,
                "color": spec.text_color,
                "size": 32,
                "font": "JetBrains Mono",
            }
        )
    return layers


def _diagram_layers(spec: DiagramSpec) -> list[dict[str, Any]]:
    """Build HyperFrames layers for a diagram plan."""
    layers: list[dict[str, Any]] = [
        {
            "name": "background",
            "type": "rect",
            "x": 0,
            "y": 0,
            "width": spec.width,
            "height": spec.height,
            "color": spec.bg_color,
        }
    ]
    layers += _ambient_layer(spec.ambient, spec.width, spec.height, spec.accent_color)
    if spec.title:
        layers.append(
            {
                "name": "title",
                "type": "text",
                "x": 80,
                "y": 60,
                "width": spec.width - 160,
                "height": 80,
                "text": spec.title,
                "color": spec.title_color,
                "size": 48,
                "font": "Inter",
                "enter": "slide_up",
            }
        )
    for idx, orbit in enumerate(spec.orbits):
        cx, cy = int(orbit.get("cx", spec.width // 2)), int(orbit.get("cy", spec.height // 2))
        radius = int(orbit.get("radius", 200))
        layers.append(
            {
                "name": f"orbit_{idx}",
                "type": "orbit",
                "x": cx - radius,
                "y": cy - radius,
                "width": 2 * radius,
                "height": 2 * radius,
                "label": str(orbit.get("label", "")),
                "body_size": int(orbit.get("body_size", 28)),
                "color": str(orbit.get("color", spec.accent_color)),
                "ring_color": str(orbit.get("ring_color", "rgba(255,255,255,0.25)")),
                "text_color": str(orbit.get("text_color", spec.text_color)),
                "period": float(orbit.get("period", 8.0)),
                "start_angle": float(orbit.get("start_angle", 0.0)),
                "enter": "fade",
                "delay": _cue(orbit, 0.2 + 0.1 * idx),
            }
        )
    # Edges join node centres, so emit them before the nodes: later layers
    # paint on top, and an edge drawn last would cross over its node boxes.
    node_ids = [str(node.get("id", f"node_{idx}")) for idx, node in enumerate(spec.nodes)]
    edge_delay = 0.3 + 0.12 * len(spec.nodes)
    for edge in spec.edges:
        from_id = str(edge.get("from", ""))
        to_id = str(edge.get("to", ""))
        # Skip edges whose endpoints are not declared; the lint pass will
        # catch this if the renderer ever wires those.
        if from_id not in node_ids or to_id not in node_ids:
            continue
        layers.append(
            {
                "name": f"edge_{from_id}_{to_id}",
                "type": "line",
                "from": from_id,
                "to": to_id,
                "color": str(edge.get("color", spec.text_color)),
                "thickness": int(edge.get("thickness", 3)),
                "arrow": bool(edge.get("arrow", False)),
                "enter": "draw",
                "delay": _cue(edge, edge_delay),
            }
        )
    for idx, node in enumerate(spec.nodes):
        node_id = node_ids[idx]
        x = int(node.get("x", 100 + idx * 200))
        y = int(node.get("y", 240))
        width = int(node.get("width", 160))
        height = int(node.get("height", 80))
        delay = _cue(node, 0.3 + 0.12 * idx)
        emphasize = [float(t) for t in node.get("emphasize", []) or []]
        node_layer: dict[str, Any] = {
            "name": f"node_{node_id}",
            "type": "rect",
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "color": str(node.get("color", spec.accent_color)),
            "shape": str(node.get("shape", "rect")),
            "radius": 14,
            "enter": "pop",
            "delay": delay,
            "emphasize": emphasize,
            "shading": bool(node.get("shading", True)),
        }
        if node.get("ring"):
            ring = node["ring"]
            node_layer["ring"] = ring if isinstance(ring, str) else "rgba(229, 207, 140, 0.85)"
        layers.append(node_layer)
        label = str(node.get("label", node_id))
        if not label.strip():
            continue  # an unlabeled node (e.g. a plain circle) gets no text layer
        layers.append(
            {
                "name": f"label_{node_id}",
                "type": "text",
                "x": x + 12,
                "y": y + 18,
                "width": width - 24,
                "height": height - 36,
                "text": label,
                "color": str(node.get("text_color", spec.bg_color)),
                "size": int(node.get("text_size", 28)),
                "font": "Inter",
                "align": "center",
                "enter": "pop",
                "delay": delay,
                "emphasize": emphasize,
            }
        )
    return layers


def _callout_layers(spec: CalloutSpec) -> list[dict[str, Any]]:
    """Build HyperFrames layers for a lower-third callout."""
    bar_height = 180
    bar_y = spec.height - bar_height - 80
    layers: list[dict[str, Any]] = [
        {
            "name": "background",
            "type": "rect",
            "x": 0,
            "y": 0,
            "width": spec.width,
            "height": spec.height,
            "color": "#000000",
            "opacity": 0.0,
        },
        {
            "name": "bar",
            "type": "rect",
            "x": 0,
            "y": bar_y,
            "width": spec.width,
            "height": bar_height,
            "color": spec.bg_color,
            "enter": "slide_right",
        },
        {
            "name": "name",
            "type": "text",
            "x": 80,
            "y": bar_y + 18,
            "width": spec.width - 160,
            "height": 80,
            "text": spec.name,
            "color": spec.text_color,
            "size": 56,
            "font": "Inter",
            "enter": "slide_up",
            "delay": 0.25,
        },
    ]
    if spec.role.strip():
        layers.append(
            {
                "name": "role",
                "type": "text",
                "x": 80,
                "y": bar_y + 96,
                "width": spec.width - 160,
                "height": 60,
                "text": spec.role,
                "color": spec.text_color,
                "size": 36,
                "font": "Inter",
                "enter": "slide_up",
                "delay": 0.45,
            }
        )
    return layers


# ---------------------------------------------------------------------------
# HyperFrames HTML compilation
# ---------------------------------------------------------------------------

# Font stacks: the brand fonts are not guaranteed to be installed, so each
# falls back to faces that ship with common Linux/macOS installs.
_SANS_STACK = "'Inter', 'Noto Sans', 'DejaVu Sans', system-ui, sans-serif"
_MONO_STACK = "'JetBrains Mono', 'DejaVu Sans Mono', monospace"

_ENTER_SECONDS = 0.6
_DRAW_SECONDS = 0.7
_PULSE_SECONDS = 0.7
_AMBIENT_KINDS = frozenset({"glow", "stars"})
# Camera drift: the largest pan, in px, across one clip. Small enough to read
# as a held shot, large enough that the frame is never a freeze-frame.
_CAMERA_DRIFT_PX = 26.0
# Starfield sheets, far to near: (count, dot px, drift px over the clip,
# twinkle period s). Nearer sheets drift further, which reads as parallax.
_STAR_LAYERS: tuple[tuple[int, float, int, float], ...] = (
    (110, 1.6, 10, 2.9),
    (60, 2.4, 22, 2.1),
    (22, 3.4, 40, 1.7),
)
_ENTER_KEYFRAMES: dict[str, str] = {
    "fade": "from { opacity: 0; } to { opacity: 1; }",
    "pop": "from { opacity: 0; transform: scale(0.6); } to { opacity: 1; transform: scale(1); }",
    "slide_up": (
        "from { opacity: 0; transform: translateY(40px); } "
        "to { opacity: 1; transform: translateY(0); }"
    ),
    "slide_right": (
        "from { opacity: 0; transform: translateX(-120px); } "
        "to { opacity: 1; transform: translateX(0); }"
    ),
}


def _cue(item: dict[str, Any], default: float) -> float:
    """``item["at"]`` (an explicit, narration-cued reveal time) or ``default``."""
    value = item.get("at")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return default


def _num(layer: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = layer.get(key, default)
    return (
        float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    )


def _enter_css(layer: dict[str, Any]) -> str:
    """Return the ``animation`` value for a layer's entrance, or ``""``."""
    enter = layer.get("enter")
    if enter not in _ENTER_KEYFRAMES:
        return ""
    return f"mg_{enter} {_ENTER_SECONDS}s cubic-bezier(0.2, 0.8, 0.2, 1) {_num(layer, 'delay'):.3f}s both"


def _pulse_css(layer: dict[str, Any]) -> list[str]:
    """One ``mg_pulse`` animation per cue time in ``layer["emphasize"]``."""
    times = layer.get("emphasize") or []
    return [
        f"mg_pulse {_PULSE_SECONDS}s ease-out {float(t):.3f}s none"
        for t in times
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t >= 0
    ]


def _camera_css(layer: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """The shared slow pan (``mg_cam``) for every content layer, when enabled."""
    if not plan.get("camera_drift") or layer.get("name") in {"background", "ambient"}:
        return []
    return [f"mg_cam {float(plan['duration']):.3f}s ease-in-out 0s both"]


def _animation(parts: list[str]) -> str:
    parts = [part for part in parts if part]
    return f"animation:{', '.join(parts)};" if parts else ""


def _box_css(layer: dict[str, Any]) -> str:
    return (
        f"left:{int(_num(layer, 'x'))}px;top:{int(_num(layer, 'y'))}px;"
        f"width:{int(_num(layer, 'width'))}px;height:{int(_num(layer, 'height'))}px;"
    )


def _node_boxes(layers: list[dict[str, Any]]) -> dict[str, tuple[float, float, float, float, bool]]:
    """``{node_id: (cx, cy, half_w, half_h, is_circle)}`` for every diagram node."""
    boxes: dict[str, tuple[float, float, float, float, bool]] = {}
    for layer in layers:
        name = str(layer.get("name", ""))
        if layer.get("type") == "rect" and name.startswith("node_"):
            half_w, half_h = _num(layer, "width") / 2, _num(layer, "height") / 2
            boxes[name[len("node_") :]] = (
                _num(layer, "x") + half_w,
                _num(layer, "y") + half_h,
                half_w,
                half_h,
                layer.get("shape") == "circle",
            )
    return boxes


def _boundary_fraction(box: tuple[float, float, float, float, bool], dx: float, dy: float) -> float:
    """Fraction of the centre-to-centre vector ``(dx, dy)`` that lies inside ``box``."""
    _, _, half_w, half_h, circle = box
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0
    if circle:
        return min(half_w, half_h) / length
    limits = [half_w / abs(dx) if dx else float("inf"), half_h / abs(dy) if dy else float("inf")]
    return min(limits)


def _edge_endpoints(
    boxes: dict[str, tuple[float, float, float, float, bool]],
    from_id: str,
    to_id: str,
) -> tuple[float, float, float, float] | None:
    """Edge endpoints clipped to the node outlines (plus a small gap)."""
    source, target = boxes.get(from_id), boxes.get(to_id)
    if source is None or target is None:
        return None
    dx, dy = target[0] - source[0], target[1] - source[1]
    length = math.hypot(dx, dy) or 1.0
    gap = 8.0 / length
    start = min(0.45, _boundary_fraction(source, dx, dy) + gap)
    end = max(0.55, 1.0 - _boundary_fraction(target, dx, dy) - gap)
    return (
        source[0] + dx * start,
        source[1] + dy * start,
        source[0] + dx * end,
        source[1] + dy * end,
    )


def _star_shadows(rng: random.Random, count: int, width: int, height: int, size: float) -> str:
    margin = 60
    dots = []
    for _ in range(count):
        x = rng.uniform(-margin, width + margin)
        y = rng.uniform(-margin, height + margin)
        alpha = rng.uniform(0.35, 0.95)
        dots.append(f"{x:.0f}px {y:.0f}px 0 {size / 2:.1f}px rgba(255,255,255,{alpha:.2f})")
    return ", ".join(dots)


def _ambient_html(layer: dict[str, Any], common: str, plan: dict[str, Any]) -> str:
    """Render an ``ambient`` layer: drifting glow or a twinkling parallax starfield."""
    width, height = int(plan["width"]), int(plan["height"])
    duration = float(plan["duration"])
    color = html.escape(str(layer.get("color", DEFAULT_ACCENT_COLOR)))
    if layer.get("style") == "stars":
        rng = random.Random(_plan_seed(plan))
        sheets = []
        for depth, (count, size, _drift, twinkle) in enumerate(_STAR_LAYERS):
            shadows = _star_shadows(rng, count, width, height, size)
            sheets.append(
                f'<div style="position:absolute;left:0;top:0;width:1px;height:1px;'
                f"box-shadow:{shadows};"
                f"animation:mg_twinkle {twinkle:.2f}s ease-in-out {-depth * 0.7:.2f}s infinite alternate, "
                f'mg_star_drift_{depth} {duration:.3f}s linear 0s both;"></div>'
            )
        glow = (
            f'<div style="position:absolute;inset:-10%;background:radial-gradient(900px 620px at 50% 45%, '
            f'color-mix(in srgb, {color} 10%, transparent), transparent 70%);"></div>'
        )
        return f'    <div {common} style="left:0;top:0;width:{width}px;height:{height}px;">{glow}{"".join(sheets)}</div>'
    glow_css = (
        f"left:{-width * 0.1:.0f}px;top:{-height * 0.1:.0f}px;width:{width * 1.2:.0f}px;height:{height * 1.2:.0f}px;"
        f"background:radial-gradient(1100px 760px at 22% 18%, color-mix(in srgb, {color} 16%, transparent), transparent 62%),"
        f"radial-gradient(1000px 700px at 80% 82%, color-mix(in srgb, {color} 10%, transparent), transparent 60%);"
        f"animation:mg_glow_drift {duration:.3f}s ease-in-out 0s both;"
    )
    return f'    <div {common} style="{glow_css}"></div>'


def _plan_seed(plan: dict[str, Any]) -> int:
    """Deterministic per-plan seed so renders are reproducible."""
    key = f"{plan.get('template')}|{plan.get('duration')}|{len(plan.get('layers', []))}"
    for layer in plan.get("layers", []):
        if layer.get("name") == "title":
            key += f"|{layer.get('text')}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


def _layer_html(
    layer: dict[str, Any],
    index: int,
    plan: dict[str, Any],
    boxes: dict[str, tuple[float, float, float, float, bool]],
) -> str | None:
    duration = float(plan["duration"])
    layer_type = layer.get("type")
    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_" else "_" for ch in str(layer.get("name", ""))
    )
    common = (
        f'id="{safe_name or "layer"}_{index}" class="clip" data-start="0" '
        f'data-duration="{duration:.3f}" data-track-index="{index + 1}"'
    )
    enter = _enter_css(layer)
    camera = _camera_css(layer, plan)
    pulses = _pulse_css(layer)
    color = html.escape(str(layer.get("color", DEFAULT_TEXT_COLOR)))

    if layer_type == "ambient":
        return _ambient_html(layer, common, plan)

    if layer_type == "rect":
        circle = layer.get("shape") == "circle"
        radius = "50%" if circle else f"{int(_num(layer, 'radius'))}px"
        background = color
        if circle and layer.get("shading", True):
            # A soft specular highlight and terminator shadow read as a sphere.
            background = (
                "radial-gradient(circle at 34% 30%, rgba(255,255,255,0.42), rgba(255,255,255,0) 50%), "
                f"radial-gradient(circle at 72% 78%, rgba(0,0,0,0.30), rgba(0,0,0,0) 62%), {color}"
            )
        css = (
            f"{_box_css(layer)}background:{background};opacity:{_num(layer, 'opacity', 1.0)};"
            f"border-radius:{radius};"
        )
        animations = [enter, *camera, *pulses]
        if circle:
            css += f"color:{color};box-shadow:0 0 50px currentColor;"
            breathe_at = _num(layer, "delay") + _ENTER_SECONDS
            animations.append(f"mg_breathe 3.2s ease-in-out {breathe_at:.3f}s infinite alternate")
        css += _animation(animations)
        inner = ""
        ring = layer.get("ring")
        if circle and ring:
            inner = (
                f'<div style="position:absolute;left:-24%;width:148%;top:37%;height:26%;'
                f"box-sizing:border-box;border:{max(3, int(_num(layer, 'width') / 28))}px solid "
                f'{html.escape(str(ring))};border-radius:50%;transform:rotate(-16deg);"></div>'
            )
        return f'    <div {common} style="{css}">{inner}</div>'

    if layer_type == "text":
        font = str(layer.get("font", "Inter"))
        stack = _MONO_STACK if "mono" in font.lower() else _SANS_STACK
        justify = "center" if layer.get("align") == "center" else "flex-start"
        css = (
            f"{_box_css(layer)}display:flex;align-items:center;justify-content:{justify};"
            f"text-align:{'center' if justify == 'center' else 'left'};"
            f"font-family:{stack};font-weight:700;font-size:{int(_num(layer, 'size', 32))}px;"
            f"color:{color};line-height:1.15;white-space:pre;"
        )
        css += _animation([enter, *camera, *pulses])
        return f'    <div {common} style="{css}">{html.escape(str(layer.get("text", "")))}</div>'

    if layer_type == "line":
        points = _edge_endpoints(boxes, str(layer.get("from", "")), str(layer.get("to", "")))
        if points is None:
            return None
        x1, y1, x2, y2 = points
        width, height = int(plan["width"]), int(plan["height"])
        delay = _num(layer, "delay")
        thickness = int(_num(layer, "thickness", 3))
        # The svg is hidden until the cue, then the stroke draws on from source
        # to target (pathLength=1 makes the dash maths length-independent).
        css = f"left:0;top:0;width:{width}px;height:{height}px;overflow:visible;"
        css += _animation([f"mg_fade 0.01s linear {delay:.3f}s both", *camera])
        draw = f"mg_draw {_DRAW_SECONDS}s cubic-bezier(0.4, 0, 0.2, 1) {delay:.3f}s both"
        svg_body = (
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" pathLength="1" '
            f'stroke="{color}" stroke-width="{thickness}" stroke-linecap="round" '
            f'stroke-opacity="0.85" stroke-dasharray="1" style="animation:{draw}" />'
        )
        if layer.get("arrow"):
            angle = math.atan2(y2 - y1, x2 - x1)
            size = 8 + thickness * 3
            cos, sin = math.cos(angle), math.sin(angle)
            left = (x2 - size * cos + size * 0.55 * sin, y2 - size * sin - size * 0.55 * cos)
            right = (x2 - size * cos - size * 0.55 * sin, y2 - size * sin + size * 0.55 * cos)
            head_at = delay + _DRAW_SECONDS * 0.85
            svg_body += (
                f'<polygon points="{x2:.1f},{y2:.1f} {left[0]:.1f},{left[1]:.1f} '
                f'{right[0]:.1f},{right[1]:.1f}" fill="{color}" fill-opacity="0.9" '
                f'style="animation:mg_fade 0.15s linear {head_at:.3f}s both" />'
            )
        return f'    <svg {common} style="{css}" viewBox="0 0 {width} {height}">{svg_body}</svg>'

    if layer_type == "orbit":
        period = max(0.1, _num(layer, "period", 8.0))
        # A negative delay starts the spin part-way round, at ``start_angle``.
        phase = -(_num(layer, "start_angle") % 360.0) / 360.0 * period
        spin = f"mg_spin {period:.3f}s linear {phase:.3f}s infinite"
        ring_css = (
            f"{_box_css(layer)}border:2px solid {html.escape(str(layer.get('ring_color')))};"
            f"border-radius:50%;box-sizing:border-box;"
        )
        ring_css += _animation([spin, enter, *camera])
        body = int(_num(layer, "body_size", 28))
        body_css = (
            f"position:absolute;left:calc(50% - {body / 2:.1f}px);top:{-body / 2:.1f}px;"
            f"width:{body}px;height:{body}px;border-radius:50%;"
            "background:radial-gradient(circle at 34% 30%, rgba(255,255,255,0.45), rgba(255,255,255,0) 55%), "
            f"{color};box-shadow:0 0 18px {color};"
        )
        label = html.escape(str(layer.get("label", "")))
        label_html = ""
        if label:
            # Counter-rotate the label so it stays upright while the ring spins.
            label_css = (
                f"position:absolute;left:50%;top:50%;width:240px;margin-left:-120px;"
                f"margin-top:{body / 2 + 6:.1f}px;text-align:center;"
                f"font-family:{_SANS_STACK};font-weight:700;font-size:24px;"
                f"color:{html.escape(str(layer.get('text_color', DEFAULT_TEXT_COLOR)))};"
                "text-shadow:0 2px 10px rgba(0,0,0,0.9);"
            )
            label_html = (
                f'<div style="position:absolute;inset:0;animation:mg_spin {period:.3f}s linear '
                f'{phase:.3f}s infinite reverse;"><div style="{label_css}">{label}</div></div>'
            )
        return (
            f'    <div {common} style="{ring_css}"><div style="{body_css}">{label_html}</div></div>'
        )

    logger.warning("motion_graphics_html_skipped_layer", name=layer.get("name"), type=layer_type)
    return None


def _camera_keyframes(plan: dict[str, Any]) -> str:
    """``mg_cam``: a slow pan whose direction is fixed per plan (deterministic)."""
    rng = random.Random(_plan_seed(plan) ^ 0x5EED)
    angle = rng.uniform(0, 2 * math.pi)
    dx = _CAMERA_DRIFT_PX * math.cos(angle)
    dy = _CAMERA_DRIFT_PX * 0.6 * math.sin(angle)
    return (
        f"      @keyframes mg_cam {{ from {{ translate: {-dx / 2:.1f}px {-dy / 2:.1f}px; }} "
        f"to {{ translate: {dx / 2:.1f}px {dy / 2:.1f}px; }} }}"
    )


def _plan_to_html(plan: dict[str, Any], fonts_css: str = "") -> str:
    """Compile a motion graphics plan into a HyperFrames HTML composition.

    Every layer becomes a root-level timed ``class="clip"`` element spanning
    the whole plan, on its own track (HyperFrames only seeks root-level
    clips; higher tracks paint in front). Animations are plain CSS keyframes,
    which HyperFrames seeks deterministically per frame. Entrances animate
    ``transform``; cue pulses animate ``scale`` and the camera pans with
    ``translate``, so the three compose instead of overriding each other.
    """
    width, height = int(plan["width"]), int(plan["height"])
    duration = float(plan["duration"])
    fps = plan["fps"]
    layers = list(plan.get("layers", []))
    boxes = _node_boxes(layers)
    clips = [
        clip
        for index, layer in enumerate(layers)
        if (clip := _layer_html(layer, index, plan, boxes)) is not None
    ]
    keyframes = "\n".join(
        f"      @keyframes mg_{name} {{ {frames} }}" for name, frames in _ENTER_KEYFRAMES.items()
    )
    clip_block = "\n".join(clips)
    star_drift = "\n".join(
        f"      @keyframes mg_star_drift_{depth} {{ from {{ transform: translate(0, 0); }} "
        f"to {{ transform: translate({-px}px, {-px * 0.35:.1f}px); }} }}"
        for depth, (_, _, px, _) in enumerate(_STAR_LAYERS)
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <title>{html.escape(str(plan.get("template", "motion_graphics")))}</title>
    <style>
{fonts_css}
      html, body {{ margin: 0; padding: 0; }}
      body {{ background: #000; }}
      #root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; }}
      .clip {{ position: absolute; transform-origin: center; }}
{keyframes}
      @keyframes mg_spin {{ from {{ transform: rotate(0deg); }} to {{ transform: rotate(360deg); }} }}
      @keyframes mg_draw {{ from {{ stroke-dashoffset: 1; }} to {{ stroke-dashoffset: 0; }} }}
      @keyframes mg_pulse {{ 0% {{ scale: 1; filter: brightness(1); }} 35% {{ scale: 1.09; filter: brightness(1.45); }} 100% {{ scale: 1; filter: brightness(1); }} }}
      @keyframes mg_breathe {{ from {{ box-shadow: 0 0 40px currentColor; }} to {{ box-shadow: 0 0 95px currentColor; }} }}
      @keyframes mg_twinkle {{ from {{ opacity: 0.55; }} to {{ opacity: 1; }} }}
      @keyframes mg_glow_drift {{ from {{ transform: translate(-3%, -2%) scale(1.04); }} to {{ transform: translate(3%, 2%) scale(1.12); }} }}
{star_drift}
{_camera_keyframes(plan)}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-no-timeline
      data-start="0"
      data-width="{width}"
      data-height="{height}"
      data-duration="{duration:.3f}"
      data-fps="{fps}"
    >
{clip_block}
    </div>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# ffmpeg fallback
# ---------------------------------------------------------------------------


def _ffmpeg_color(value: object, default: str) -> str:
    """Return ``0xRRGGBB`` for a ``#RRGGBB`` value (else ``default``)."""
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in text):
        return f"0x{text}"
    return f"0x{default.lstrip('#')}"


def _filter_path(path: Path) -> str:
    """Single-quote a file path for use as a filter option value.

    Callers pass paths inside a ``tempfile`` directory, whose generated names
    contain no filter metacharacters; a stray ``'`` is still closed, escaped
    and reopened so the quoting can never unbalance.
    """
    return "'" + str(path).replace("'", "'\\''") + "'"


def _fallback_filters(plan: dict[str, Any], workdir: Path) -> list[str | PngOverlay]:
    """Build the ffmpeg filter chain for the fallback renderer.

    Rect layers become ``drawbox`` fills. Text layers become ``drawtext``
    filters that read their text from a file in ``workdir``, so quotes,
    colons, percents and commas in the text need no escaping. Lines and
    orbits have no ffmpeg equivalent here and are skipped. Text in a script
    drawtext cannot shape (Telugu, Devanagari, ...) becomes a PNG overlay (#322).
    """
    filters: list[str | PngOverlay] = []
    for index, layer in enumerate(plan.get("layers", [])):
        layer_type = layer.get("type")
        x, y = int(_num(layer, "x")), int(_num(layer, "y"))
        w, h = int(_num(layer, "width")), int(_num(layer, "height"))
        if layer_type == "rect":
            opacity = max(0.0, min(1.0, _num(layer, "opacity", 1.0)))
            if opacity == 0.0 or w <= 0 or h <= 0:
                continue
            color = _ffmpeg_color(layer.get("color"), DEFAULT_ACCENT_COLOR)
            filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}@{opacity:.2f}:t=fill")
        elif layer_type == "text":
            text = str(layer.get("text", ""))
            if not text.strip():
                continue
            color = _ffmpeg_color(layer.get("color"), DEFAULT_TEXT_COLOR)
            if needs_shaping(text):
                shaped = render_shaped_text(
                    text,
                    workdir / f"text_{index:03d}.png",
                    font_size=int(_num(layer, "size", 32)),
                    color=color,
                )
                centered = layer.get("align") == "center"
                filters.append(
                    PngOverlay(
                        shaped.path,
                        f"{x}+({w}-w)/2" if centered else str(x),
                        f"{y}+({h}-h)/2" if centered else str(y),
                    )
                )
                continue
            text_file = workdir / f"text_{index:03d}.txt"
            text_file.write_text(text, encoding="utf-8")
            if layer.get("align") == "center":
                x_expr, y_expr = f"{x}+({w}-text_w)/2", f"{y}+({h}-text_h)/2"
            else:
                x_expr, y_expr = str(x), str(y)
            filters.append(
                f"drawtext=textfile={_filter_path(text_file)}:x={x_expr}:y={y_expr}:"
                f"fontsize={int(_num(layer, 'size', 32))}:fontcolor={color}"
            )
    return filters


class MotionGraphicsSkill:
    """Render code highlights, diagrams, and callouts as MP4 segments."""

    @property
    def name(self) -> str:
        return "motion_graphics"

    def __init__(self, ctx: JobContext, *, cli_path: str = "hyperframes"):
        self.ctx = ctx
        self.cli_path = cli_path
        self.output_dir = ctx.work_dir / "motion_graphics"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Which renderer produced the most recent ``render()`` output.
        self.last_renderer = "none"

    # ------------------------------------------------------------------
    # Plan builders
    # ------------------------------------------------------------------

    def build_code_highlight_plan(self, spec: CodeHighlightSpec) -> dict[str, Any]:
        """Build a HyperFrames composition plan for a code highlight."""
        return {
            "template": "code_highlight",
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "duration": spec.duration,
            "layers": _code_highlight_layers(spec),
            "camera_drift": spec.camera_drift,
        }

    def build_diagram_plan(self, spec: DiagramSpec) -> dict[str, Any]:
        """Build a HyperFrames composition plan for a diagram."""
        return {
            "template": "diagram",
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "duration": spec.duration,
            "layers": _diagram_layers(spec),
            "camera_drift": spec.camera_drift,
        }

    def build_callout_plan(self, spec: CalloutSpec) -> dict[str, Any]:
        """Build a HyperFrames composition plan for a lower-third callout."""
        return {
            "template": "callout",
            "width": spec.width,
            "height": spec.height,
            "fps": spec.fps,
            "duration": spec.duration,
            "layers": _callout_layers(spec),
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def lint(self, plan: dict[str, Any]) -> list[str]:
        """Run the HyperFrames local lint pass over ``plan``."""
        return lint_plan(plan)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render(
        self,
        plan: dict[str, Any],
        *,
        output_path: Path | str | None = None,
        render_options: dict[str, Any] | None = None,
    ) -> Path:
        """Render ``plan`` to an MP4 segment.

        Uses HyperFrames when the CLI is on PATH; otherwise (or if the
        HyperFrames render fails) falls back to a deterministic ffmpeg render
        so the skill still produces an artifact offline. ``last_renderer``
        records which path produced the output.
        """
        problems = self.lint(plan)
        if problems:
            raise HyperFramesLintError(problems)

        output = (
            Path(output_path)
            if output_path is not None
            else self.output_dir / f"{plan['template']}_{self.ctx.job_id}.mp4"
        )
        output.parent.mkdir(parents=True, exist_ok=True)

        if hyperframes_available(self.cli_path):
            try:
                rendered = self._render_via_hyperframes(plan, output, render_options)
            except RenderSegmentError as exc:
                logger.warning("hyperframes_render_failed_falling_back", error=str(exc)[-500:])
            else:
                self.last_renderer = "hyperframes"
                return rendered
        rendered = self._render_ffmpeg_fallback(plan, output)
        self.last_renderer = "ffmpeg-fallback"
        return rendered

    def _render_via_hyperframes(
        self,
        plan: dict[str, Any],
        output: Path,
        extra: dict[str, Any] | None,
    ) -> Path:
        """Write the plan as an HTML project next to ``output`` and render it."""
        project_dir = output.parent / f"{output.stem}.hf"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True)
        families = [str(layer.get("font", "Inter")) for layer in plan.get("layers", [])]
        families.append("Inter")  # orbit labels use the sans stack
        fonts_css = font_face_css(with_indic_fallbacks(families), project_dir / "assets" / "fonts")
        (project_dir / "index.html").write_text(_plan_to_html(plan, fonts_css), encoding="utf-8")
        return render_project(
            project_dir,
            output,
            cli_path=self.cli_path,
            fps=int(plan["fps"]),
            extra_args=extra,
        )

    def _render_ffmpeg_fallback(
        self,
        plan: dict[str, Any],
        output: Path,
    ) -> Path:
        """Produce a deterministic MP4 without HyperFrames."""
        if shutil.which("ffmpeg") is None:
            raise MotionGraphicsError(
                "ffmpeg is required for the motion graphics fallback renderer"
            )

        width = int(plan["width"])
        height = int(plan["height"])
        fps = int(plan["fps"])
        duration = float(plan["duration"])
        layers = plan.get("layers", [])
        background = next(
            (
                layer
                for layer in layers
                if layer.get("name") == "background" and _num(layer, "opacity", 1.0) > 0
            ),
            None,
        )
        bg_color = _ffmpeg_color(background.get("color") if background else None, DEFAULT_BG_COLOR)

        with tempfile.TemporaryDirectory(prefix="mg_fallback_") as tmp:
            workdir = Path(tmp)
            filters = _fallback_filters(plan, workdir) or ["null"]
            filter_path = workdir / "filters.txt"
            filter_path.write_text(linear_graph(filters) + "\n", encoding="utf-8")
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={bg_color}:s={width}x{height}:d={duration}:r={fps}",
                "-filter_script:v",
                str(filter_path),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ]
            logger.info("motion_graphics_ffmpeg_fallback", cmd=" ".join(cmd))
            try:
                run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
            except FFmpegError as exc:
                raise MotionGraphicsError(f"ffmpeg fallback failed: {exc.stderr or ''}") from exc
        return output

    # ------------------------------------------------------------------
    # Skill protocol entrypoint
    # ------------------------------------------------------------------

    def run(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        render_outputs: bool = True,
    ) -> dict[str, Any]:
        """Render a batch of motion graphics items.

        Each ``item`` is a dict with one of:

        - ``{"kind": "code_highlight", "spec": CodeHighlightSpec(...)}``
        - ``{"kind": "diagram", "spec": DiagramSpec(...)}``
        - ``{"kind": "callout", "spec": CalloutSpec(...)}``

        Returns a dict with ``count`` and ``items`` (per-item rendered
        metadata) and ``manifest`` (path to written JSON manifest).
        """
        self.ctx.ensure_dirs()
        items = items or []
        manifest: list[dict[str, Any]] = []

        for idx, item in enumerate(items):
            kind = item.get("kind")
            spec = item.get("spec")
            if kind == "code_highlight" and isinstance(spec, CodeHighlightSpec):
                plan = self.build_code_highlight_plan(spec)
            elif kind == "diagram" and isinstance(spec, DiagramSpec):
                plan = self.build_diagram_plan(spec)
            elif kind == "callout" and isinstance(spec, CalloutSpec):
                plan = self.build_callout_plan(spec)
            else:
                raise MotionGraphicsError(f"unsupported motion graphics item: {item!r}")

            lint_problems = self.lint(plan)
            output_path: Path | None = None
            renderer = "none"
            if render_outputs:
                output_path = self.output_dir / f"{kind}_{idx:03d}.mp4"
                rendered = self.render(plan, output_path=output_path)
                output_path = rendered
                renderer = self.last_renderer

            manifest.append(
                {
                    "index": idx,
                    "kind": kind,
                    "template": plan["template"],
                    "duration": plan["duration"],
                    "width": plan["width"],
                    "height": plan["height"],
                    "lint_problems": lint_problems,
                    "renderer": renderer,
                    "output": str(output_path) if output_path else None,
                }
            )

        manifest_path = self.output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {"job_id": self.ctx.job_id, "count": len(manifest), "items": manifest}, indent=2
            ),
            encoding="utf-8",
        )

        return {
            "manifest": str(manifest_path),
            "count": len(manifest),
            "items": manifest,
        }
