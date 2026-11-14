"""Emit a scene's declared entrance as per-layer motion.

Scenes are a compile-time grouping only — every visual layer is emitted as a
root-level clip — so a scene-level transition has to become motion on the
layers themselves. A crossfade has two halves:

* every layer of the incoming scene fades in over the transition window;
* every layer of the outgoing scene that spans the whole scene stays visible
  past the boundary and fades out over the same window.

Layers that end before their scene does (a caption, a pill revealed late) are
left alone: extending them would keep stale content on screen.

The pass is pure and idempotent. It returns a new :class:`Composition`, clears
``transition_in`` on the scenes it processed, and never mutates its input, so
calling it twice renders the same frames as calling it once.
"""

from __future__ import annotations

from video_editor_core.composition import (
    Composition,
    CompositionLayer,
    CompositionScene,
    MotionSpec,
)

#: Transition name -> (incoming motion, outgoing motion or None, ease).
#: All motions are seek-safe primitives; a hard cut (``None`` outgoing) does not
#: extend the outgoing layers.
_TRANSITIONS: dict[str, tuple[str, str | None, str]] = {
    "cut": ("", None, "linear"),
    "crossfade": ("fade", "fade_out", "linear"),
    "fade": ("fade", "fade_out", "linear"),
    "ink_bleed": ("mask_reveal", "fade_out", "linear"),
    "mist_dissolve": ("fade", "fade_out", "ease-in-out"),
    "push_through": ("camera", None, "ease-out"),
    "hanji_turn": ("flip", "flip_out", "ease-in-out"),
    "seal_stamp": ("stamp", None, "ease-out"),
}

#: Entrance names this module can emit.
SUPPORTED_TRANSITIONS: frozenset[str] = frozenset(_TRANSITIONS)

#: Times closer than this are the same frame boundary.
_EPSILON = 1e-6


class SceneTransitionError(ValueError):
    """A scene transition the pre-pass cannot emit."""


def apply_scene_transitions(composition: Composition) -> Composition:
    """Return ``composition`` with every ``scene.transition_in`` emitted."""
    if not any(scene.transition_in is not None for scene in composition.scenes):
        return composition

    scenes: list[CompositionScene] = []
    for index, scene in enumerate(composition.scenes):
        transition = scene.transition_in
        if transition is None:
            scenes.append(scene)
            continue
        previous = composition.scenes[index - 1] if index else None
        duration = _transition_duration(transition, scene, previous)
        incoming_name, outgoing_name, ease = _TRANSITIONS[transition.name]
        layers = (
            [_enter(layer, duration, incoming_name, ease) for layer in scene.layers]
            if incoming_name
            else list(scene.layers)
        )
        if previous is not None and outgoing_name is not None:
            scenes[-1] = _exit(scenes[-1], duration, composition.duration, outgoing_name, ease)
        scenes.append(scene.model_copy(update={"layers": layers, "transition_in": None}))
    return composition.model_copy(update={"scenes": scenes})


def _transition_duration(
    transition: MotionSpec,
    scene: CompositionScene,
    previous: CompositionScene | None,
) -> float:
    if transition.name not in SUPPORTED_TRANSITIONS:
        raise SceneTransitionError(
            f"scene {scene.id!r}: unknown transition {transition.name!r}; known: "
            f"{', '.join(sorted(SUPPORTED_TRANSITIONS))}"
        )
    if transition.params:
        raise SceneTransitionError(
            f"scene {scene.id!r}: transition {transition.name!r} takes no params; "
            f"got {', '.join(sorted(transition.params))}"
        )
    if transition.delay:
        raise SceneTransitionError(
            f"scene {scene.id!r}: transition {transition.name!r} cannot be delayed; "
            "the scene starts when it starts"
        )
    if transition.duration is None:
        raise SceneTransitionError(
            f"scene {scene.id!r}: transition {transition.name!r} needs an explicit duration"
        )
    duration = float(transition.duration)
    limit = scene.end - scene.start
    if previous is not None:
        limit = min(limit, previous.end - previous.start)
    if duration > limit + _EPSILON:
        if previous is None:
            detail = f"longer than the scene's {limit:.3f}s window"
        else:
            detail = f"longer than the {limit:.3f}s both scenes have in common"
        raise SceneTransitionError(
            f"scene {scene.id!r}: transition {transition.name!r} lasts {duration:.3f}s, {detail}"
        )
    return duration


def _enter(layer: CompositionLayer, duration: float, name: str, ease: str) -> CompositionLayer:
    # Append, do not prepend: the compiler emits ``animation: a, b`` and the
    # later animation wins on a shared property. A layer that already has
    # ``rise`` or ``pop`` would otherwise keep its own opacity ramp and cut the
    # crossfade short. Appended, this fade owns opacity and the authored move
    # keeps its transform.
    #
    # A crossfade is linear, like a dissolve or FFmpeg's ``xfade``; the default
    # motion ease would bias the blend and make the two sides disagree at the
    # same instant.
    motion = MotionSpec(name=name, duration=duration, ease=ease)
    return layer.model_copy(update={"motions": [*layer.motions, motion]})


def _exit(
    scene: CompositionScene,
    duration: float,
    total: float,
    name: str,
    ease: str,
) -> CompositionScene:
    """Extend and fade out the layers that cover ``scene``'s whole window."""
    layers: list[CompositionLayer] = []
    for layer in scene.layers:
        end = layer.time.end
        if end is not None and end < scene.end - _EPSILON:
            layers.append(layer)
            continue
        new_end = (scene.end if end is None else end) + duration
        if new_end > total + _EPSILON:
            raise SceneTransitionError(
                f"layer {layer.id!r} would end at {new_end:.3f}s, past the composition "
                f"duration {total:.3f}s; it already outlives its scene"
            )
        # Start the fade at the scene boundary, not at the layer's old end: a
        # layer that already reached past the boundary must still cross-fade
        # with the incoming scene.
        delay = max(0.0, scene.end - layer.time.start)
        motion = MotionSpec(name=name, duration=duration, delay=delay, ease=ease)
        layers.append(
            layer.model_copy(
                update={
                    "time": layer.time.model_copy(update={"end": new_end}),
                    "motions": [*layer.motions, motion],
                }
            )
        )
    return scene.model_copy(update={"layers": layers})
