"""Pack + design + measured alignment → one :class:`Composition`.

This adapter owns *timing*: which layer is on screen and when. Placement and
styling come from :mod:`video_editor_core.layer_presets`; the pack supplies
only words and asset keys. The result is the same IR every other path renders,
so `ve short` and `ve explain` share one compiler, one renderer and one QA gate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    canvas_for_aspect,
    quantise,
)
from video_editor_core.content_pack import (
    END_CARD_ID,
    Alignment,
    ContentPack,
    ContentPackError,
    PackScene,
    TimedScene,
    cue_phrases,
    still_for,
)
from video_editor_core.cues import resolve_cues
from video_editor_core.designs import DesignTokens
from video_editor_core.film_look import light_leak_layers
from video_editor_core.graphics import render_graphic
from video_editor_core.layer_presets import Placed, chip, end_card, pill, scrims
from video_editor_core.render_graph import Rect

#: z-order bands: the still is the floor, the end card the ceiling. A
#: layerized scene splits the floor into a background plate and a cut-out.
Z_STILL = 0
Z_LAYER_FG = 5
#: Half-travel in percent of frame width; the near layer moves more.
LAYER_FAR_RATE = 0.2
LAYER_NEAR_RATE = 0.6
LAYER_BG_RATE = 0.35
LAYER_FG_RATE = 1.0
Z_SCRIM_TOP = 10
Z_SCRIM_BOTTOM = 12
Z_CHIP = 20
Z_PILL = 30
Z_CAPTION = 40
Z_GRAPHIC = 35
Z_END_CARD = 50
Z_END_URL = 55


def _drift(tokens: DesignTokens) -> MotionSpec:
    """The ambient zoom that keeps a still from looking frozen."""
    return MotionSpec(
        name="drift",
        params={"from": "scale(1.0)", "to": f"scale({tokens.motion.drift})"},
    )


def _entry(tokens: DesignTokens) -> MotionSpec:
    return MotionSpec(name=tokens.motion.entry or "rise")


def _still_motion(scene: PackScene, tokens: DesignTokens) -> MotionSpec:
    """The pack's per-scene motion; ``drift`` keeps the series' ambient zoom."""
    return _drift(tokens) if scene.motion == "drift" else MotionSpec(name=scene.motion)


def _parallax(rate: float) -> MotionSpec:
    return MotionSpec(name="parallax", params={"rate": rate})


def _windows(
    pack: ContentPack, alignment: Alignment, fps: int
) -> tuple[list[tuple[PackScene, float, float]], float]:
    """Pack scene windows plus the end-card start, all frame-quantised."""
    aligned = {scene.id: scene for scene in alignment.scenes}
    known = {scene.id for scene in pack.scenes} | {END_CARD_ID}
    unknown = sorted(set(aligned) - known)
    if unknown:
        raise ContentPackError(
            "the alignment names scenes the pack does not have: "
            + ", ".join(repr(name) for name in unknown)
        )
    windows: list[tuple[PackScene, float, float]] = []
    for scene in pack.scenes:
        window = aligned.get(scene.id)
        if window is None:
            raise ContentPackError(f"pack scene {scene.id!r} has no aligned window")
        windows.append((scene, quantise(window.start, fps), quantise(window.end, fps)))
    card = aligned.get(END_CARD_ID)
    card_start = quantise(card.start if card else alignment.duration, fps)
    return windows, card_start


def _still_layer(
    pack: ContentPack,
    scene: PackScene,
    start: float,
    end: float,
    stills: Mapping[str, str | Path],
    tokens: DesignTokens,
    canvas: Canvas,
) -> CompositionLayer:
    key = still_for(pack, scene.id)
    source = stills.get(key)
    if source is None:
        raise ContentPackError(f"no still was generated for key {key!r}")
    return CompositionLayer(
        id=f"still_{scene.id}",
        kind="media",
        preset="base",
        rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
        time=LayerTime(start=start, end=end),
        content=LayerContent(source=str(source), alt=scene.id),
        style={"object_fit": "cover"},
        motions=[_still_motion(scene, tokens)],
        media_kind="image",
        track=0,
        z=Z_STILL,
    )


def _generated_layers(
    scene: PackScene,
    start: float,
    end: float,
    stills: Mapping[str, str | Path],
    canvas: Canvas,
) -> list[CompositionLayer]:
    """The keyed BG/MG/FG stack with a depth-graded parallax (#317)."""
    layers: list[CompositionLayer] = []
    for role, rate, z in (
        ("bg", 0.15, Z_STILL),
        ("mg", 0.45, Z_STILL + 1),
        ("fg", 0.9, Z_STILL + 2),
    ):
        key = f"{scene.id}__{role}"
        source = stills.get(key)
        if source is None:
            raise ContentPackError(f"generated plate {key!r} is missing")
        layers.append(
            _layer_media(
                f"{role}_{scene.id}",
                source,
                z,
                rate,
                scene.id,
                start,
                end,
                canvas,
            )
        )
    return layers


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def _line_starts(
    scene: PackScene,
    timed: TimedScene | None,
    scene_start: float,
    scene_end: float,
) -> list[float]:
    """Approximate each line's first spoken word; fall back to even slices."""
    lines = scene.lines
    if not lines:
        return [scene_start]
    words = list(timed.words) if timed is not None else []
    starts: list[float] = []
    cursor = 0
    for index, line in enumerate(lines):
        wanted = _tokens(line.spoken)
        found: float | None = None
        if wanted:
            for word_index in range(cursor, len(words)):
                word = words[word_index]
                if _tokens(word.display or word.text)[:1] == wanted[:1]:
                    found = word.start
                    cursor = word_index + max(1, len(wanted))
                    break
        if found is None:
            found = scene_start + (scene_end - scene_start) * index / len(lines)
        starts.append(max(scene_start, min(found, scene_end)))
    return starts


def _shot_layers(
    pack: ContentPack,
    scene: PackScene,
    start: float,
    end: float,
    stills: Mapping[str, str | Path],
    tokens: DesignTokens,
    canvas: Canvas,
    timed: TimedScene | None,
) -> list[CompositionLayer]:
    """One media layer per shot, split on the aligned line starts (#314)."""
    starts = _line_starts(scene, timed, start, end)
    layers: list[CompositionLayer] = []
    for index, shot in enumerate(scene.shots):
        shot_start = start if index == 0 else starts[min(shot.at_line, len(starts) - 1)]
        next_line = scene.shots[index + 1].at_line if index + 1 < len(scene.shots) else None
        shot_end = starts[min(next_line, len(starts) - 1)] if next_line is not None else end
        if shot_end <= shot_start:
            shot_end = end if index + 1 == len(scene.shots) else shot_start + 0.1
        if shot.video:
            layers.append(
                CompositionLayer(
                    id=f"shot{index}_{scene.id}",
                    kind="media",
                    preset="base",
                    rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
                    time=LayerTime(start=shot_start, end=shot_end),
                    content=LayerContent(source=shot.video, alt=scene.id),
                    style={"object_fit": "cover"},
                    motions=[_still_motion(scene, tokens)],
                    media_kind="video",
                    track=0,
                    z=Z_STILL,
                )
            )
            continue
        still = shot.still
        key = (still.key or still.reuse) if still is not None else ""
        source = stills.get(key)
        if source is None:
            raise ContentPackError(f"no still was generated for shot key {key!r}")
        layers.append(
            CompositionLayer(
                id=f"shot{index}_{scene.id}",
                kind="media",
                preset="base",
                rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
                time=LayerTime(start=shot_start, end=shot_end),
                content=LayerContent(source=str(source), alt=scene.id),
                style={"object_fit": "cover"},
                motions=[_still_motion(scene, tokens)],
                media_kind="image",
                track=0,
                z=Z_STILL,
            )
        )
    return layers


def _html_shot_layer(
    scene: PackScene, start: float, end: float, canvas: Canvas
) -> CompositionLayer:
    """A sandboxed static-HTML shot fills the frame (P4.9)."""
    return CompositionLayer(
        id=f"html_{scene.id}",
        kind="html",
        preset="html",
        rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
        time=LayerTime(start=start, end=end),
        content=LayerContent(text=scene.html),
        track=0,
        z=Z_STILL,
    )


def _video_layer(
    scene: PackScene,
    start: float,
    end: float,
    tokens: DesignTokens,
    canvas: Canvas,
) -> CompositionLayer:
    """A local video fills the frame; the compiler copies it like a still (#314)."""
    return CompositionLayer(
        id=f"video_{scene.id}",
        kind="media",
        preset="base",
        rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
        time=LayerTime(start=start, end=end),
        content=LayerContent(source=scene.video, alt=scene.id),
        style={"object_fit": "cover"},
        motions=[_drift(tokens)],
        media_kind="video",
        track=0,
        z=Z_STILL,
    )


def _layer_sources(
    pack: ContentPack, scene: PackScene, stills: Mapping[str, str | Path]
) -> dict[str, str | Path]:
    """The layerized assets for one scene, or an error naming the missing key."""
    key = still_for(pack, scene.id)
    sources: dict[str, str | Path] = {}
    for role in ("bg", "fg", "depth"):
        derived = f"{key}__{role}"
        source = stills.get(derived)
        if source is None:
            raise ContentPackError(
                f"scene {scene.id!r} asks for layers but still {derived!r} is missing; "
                "layerize writes <key>__bg, <key>__fg and <key>__depth"
            )
        sources[role] = source
    near = stills.get(f"{key}__near")
    if near is not None:
        sources["near"] = near
    return sources


def _layer_media(
    layer_id: str,
    source: str | Path,
    z: int,
    rate: float,
    scene_id: str,
    start: float,
    end: float,
    canvas: Canvas,
    mask: str | Path | None = None,
) -> CompositionLayer:
    style: dict[str, object] = {"object_fit": "cover"}
    if mask is not None:
        style["mask"] = str(mask)
    return CompositionLayer(
        id=layer_id,
        kind="media",
        preset="base",
        rect=Rect(x=0, y=0, width=canvas.width, height=canvas.height),
        time=LayerTime(start=start, end=end),
        content=LayerContent(source=str(source), alt=scene_id),
        style=style,
        motions=[_parallax(rate)],
        media_kind="image",
        track=0,
        z=z,
    )


def _layerized_layers(
    pack: ContentPack,
    scene: PackScene,
    start: float,
    end: float,
    stills: Mapping[str, str | Path],
    canvas: Canvas,
) -> list[CompositionLayer]:
    """The background plate(s) and the cut-out that replace a scene's still.

    With a depth mask, the background splits into a far plate and a
    depth-masked near copy; without one, it stays a single plate.
    """
    sources = _layer_sources(pack, scene, stills)
    if "near" in sources:
        background = [
            _layer_media(
                f"still_{scene.id}__bg_far",
                sources["bg"],
                Z_STILL,
                LAYER_FAR_RATE,
                scene.id,
                start,
                end,
                canvas,
            ),
            _layer_media(
                f"still_{scene.id}__bg_near",
                sources["bg"],
                Z_STILL + 1,
                LAYER_NEAR_RATE,
                scene.id,
                start,
                end,
                canvas,
                mask=sources["near"],
            ),
        ]
    else:
        background = [
            _layer_media(
                f"still_{scene.id}__bg",
                sources["bg"],
                Z_STILL,
                LAYER_BG_RATE,
                scene.id,
                start,
                end,
                canvas,
            )
        ]
    return [
        *background,
        _layer_media(
            f"still_{scene.id}__fg",
            sources["fg"],
            Z_LAYER_FG,
            LAYER_FG_RATE,
            scene.id,
            start,
            end,
            canvas,
        ),
    ]


def _scrim_layers(
    scene: PackScene,
    start: float,
    end: float,
    tokens: DesignTokens,
    canvas: Canvas,
) -> list[CompositionLayer]:
    layers: list[CompositionLayer] = []
    for index, placed in enumerate(scrims(tokens, canvas)):
        layers.append(
            CompositionLayer(
                id=f"scrim_{index}_{scene.id}",
                kind="shape",
                preset="scrim",
                rect=placed.rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(gradient=placed.content),
                style=dict(placed.style),
                track=0,
                z=Z_SCRIM_TOP if index == 0 else Z_SCRIM_BOTTOM,
            )
        )
    return layers


def _chip_layer(
    scene: PackScene, placed: Placed, start: float, end: float, tokens: DesignTokens
) -> CompositionLayer:
    return CompositionLayer(
        id=f"chip_{scene.id}",
        kind="text",
        preset="chip",
        rect=placed.rect,
        time=LayerTime(start=start, end=end),
        content=LayerContent(text=scene.chip),
        style=dict(placed.style),
        motions=[_entry(tokens)],
        track=0,
        z=Z_CHIP,
    )


def _scene_cue_times(
    scene: PackScene,
    timed: TimedScene,
    start: float,
    end: float,
    phrases: Mapping[str, str],
    fps: int,
) -> dict[str, float]:
    """Cue times for *this* scene, searched among its own spoken words only.

    A time-window search cannot be made exact here: scene windows are
    frame-quantised while measured word starts are not, so at every boundary
    the two disagree by up to half a frame. ``after=start`` then drops a cue
    word that begins a fraction before the quantised start, and the strict
    resolve aborts an otherwise valid build — after the paid stages have run.
    ``timed.words`` is the alignment's own answer to "which words belong to
    this scene", so no tolerance is needed and a phrase spoken in an earlier
    scene cannot answer this scene's cue.

    The reveal is clamped into ``[start, end - 1/fps]``: ``LayerTime``
    requires ``end > start``, and the lead-in can otherwise land before the
    scene opens.

    ``strict``: a cue that matches no spoken word is a content bug the build
    must stop on — the pill would otherwise silently never appear.
    """
    keys = {key: phrase for key, phrase in phrases.items() if key.startswith(f"{scene.id}:")}
    words = [{"word": word.text, "start": word.start, "end": word.end} for word in timed.words]
    ceiling = max(start, end - 1.0 / fps)
    return {
        key: min(max(quantise(time, fps), start), ceiling)
        for key, time in resolve_cues(words, keys, strict=True).items()
    }


def _pill_layers(
    scene: PackScene,
    start: float,
    end: float,
    tokens: DesignTokens,
    canvas: Canvas,
    cue_times: Mapping[str, float],
) -> list[CompositionLayer]:
    layers: list[CompositionLayer] = []
    pill_index = 0
    for line_index, line in enumerate(scene.lines):
        if not line.show:
            continue
        placed = pill(tokens, canvas, index=pill_index)
        # ``_scene_cue_times`` already returns a frame-aligned reveal inside
        # the window; a cue-less line reveals at the scene's start.
        reveal = cue_times.get(f"{scene.id}:{line_index}", start)
        layers.append(
            CompositionLayer(
                id=f"pill_{pill_index}_{scene.id}",
                kind="text",
                preset="pill",
                rect=placed.rect,
                time=LayerTime(start=reveal, end=end),
                content=LayerContent(text=line.show),
                style=dict(placed.style),
                motions=[_entry(tokens)],
                track=0,
                z=Z_PILL + pill_index,
            )
        )
        pill_index += 1
    return layers


def _end_card_layers(
    pack: ContentPack,
    tokens: DesignTokens,
    canvas: Canvas,
    start: float,
    end: float,
) -> list[CompositionLayer]:
    if not pack.end_card.text.strip():
        raise ContentPackError("the pack has no end_card.text; the closing card would be blank")
    text, url = end_card(tokens, canvas)
    return [
        CompositionLayer(
            id="end_card_text",
            kind="text",
            preset="end_card",
            rect=text.rect,
            time=LayerTime(start=start, end=end),
            content=LayerContent(text=pack.end_card.text),
            style=dict(text.style),
            motions=[_entry(tokens)],
            track=0,
            z=Z_END_CARD,
        ),
        CompositionLayer(
            id="end_card_url",
            kind="text",
            preset="end_card",
            rect=url.rect,
            time=LayerTime(start=start, end=end),
            content=LayerContent(text=pack.end_card.url),
            style=dict(url.style),
            motions=[_entry(tokens)],
            track=0,
            z=Z_END_URL,
        ),
    ]


def _place_captions(
    scenes: list[CompositionScene],
    captions: Sequence[CompositionLayer],
    duration: float,
) -> None:
    """Attach each caption layer to the scene whose window contains its start."""
    for layer in captions:
        if layer.time.start >= duration:
            raise ContentPackError(
                f"caption layer {layer.id!r} starts at {layer.time.start:.3f}s, "
                f"past the composition duration {duration:.3f}s"
            )
        for scene in scenes:
            if scene.start <= layer.time.start < scene.end:
                scene.layers.append(layer)
                break
        else:
            raise ContentPackError(
                f"caption layer {layer.id!r} at {layer.time.start:.3f}s is not "
                "inside any scene window"
            )


def pack_to_composition(
    pack: ContentPack,
    tokens: DesignTokens,
    alignment: Alignment,
    *,
    stills: Mapping[str, str | Path],
    captions: Sequence[CompositionLayer] = (),
    fps: int = 30,
) -> Composition:
    """Build the composition the pack describes, on the measured timeline."""
    width, height = canvas_for_aspect(pack.aspect)
    canvas = Canvas(width=width, height=height, fps=fps)
    windows, card_start = _windows(pack, alignment, fps)
    phrases = cue_phrases(pack)
    aligned = {timed.id: timed for timed in alignment.scenes}

    leaks: dict[str, list[CompositionLayer]] = {}
    for scene_id, layer in light_leak_layers(
        tokens, [(scene.id, start, end) for scene, start, end in windows], canvas
    ):
        leaks.setdefault(scene_id, []).append(layer)

    scenes: list[CompositionScene] = []
    for index, (scene, start, end) in enumerate(windows):
        transition_in = None
        if index > 0 and pack.scenes[index - 1].transition_out:
            previous = pack.scenes[index - 1]
            transition_in = MotionSpec(
                name=previous.transition_out,
                duration=previous.transition_seconds,
                ease="linear",
            )
        if scene.shots:
            base = _shot_layers(
                pack, scene, start, end, stills, tokens, canvas, aligned.get(scene.id)
            )
        elif scene.html:
            base = [_html_shot_layer(scene, start, end, canvas)]
        elif scene.video:
            base = [_video_layer(scene, start, end, tokens, canvas)]
        elif scene.layers == "generated":
            base = _generated_layers(scene, start, end, stills, canvas)
        elif scene.layers == "auto":
            base = _layerized_layers(pack, scene, start, end, stills, canvas)
        else:
            base = [_still_layer(pack, scene, start, end, stills, tokens, canvas)]
        layers = [*base, *_scrim_layers(scene, start, end, tokens, canvas)]
        placed_chip = chip(tokens, canvas)
        if placed_chip is not None and scene.chip:
            layers.append(_chip_layer(scene, placed_chip, start, end, tokens))
        layers.extend(
            _pill_layers(
                scene,
                start,
                end,
                tokens,
                canvas,
                _scene_cue_times(scene, aligned[scene.id], start, end, phrases, fps),
            )
        )
        if scene.graphic is not None:
            layers.append(
                CompositionLayer(
                    id=f"graphic_{scene.id}",
                    kind="html",
                    preset="graphic",
                    rect=Rect(x=0, y=0, width=width, height=height),
                    time=LayerTime(start=start, end=end),
                    content=LayerContent(
                        text=render_graphic(
                            scene.graphic.component, scene.graphic.data, tokens, canvas
                        )
                    ),
                    z=Z_GRAPHIC,
                )
            )
            if scene.graphic.over:
                for layer in base:
                    if layer.kind == "media":
                        layer.style["filter"] = "blur(12px) brightness(.6)"
        layers.extend(leaks.get(scene.id, []))
        scenes.append(
            CompositionScene(
                id=scene.id,
                start=start,
                end=end,
                layers=layers,
                transition_in=transition_in,
            )
        )

    if pack.end_card.enabled:
        card_end = quantise(alignment.duration + pack.end_card.tail_seconds, fps)
        last_scene = pack.scenes[-1] if pack.scenes else None
        card_transition = (
            MotionSpec(
                name=last_scene.transition_out,
                duration=last_scene.transition_seconds,
                ease="linear",
            )
            if last_scene is not None and last_scene.transition_out
            else None
        )
        scenes.append(
            CompositionScene(
                id=END_CARD_ID,
                start=card_start,
                end=card_end,
                layers=_end_card_layers(pack, tokens, canvas, card_start, card_end),
                transition_in=card_transition,
            )
        )
    else:
        card_end = quantise(alignment.duration, fps)

    _place_captions(scenes, captions, card_end)

    return Composition(
        job_id=pack.job_id,
        canvas=canvas,
        duration=card_end,
        scenes=scenes,
        audio=[],
        audio_mode="external",
        design=tokens,
    )
