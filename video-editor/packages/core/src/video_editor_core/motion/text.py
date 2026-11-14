"""Structural and text motion primitives.

``draw`` works on SVG strokes; ``count`` renders its own markup because CSS
cannot change text content; ``word_highlight`` is a validation-only marker —
the rich-text emitter colours every word from the layer's ``highlight_color``,
so the primitive contributes no CSS of its own and exists here only so a plan
may name it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from video_editor_core.motion.primitives import _css_num, _keyframes, _num
from video_editor_core.motion.registry import MotionError, register
from video_editor_core.motion.spec import MotionCSS


@register("draw", default_duration=0.8)
def draw(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Stroke a path in. The compiler sets ``pathLength="1"`` on each path."""
    return MotionCSS(
        keyframes=_keyframes(name, "stroke-dashoffset:1", "stroke-dashoffset:0"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="stroke-dasharray:1;fill:none;",
    )


@register("count", default_duration=1.2)
def count(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """A digit column translated inside a one-line window — no JavaScript.

    The animation lives on the strip inside ``markup``; the layer itself gets
    no ``animation`` declaration, which is why ``declaration`` is empty.
    """
    first = int(_num(params, "from", 0))
    last = int(_num(params, "to", 10))
    if last < first:
        raise MotionError(f"count 'to' ({last}) must be >= 'from' ({first})")
    steps = last - first
    declaration = f"{name} {duration:.3f}s {ease} both"
    digits = "".join(
        f'<span style="display:block;height:1em;">{value}</span>'
        for value in range(first, last + 1)
    )
    markup = (
        '<span class="ve-count" style="display:inline-block;overflow:hidden;'
        'height:1em;vertical-align:bottom;">'
        f'<span class="ve-count-strip" style="display:block;animation:{declaration};">'
        f"{digits}</span></span>"
    )
    return MotionCSS(
        keyframes=_keyframes(
            name, "transform: translateY(0)", f"transform: translateY(-{steps}em)"
        ),
        declaration="",
        markup=markup,
    )


@register("pill", default_duration=0.34)
def pill(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    distance = _num(params, "distance", 16.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d(0, {_css_num(distance)}px, 0)",
            "opacity:1; transform: translate3d(0, 0, 0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("word_highlight")
def word_highlight(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Marker: contributes no CSS; see the module docstring."""
    return MotionCSS()


@register("type_on")
def type_on(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Validation-only marker: the compiler splits the layer text into chars.

    The per-character spans and their stagger live in ``compile_composition``
    because the primitive does not receive the layer's text.
    """
    return MotionCSS()


@register("cursor", default_duration=1.2)
def cursor(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """A synthetic pointer travelling along an SVG path (#P4.7).

    ``path`` is a valid CSS ``offset-path`` (``path("M ...")`` value without the
    wrapper), and the pointer element is emitted as markup. A click SFX is
    attached by the cue table.
    """
    path = str(params.get("path") or "").strip()
    if not path:
        raise MotionError("cursor needs a 'path' (SVG path data)")
    size = float(params.get("size", 18.0))
    markup = (
        f'<span class="ve-cursor" style="position:absolute;left:0;top:0;'
        f"width:{_css_num(size)}px;height:{_css_num(size)}px;border-radius:50%;"
        f"background:var(--ve-cursor,#fff);box-shadow:0 0 0 3px rgba(0,0,0,.25);"
        f"offset-path:path('{path}');offset-rotate:0deg;"
        f'animation:{name} {duration:.3f}s {ease} both;"></span>'
    )
    return MotionCSS(
        keyframes=_keyframes(name, "offset-distance:0%", "offset-distance:100%"),
        declaration="",
        markup=markup,
    )
