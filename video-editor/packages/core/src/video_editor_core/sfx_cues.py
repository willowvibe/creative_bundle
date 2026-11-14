"""Turn a Composition's motion into sound-effect cues.

The visual IR already knows when a layer appears and which primitive moves it;
that is the cue table. No detection pass is needed: a `stamp` at
``layer.time.start + motion.delay`` is a hit, a `pop` is a click, a `flip` is a
card turn, and a scene transition is a whoosh.

The table is deliberately small. Ambient primitives (`drift`, `pulse`) and the
transition's own `fade`/`fade_out` motions are **not** cues: `rise` is the pack
default on every chip, pill and end card, so mapping it would fire a whoosh on
every text element. Captions are skipped too; their pop is a reveal, not a
sound event. A pack with no cued primitives can opt into one whoosh per cut
with `scene_cuts=True`.

Call this on the Composition the adapter built, before
``apply_scene_transitions`` clears ``transition_in``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from video_editor_core.composition import Composition


@dataclass(frozen=True)
class CueSpec:
    """The sound a primitive maps to, and its mix level (linear gain)."""

    sound: str
    volume: float = 1.0


DEFAULT_CUE_TABLE: Mapping[str, CueSpec] = {
    "stamp": CueSpec("hit", 1.0),
    "pop": CueSpec("click", 0.8),
    "cursor": CueSpec("click", 0.9),
    "flip": CueSpec("card_flip", 0.9),
    "lock": CueSpec("latch", 0.9),
}

#: A scene entrance has no layer motion of its own; it gets this cue.
TRANSITION_CUE = CueSpec("whoosh", 0.5)

#: Lower wins when two cues land within ``min_gap`` of each other.
_PRIORITY: Mapping[str, int] = {
    "hit": 0,
    "click": 1,
    "card_flip": 2,
    "latch": 3,
    "whoosh": 4,
}


class SfxCue(BaseModel):
    """One sound to place at an absolute timeline second."""

    id: str
    sound: str
    start: float = Field(ge=0.0)
    volume: float = Field(default=1.0, gt=0.0)
    layer_id: str = ""
    motion: str = ""


def cues_from_composition(
    composition: Composition,
    *,
    table: Mapping[str, CueSpec] = DEFAULT_CUE_TABLE,
    max_cues: int = 12,
    min_gap: float = 0.05,
    scene_cuts: bool = False,
) -> list[SfxCue]:
    """Return the cue list for ``composition``, earliest first.

    ``max_cues`` keeps a build from sonifying every layer; ``min_gap`` collapses
    a cluster (a stamp and a pop in the same frame) to its earliest cue.
    ``scene_cuts`` adds a whoosh at every scene boundary after the first, which
    is what makes the shipped `rise`-only packs audible at all. The result is
    deterministic for a given composition and table.
    """
    if max_cues <= 0:
        raise ValueError(f"max_cues must be positive, got {max_cues!r}")
    if min_gap < 0:
        raise ValueError(f"min_gap must be >= 0, got {min_gap!r}")

    raw: list[tuple[float, int, str, str, float, str]] = []
    for index, scene in enumerate(composition.scenes):
        if scene.transition_in is not None:
            transition_motion = "scene.transition"
        elif scene_cuts and index > 0:
            transition_motion = "scene.cut"
        else:
            transition_motion = ""
        if transition_motion:
            raw.append(
                (
                    round(scene.start, 3),
                    _priority(TRANSITION_CUE.sound),
                    TRANSITION_CUE.sound,
                    scene.id,
                    TRANSITION_CUE.volume,
                    transition_motion,
                )
            )
        for layer in scene.layers:
            if layer.preset == "caption_line":
                continue
            for motion in layer.motions:
                spec = table.get(motion.name)
                if spec is None:
                    continue
                start = layer.time.start + motion.delay
                if start >= composition.duration:
                    continue
                raw.append(
                    (
                        round(start, 3),
                        _priority(spec.sound),
                        spec.sound,
                        layer.id,
                        spec.volume,
                        motion.name,
                    )
                )

    raw.sort(key=lambda item: (item[0], item[1], item[3]))
    kept: list[tuple[float, int, str, str, float, str]] = []
    for item in raw:
        if kept and item[0] - kept[-1][0] < min_gap:
            continue
        kept.append(item)

    return [
        SfxCue(
            id=f"{sound}_{int(round(start * 1000)):07d}",
            sound=sound,
            start=start,
            volume=volume,
            layer_id=layer_id,
            motion=motion,
        )
        for start, _priority_value, sound, layer_id, volume, motion in kept[:max_cues]
    ]


def _priority(sound: str) -> int:
    return _PRIORITY.get(sound, len(_PRIORITY))


def cue_table_from_dict(data: Mapping[str, Any]) -> dict[str, CueSpec]:
    """Build a cue table from ``{primitive: {"sound": ..., "volume": ...}}``."""
    table: dict[str, CueSpec] = {}
    for primitive, value in data.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"cue {primitive!r} must be an object")
        sound = str(value.get("sound") or "")
        if not sound:
            raise ValueError(f"cue {primitive!r} has no sound")
        volume = float(value.get("volume", 1.0))
        if volume <= 0:
            raise ValueError(f"cue {primitive!r} volume must be positive")
        table[str(primitive)] = CueSpec(sound, volume)
    return table
