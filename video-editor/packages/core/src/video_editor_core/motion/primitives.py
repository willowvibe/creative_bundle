"""Transform motion primitives.

``drift`` reproduces the four Ken Burns moves the scene-plan compiler shipped
(``KEN_BURNS_MOVES``) bit-for-bit: the parity rebuild compares frames, so a
"similar" move is a bug.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from video_editor_core.motion.registry import MotionError, register
from video_editor_core.motion.spec import MotionCSS

KEN_BURNS_SCALE_END = 1.12
KEN_BURNS_MOVES: tuple[tuple[str, str], ...] = (
    ("scale(1) translate(0, 0)", f"scale({KEN_BURNS_SCALE_END}) translate(0, -1%)"),
    ("scale(1.08) translate(-2.5%, 0)", "scale(1.08) translate(2.5%, 0)"),
    (f"scale({KEN_BURNS_SCALE_END}) translate(0, -1%)", "scale(1.02) translate(0, 0)"),
    ("scale(1.08) translate(2.5%, 1%)", "scale(1.08) translate(-2.5%, -1%)"),
)


def _keyframes(name: str, start: str, end: str) -> str:
    return f"@keyframes {name} {{ from {{ {start}; }} to {{ {end}; }} }}"


def _num(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        raise MotionError(f"{key} must be a number, got {value!r}") from None


def _css_num(value: float) -> str:
    """Format ``value`` the compact way CSS accepts: no leading zero."""
    text = f"{value:g}"
    if text.startswith("0."):
        return text[1:]
    if text.startswith("-0."):
        return "-" + text[2:]
    return text


@register("drift")
def drift(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Slow zoom/pan. ``move`` picks a Ken Burns pair; from/to override it."""
    if "from" in params or "to" in params:
        start = str(params.get("from") or "scale(1)")
        end = str(params.get("to") or "scale(1.08)")
    else:
        move = int(_num(params, "move", 0))
        start, end = KEN_BURNS_MOVES[move % len(KEN_BURNS_MOVES)]
    return MotionCSS(
        keyframes=_keyframes(name, f"transform: {start}", f"transform: {end}"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;",
    )


@register("parallax")
def parallax(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Pan one depth layer sideways.

    ``rate`` is the half-travel in percent of the frame width per clip: a
    background at ``0.35`` moves less than a cut-out foreground at ``1.0``,
    which is what reads as depth. Layers are scaled slightly so the pan never
    exposes an edge. ``from``/``to`` override the pair for a custom move.
    """
    rate = _num(params, "rate", 1.0)
    scale = _num(params, "scale", 1.04)
    start = str(
        params.get("from") or f"scale({_css_num(scale)}) translate3d({_css_num(rate)}%,0,0)"
    )
    end = str(params.get("to") or f"scale({_css_num(scale)}) translate3d({_css_num(-rate)}%,0,0)")
    return MotionCSS(
        keyframes=_keyframes(name, f"transform: {start}", f"transform: {end}"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;",
    )


@register("rise", default_duration=0.45)
def rise(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    distance = _num(params, "distance", 22.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d(0,{_css_num(distance)}px,0)",
            "opacity:1; transform: translate3d(0,0,0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("pop", default_duration=0.4)
def pop(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    start = _num(params, "from_scale", 0.94)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: scale({_css_num(start)})",
            "opacity:1; transform: scale(1)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("fade", default_duration=0.45)
def fade(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    return MotionCSS(
        keyframes=_keyframes(name, "opacity:0", "opacity:1"),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("fade_out", default_duration=0.45)
def fade_out(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Fade a clip out over its last seconds; ``forwards`` holds the last frame.

    The scene-transition pre-pass extends a clip and places this motion at the
    clip's tail, so without ``forwards`` the clip would snap back to full
    opacity at the end of the animation and reappear over the next scene.
    """
    return MotionCSS(
        keyframes=_keyframes(name, "opacity:1", "opacity:0"),
        declaration=f"{name} {duration:.3f}s {ease} forwards",
    )


@register("stamp", default_duration=0.5)
def stamp(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """The brief's Stamp: 120% -> 100% with an ink-bleed blur that settles."""
    start = _num(params, "from_scale", 1.2)
    blur = _num(params, "blur", 10.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: scale({_css_num(start)}); filter: blur({_css_num(blur)}px)",
            "opacity:1; transform: scale(1); filter: blur(0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("flip", default_duration=0.6)
def flip(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """A card turning over to reveal what is on its face."""
    return MotionCSS(
        keyframes=_keyframes(
            name,
            "opacity:0; transform: perspective(1200px) rotateY(90deg)",
            "opacity:1; transform: perspective(1200px) rotateY(0deg)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;backface-visibility:hidden;",
    )


@register("lock", default_duration=0.55)
def lock(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Two halves that slide in from opposite sides and lock together."""
    side = str(params.get("side", "left")).lower()
    if side not in {"left", "right"}:
        raise MotionError(f"lock side must be 'left' or 'right', got {side!r}")
    distance = _num(params, "distance", 60.0)
    start = -distance if side == "left" else distance
    return MotionCSS(
        keyframes=_keyframes(
            name,
            f"opacity:0; transform: translate3d({_css_num(start)}px,0,0)",
            "opacity:1; transform: translate3d(0,0,0)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
    )


@register("pulse", default_duration=1.6)
def pulse(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    peak = _num(params, "peak", 1.35)
    return MotionCSS(
        keyframes=_keyframes(
            name, "filter: brightness(1)", f"filter: brightness({_css_num(peak)})"
        ),
        declaration=f"{name} {duration:.3f}s ease-in-out infinite alternate",
    )


@register("wipe", default_duration=0.5)
def wipe(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Reveal a layer from one edge with ``clip-path`` (P4.7)."""
    direction = str(params.get("direction", "right")).lower()
    insets = {
        "right": ("inset(0 100% 0 0)", "inset(0 0 0 0)"),
        "left": ("inset(0 0 0 100%)", "inset(0 0 0 0)"),
        "down": ("inset(0 0 100% 0)", "inset(0 0 0 0)"),
        "up": ("inset(100% 0 0 0)", "inset(0 0 0 0)"),
    }
    if direction not in insets:
        raise MotionError(
            f"wipe direction must be one of {', '.join(sorted(insets))}, got {direction!r}"
        )
    start, end = insets[direction]
    return MotionCSS(
        keyframes=_keyframes(name, f"clip-path: {start}", f"clip-path: {end}"),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="will-change:clip-path;",
    )


@register("camera", default_duration=0.9)
def camera(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """A perspective dolly on a 2.5D stack (P4.7)."""
    distance = _num(params, "distance", 80.0)
    rotate = _num(params, "rotate", 0.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            "transform: perspective(1200px) translate3d(0,0,0) rotateX(0deg)",
            f"transform: perspective(1200px) translate3d(0,0,{_css_num(-distance)}px) "
            f"rotateX({_css_num(rotate)}deg)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;",
    )


@register("mask_reveal", default_duration=0.6)
def mask_reveal(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Reveal from the centre with an expanding ``clip-path`` circle (P4.7)."""
    radius = _num(params, "radius", 75.0)
    return MotionCSS(
        keyframes=_keyframes(
            name,
            "clip-path: circle(0% at 50% 50%)",
            f"clip-path: circle({_css_num(radius)}% at 50% 50%)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="will-change:clip-path;",
    )


@register("flip_out", default_duration=0.6)
def flip_out(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Turn a card away from the viewer (the outgoing half of ``hanji_turn``)."""
    return MotionCSS(
        keyframes=_keyframes(
            name,
            "opacity:1; transform: perspective(1200px) rotateY(0deg)",
            "opacity:0; transform: perspective(1200px) rotateY(-90deg)",
        ),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="transform-origin:center;backface-visibility:hidden;",
    )


@register("morph", default_duration=0.6)
def morph(params: Mapping[str, Any], name: str, duration: float, ease: str) -> MotionCSS:
    """Morph CSS properties between two states (a shared-element substitute).

    ``from``/``to`` are raw declaration strings (radius, scale, background).
    True FLIP needs the destination layout, which the IR does not carry, so a
    pack supplies the two states explicitly.
    """
    start = str(params.get("from") or "border-radius:12px; transform:scale(.92)")
    end = str(params.get("to") or "border-radius:999px; transform:scale(1)")
    return MotionCSS(
        keyframes=_keyframes(name, start, end),
        declaration=f"{name} {duration:.3f}s {ease} both",
        props="will-change:transform,border-radius;",
    )
