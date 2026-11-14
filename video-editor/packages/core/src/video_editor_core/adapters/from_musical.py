"""Turn a musical segment plan and lyrics into a :class:`Composition`.

The musical path has no content pack and no design: one still or video per
scene, a calm Ken Burns drift on stills, 50% slow motion on video, and one
pop-in caption per lyric line along the bottom. Audio stays external — the
pipeline muxes the music as the sole track and normalises loudness — so this
adapter never embeds it.
"""

from __future__ import annotations

from collections.abc import Sequence

from video_editor_core.composition import (
    Canvas,
    Composition,
    CompositionLayer,
    CompositionScene,
    LayerContent,
    LayerTime,
    MotionSpec,
    quantise,
)
from video_editor_core.musical import MusicalSegment
from video_editor_core.render_graph import Rect

#: z-order bands: media is the floor, captions sit above it.
Z_MEDIA = 0
Z_CAPTION = 40

#: The legacy composer halves both the zoom and the pan amplitude.
SCALE_DELTA = 0.04
PAN_DELTA = 0.01

#: Caption geometry, matching ``scripts/burn_musical_captions.py``: 40 px white
#: text in a two-line box, 20 px padding, 50 px above the bottom edge.
CAPTION_FONT_SIZE = 40
CAPTION_LINE_HEIGHT = 48
CAPTION_PADDING = 20
CAPTION_BOTTOM_MARGIN = 50
CAPTION_SIDE_INSET = 86
CAPTION_POP_SECONDS = 0.35


def from_musical(
    segments: Sequence[MusicalSegment],
    lyrics: Sequence[str],
    *,
    job_id: str,
    canvas: Canvas,
) -> Composition:
    """Build the Composition for ``segments`` and ``lyrics`` (one line each)."""
    if not segments:
        raise ValueError("a musical composition needs at least one segment")

    fps = canvas.fps
    segments = list(segments)
    total = quantise(segments[-1].end, fps)
    # A tail that rounds to a single frame cannot fill a scene of its own: the
    # last caption (which runs to the composition end) then belongs to the
    # scene before it and outlives it. Absorb the tail into that scene instead.
    tail = quantise(segments[-1].end, fps) - quantise(segments[-1].start, fps)
    if len(segments) > 1 and tail <= 1.0 / fps:
        segments.pop()
    scenes: list[CompositionScene] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        start = quantise(segment.start, fps)
        end = total if index == len(segments) - 1 else quantise(segment.end, fps)
        if abs(start - cursor) > 1e-6:
            raise ValueError(f"segment {index} starts at {start:.3f}s, expected {cursor:.3f}s")
        transition = _transition_in(segment, index, segments, fps)
        layer = _media_layer(segment, index, start, end, canvas)
        scenes.append(
            CompositionScene(
                id=f"segment_{index}",
                start=start,
                end=end,
                layers=[layer],
                transition_in=transition,
            )
        )
        cursor = end

    _place_captions(scenes, lyrics, total, canvas, fps)
    return Composition(
        job_id=job_id,
        canvas=canvas,
        duration=total,
        scenes=scenes,
        audio=[],
        audio_mode="external",
    )


def _transition_in(
    segment: MusicalSegment,
    index: int,
    segments: Sequence[MusicalSegment],
    fps: int,
) -> MotionSpec | None:
    if index == 0 or segment.transition_in is None:
        return None
    # Quantise to the frame grid and keep the pre-pass's rule: a transition
    # cannot outlive either adjacent scene.
    duration = quantise(segment.transition_in, fps)
    previous = segments[index - 1]
    limit = min(
        quantise(segment.end, fps) - quantise(segment.start, fps),
        quantise(previous.end, fps) - quantise(previous.start, fps),
    )
    duration = min(duration, limit)
    if duration <= 0:
        return None
    return MotionSpec(name="crossfade", duration=duration)


