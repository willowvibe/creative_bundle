"""Choose a render engine for a :class:`RenderGraph`.

Two engines exist:

* ``ffmpeg`` — a single-pass, CPU/NVENC `filter_complex` renderer. Fast and
  dependency-light, but it only supports the overlays it can express as
  ``drawtext``/``overlay`` filters and cannot do HTML motion, shader
  transitions, or shape layers.
* ``hyperframes`` — renders an HTML composition through Chrome. Far more
  capable (rich motion, shapes, transitions), but ~an order of magnitude slower
  on modest hardware.

The router picks the cheapest engine that can actually render the graph: it
stays on FFmpeg unless the graph explicitly requests HyperFrames or contains a
feature only HyperFrames can render. Callers can always force an engine.
"""

from __future__ import annotations

from video_editor_core.render_graph import RenderGraph

EngineName = str  # "auto" | "ffmpeg" | "hyperframes"

# Layer kinds the FFmpeg compiler cannot draw.
_FFMPEG_UNSUPPORTED_KINDS = frozenset({"shape"})

# ``style`` keys whose presence means the layer expects an HTML renderer.
_HTML_STYLE_KEYS = ("engine", "motion", "requires_motion")


def _style_needs_html(style: dict[str, object]) -> bool:
    for key in _HTML_STYLE_KEYS:
        value = style.get(key)
        if key == "engine":
            if isinstance(value, str) and value.strip().lower() == "hyperframes":
                return True
        elif value is True:
            return True
    return False


def graph_needs_hyperframes(graph: RenderGraph) -> bool:
    """Return True when only the HyperFrames renderer can render ``graph``."""
    if graph.render_preference == "hyperframes":
        return True
    if any(layer.kind in _FFMPEG_UNSUPPORTED_KINDS for layer in graph.overlays):
        return True
    if any(_style_needs_html(layer.style or {}) for layer in graph.overlays):
        return True
    transition = graph.options.transition if graph.options else "none"
    return bool(transition and transition not in {"", "none"})


def choose_engine(
    graph: RenderGraph,
    *,
    requested: EngineName = "auto",
    hyperframes_available: bool = True,
) -> str:
    """Return the engine name (``"ffmpeg"`` or ``"hyperframes"``).

    Args:
        graph: the render graph to render.
        requested: caller override (``"auto"`` defers to graph + heuristics).
        hyperframes_available: whether the HyperFrames CLI can be invoked.
    """
    if requested == "ffmpeg":
        return "ffmpeg"

    # A graph-level preference wins over the CLI default ("auto").
    preference = graph.render_preference
    effective = requested if requested != "auto" else preference

    if effective == "ffmpeg":
        return "ffmpeg"
    if effective == "hyperframes":
        return "hyperframes" if hyperframes_available else "ffmpeg"

    # auto: HyperFrames only when the graph needs it.
    if hyperframes_available and graph_needs_hyperframes(graph):
        return "hyperframes"
    return "ffmpeg"
