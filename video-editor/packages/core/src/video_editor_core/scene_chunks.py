"""Cut a compiled composition into scene-aligned chunks and slice each one out.

A chunk is a run of whole scenes, cut only where no layer is on screen on both
sides. ``slice_composition`` shifts a chunk to start at 0 and drops the audio,
so the same scenes give the same chunk project wherever they sit in the
timeline (the render-cache key relies on that). Apply
``scene_transitions.apply_scene_transitions`` before planning: a transition's
exit motion lives on the previous scene's layers and must already be baked in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from video_editor_core.composition import Composition, CompositionLayer, quantise

DEFAULT_TARGET_SECONDS = 45.0
DEFAULT_MIN_SECONDS = 12.0
_EPS = 1e-6


def frame_count(seconds: float, fps: int) -> int:
    """Frames HyperFrames renders for ``seconds`` (the overlay_burn formula)."""
    return round(quantise(seconds, fps) * fps)


@dataclass(frozen=True)
class ChunkSpan:
    index: int
    scene_ids: tuple[str, ...]
    start_frame: int
    end_frame: int
    fps: int

    @property
    def frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def start(self) -> float:
        return self.start_frame / self.fps

    @property
    def end(self) -> float:
        return self.end_frame / self.fps

    @property
    def duration(self) -> float:
        return self.frames / self.fps


def _straddles(layer: CompositionLayer, t: float, duration: float) -> bool:
    end = duration if layer.time.end is None else layer.time.end
    return layer.time.start < t - _EPS and end > t + _EPS


def cut_points(composition: Composition) -> set[int]:
    """Scene indexes ``i`` where cutting between scene ``i-1`` and ``i`` splits no layer."""
    scenes = composition.scenes
    layers = [layer for scene in scenes for layer in scene.layers]
    allowed: set[int] = set()
    for i in range(1, len(scenes)):
        t = scenes[i].start
        if not any(_straddles(layer, t, composition.duration) for layer in layers):
            allowed.add(i)
    return allowed


def plan_scene_chunks(
    composition: Composition,
    target_seconds: float = DEFAULT_TARGET_SECONDS,
    min_seconds: float = DEFAULT_MIN_SECONDS,
) -> list[ChunkSpan]:
    """Group scenes into chunks of about ``target_seconds``; merge a short tail."""
    scenes = composition.scenes
    if not scenes:
        raise ValueError("composition has no scenes")
    fps = composition.canvas.fps
    cuts = cut_points(composition)
    groups: list[list[int]] = [[0]]
    for i in range(1, len(scenes)):
        current = groups[-1]
        length = scenes[current[-1]].end - scenes[current[0]].start
        if i in cuts and length >= target_seconds - _EPS:
            groups.append([i])
        else:
            current.append(i)
    if len(groups) > 1:
        tail = groups[-1]
        if scenes[tail[-1]].end - scenes[tail[0]].start < min_seconds - _EPS:
            groups.pop()
            groups[-1].extend(tail)
    total = frame_count(composition.duration, fps)
    spans: list[ChunkSpan] = []
    for n, group in enumerate(groups):
        start_frame = 0 if n == 0 else round(scenes[group[0]].start * fps)
        end_frame = total if n == len(groups) - 1 else round(scenes[group[-1]].end * fps)
        spans.append(
            ChunkSpan(
                index=n,
                scene_ids=tuple(scenes[i].id for i in group),
                start_frame=start_frame,
                end_frame=end_frame,
                fps=fps,
            )
        )
    return spans


def _clamp(t: float, length: float) -> float:
    return min(max(round(t, 6), 0.0), length)


def _shift_layer(layer: CompositionLayer, offset: float, length: float, fps: int) -> CompositionLayer:
    start = _clamp(layer.time.start - offset, length)
    end = None if layer.time.end is None else _clamp(layer.time.end - offset, length)
    if end is not None and end <= start + _EPS:
        end = min(length, start + 1.0 / fps)
    time = layer.time.model_copy(update={"start": start, "end": end})
    return layer.model_copy(update={"time": time})


def slice_composition(composition: Composition, span: ChunkSpan) -> Composition:
    """The chunk's scenes as their own composition: rebased to 0, frame-exact, video only."""
    wanted = set(span.scene_ids)
    picked = [scene for scene in composition.scenes if scene.id in wanted]
    if [scene.id for scene in picked] != list(span.scene_ids):
        raise ValueError(f"chunk {span.index}: scenes {span.scene_ids} not found in order")
    offset = span.start
    length = math.floor(span.duration * 1000 + 1e-6) / 1000
    fps = composition.canvas.fps
    scenes = []
    for n, scene in enumerate(picked):
        start = 0.0 if n == 0 else _clamp(scene.start - offset, length)
        end = length if n == len(picked) - 1 else _clamp(scene.end - offset, length)
        layers = [_shift_layer(layer, offset, length, fps) for layer in scene.layers]
        scenes.append(scene.model_copy(update={"start": start, "end": end, "layers": layers}))
    sliced = composition.model_copy(
        update={"scenes": scenes, "duration": length, "audio": [], "audio_mode": "external"}
    )
    # model_copy skips validation; re-validate so a bad slice fails here, not in Chrome.
    return Composition.model_validate(sliced.model_dump())