def _media_layer(
    segment: MusicalSegment,
    index: int,
    start: float,
    end: float,
    canvas: Canvas,
) -> CompositionLayer:
    rect = Rect(x=0, y=0, width=canvas.width, height=canvas.height)
    source = segment.source
    if source.kind == "video":
        return CompositionLayer(
            id=f"segment_{index}",
            kind="media",
            preset="base",
            media_kind="video",
            rect=rect,
            time=LayerTime(start=start, end=end),
            content=LayerContent(source=str(source.path), alt=f"segment {index}"),
            style={
                "object_fit": "cover",
                # The composer trims to half the segment and stretches it to
                # 2x; the runtime expresses the same thing without a pre-pass.
                "playback_rate": 0.5,
                "media_start": 0.0,
            },
            motions=[],
            track=0,
            z=Z_MEDIA,
        )
    return CompositionLayer(
        id=f"segment_{index}",
        kind="media",
        preset="base",
        media_kind="image",
        rect=rect,
        time=LayerTime(start=start, end=end),
        content=LayerContent(source=str(source.path), alt=f"segment {index}"),
        style={"object_fit": "cover"},
        motions=[_drift(index)],
        track=0,
        z=Z_MEDIA,
    )


def _drift(index: int) -> MotionSpec:
    """The composer's halved Ken Burns move, alternating direction per segment."""
    if index % 2 == 0:
        start_scale, end_scale = 1.0, 1.0 + SCALE_DELTA
    else:
        start_scale, end_scale = 1.0 + SCALE_DELTA, 1.0
    # Mirrors the composer's pan terms in ``x=`` (a fraction of the source
    # width). CSS translate percentages are of the element's own width, which
    # is the same fraction for a source that already covers the canvas.
    start_pan = -PAN_DELTA * (1 - 1 / start_scale)
    end_pan = PAN_DELTA * (1 - 1 / end_scale)
    return MotionSpec(
        name="drift",
        params={
            "from": f"scale({start_scale:.2f}) translate({start_pan * 100:.4f}%, 0)",
            "to": f"scale({end_scale:.2f}) translate({end_pan * 100:.4f}%, 0)",
        },
        # The composer's zoompan advances linearly with the frame counter; the
        # default motion ease would finish most of the zoom early.
        ease="linear",
    )


def _caption_layers(
    lyrics: Sequence[str],
    total: float,
    canvas: Canvas,
    fps: int,
) -> list[CompositionLayer]:
    if not lyrics:
        return []
    width = canvas.width - 2 * CAPTION_SIDE_INSET
    text_height = 2 * CAPTION_LINE_HEIGHT
    height = text_height + 2 * CAPTION_PADDING
    # Match the composer: the *text* top is ``height - text_height - 50``; the
    # box adds the padding above and below it.
    text_top = canvas.height - text_height - CAPTION_BOTTOM_MARGIN
    rect = Rect(
        x=CAPTION_SIDE_INSET,
        y=text_top - CAPTION_PADDING,
        width=width,
        height=height,
    )
    layers: list[CompositionLayer] = []
    for index, text in enumerate(lyrics):
        start = quantise(total * index / len(lyrics), fps)
        end = (
            total if index == len(lyrics) - 1 else quantise(total * (index + 1) / len(lyrics), fps)
        )
        if end <= start:
            continue
        layers.append(
            CompositionLayer(
                id=f"caption_{index}",
                kind="text",
                preset="caption_line",
                rect=rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(text=text),
                style={
                    "font": "Noto Sans Devanagari",
                    "font_size": CAPTION_FONT_SIZE,
                    "font_weight": 700,
                    "font_color": "#FFFFFF",
                    "text_effect": "shadow",
                    "background": "rgba(0,0,0,0.55)",
                    "background_hug": True,
                    "padding": CAPTION_PADDING,
                    "valign": "top",
                    "highlight_color": "#FFFFFF",
                },
                motions=[
                    MotionSpec(
                        name="pop",
                        params={"from_scale": 0.55},
                        duration=CAPTION_POP_SECONDS,
                    )
                ],
                track=0,
                z=Z_CAPTION,
            )
        )
    return layers


def _place_captions(
    scenes: list[CompositionScene],
    lyrics: Sequence[str],
    total: float,
    canvas: Canvas,
    fps: int,
) -> None:
    """Attach each caption to the scene whose window contains its start."""
    for layer in _caption_layers(lyrics, total, canvas, fps):
        for index, scene in enumerate(scenes):
            if scene.start <= layer.time.start < scene.end:
                scenes[index] = scene.model_copy(update={"layers": [*scene.layers, layer]})
                break
        else:
            raise ValueError(
                f"caption {layer.id!r} starts at {layer.time.start:.3f}s, "
                "outside every scene window"
            )
