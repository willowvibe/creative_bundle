"""The named-primitive registry and the code that resolves a motion spec."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from video_editor_core.motion.spec import DEFAULT_EASE, MotionCSS, keyframes_name

# ``declaration`` must describe exactly one animation whose only ``<time>`` is
# the duration: ``build_motion`` appends the delay as the shorthand's second
# ``<time>``. A builder that emits its own delay, or a comma-joined pair, would
# silently break that (a third ``<time>`` makes the browser drop the whole
# shorthand; a pair takes the delay on the last animation only).
Builder = Callable[[Mapping[str, Any], str, float, str], MotionCSS]


@dataclass(frozen=True)
class Primitive:
    """One registered motion: how to build it, and its usual length."""

    builder: Builder
    default_duration: float | None = None


PRIMITIVES: dict[str, Primitive] = {}


class MotionError(ValueError):
    """A motion spec the registry cannot compile."""


def register(name: str, *, default_duration: float | None = None) -> Callable[[Builder], Builder]:
    """Register ``builder`` under ``name``.

    ``default_duration=None`` means "run for the whole clip".
    """

    def _wrap(builder: Builder) -> Builder:
        PRIMITIVES[name] = Primitive(builder=builder, default_duration=default_duration)
        return builder

    return _wrap


def build_motion(motion: Any, *, clip_duration: float, tempo: float = 1.0) -> MotionCSS:
    """Compile one motion spec against ``clip_duration`` seconds of clip.

    HyperFrames seeks CSS animations from the clip's own ``data-start``, so a
    motion that outlives its clip is silently cut off. Clamping here means the
    declaration always describes something the viewer can actually see.
    """
    primitive = PRIMITIVES.get(motion.name)
    if primitive is None:
        raise MotionError(f"unknown motion {motion.name!r}; known: {', '.join(sorted(PRIMITIVES))}")
    if clip_duration <= 0:
        raise MotionError(f"clip duration must be positive, got {clip_duration!r}")
    if not isinstance(tempo, int | float) or not math.isfinite(tempo) or tempo <= 0:
        raise MotionError(f"tempo must be a positive finite number, got {tempo!r}")

    delay = _finite(motion.delay, "delay", motion.name)
    if delay < 0:
        raise MotionError(f"motion {motion.name!r} delay must be >= 0, got {delay}")

    requested = motion.duration
    if requested is None:
        duration = (
            clip_duration
            if primitive.default_duration is None
            else primitive.default_duration / tempo
        )
    else:
        duration = _finite(requested, "duration", motion.name)
    if duration <= 0:
        raise MotionError(f"motion {motion.name!r} duration must be positive, got {duration}")

    delay = min(delay, max(0.0, clip_duration - 0.001))
    duration = min(duration, max(0.001, clip_duration - delay))
    name = keyframes_name(motion.name, motion.params)
    built = primitive.builder(dict(motion.params), name, duration, motion.ease or DEFAULT_EASE)
    if not built.declaration:
        # ``count`` writes its own timing into its markup, so there is no
        # ``animation`` value here to postpone — and
        # therefore nowhere for the delay to go. Appending it would silently
        # vanish while the duration above has already been shortened by it,
        # which renders a motion at the wrong time with no error. Refuse
        # instead; a caller that needs a later start moves the clip later.
        if delay > 0:
            raise MotionError(
                f"motion {motion.name!r} draws its own markup and cannot take a "
                f"delay; start the clip later instead (got delay={delay})"
            )
        return built
    # The second ``<time>`` in the ``animation`` shorthand is the delay, so
    # appending it keeps one declaration per animation. A separate
    # ``animation-delay`` list would have to stay aligned with a list the
    # compiler builds by joining declarations, and would silently reset the
    # shorthand's own delay if emitted before it.
    return replace(built, declaration=f"{built.declaration} {delay:.3f}s")


def _finite(value: Any, field: str, motion: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MotionError(f"motion {motion!r} {field} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise MotionError(f"motion {motion!r} {field} must be finite, got {value!r}")
    return number
