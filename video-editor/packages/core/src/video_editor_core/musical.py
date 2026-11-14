"""Plan the segments of a musical video on the final, crossfaded timeline.

The legacy composer (`scripts/compose_musical_video.py`) builds an FFmpeg
``xfade`` chain and lets FFmpeg compute the overlapping timeline. The IR needs
that timeline up front: scene windows are contiguous, each boundary carries the
transition FFmpeg would have used, and every segment knows how much source
material it consumes (the visible window plus the tail the next crossfade
eats).

The arithmetic here is a direct port of ``_build_filtergraph`` and
``_build_xfade_chain``; the regression cases in ``tests/test_compose_musical_video.py``
are mirrored in ``tests/test_musical.py`` so the port and the script cannot
drift. Times are left unquantised on purpose — the adapter quantises the scene
windows when it builds the :class:`~video_editor_core.composition.Composition`,
exactly as the other adapters do.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MediaKind = Literal["image", "video"]

#: Extensions the composer treats as moving media. Animated GIFs go through the
#: video branch so their frames are decoded instead of collapsed to one still.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({"mp4", "mov", "m4v", "mkv", "webm", "avi", "gif"})
IMAGE_EXTENSIONS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tiff"})


class MusicalPlanError(ValueError):
    """A musical request that cannot be laid out on one timeline."""


@dataclass(frozen=True)
class MusicalSource:
    """One image or video the composer cycles through."""

    path: Path
    kind: MediaKind
    #: Probed source length for video; ``None`` for images and for a video the
    #: caller has not probed. The planner does not need it; the adapter uses it
    #: to decide whether a slowed clip needs padding.
    duration: float | None = None


@dataclass(frozen=True)
class MusicalSegment:
    """One scene on the final timeline."""

    source: MusicalSource
    start: float
    #: Scene window end; the next scene starts here.
    end: float
    #: Source material the segment consumes: ``end - start`` plus the tail the
    #: next crossfade overlaps. Equals FFmpeg's ``source_dur`` for this segment.
    clip_duration: float
    #: Length of the crossfade that brings this scene in; ``None`` for the first.
    transition_in: float | None
    #: Position in the media cycle; drives the alternating Ken Burns direction.
    motion_index: int

    @property
    def visible(self) -> float:
        return self.end - self.start


def discover_media(
    media_dir: Path | str,
    *,
    exclude: Iterable[Path | str] = (),
) -> list[MusicalSource]:
    """List the supported media in ``media_dir``, sorted and de-duplicated.

    Mirrors the legacy composer's discovery: sorted files, no generated
    intermediates (``fixed_audio`` / ``_remux`` stems), and the output and music
    passed in ``exclude`` skipped. Raises rather than returning an empty list so
    a mistyped directory fails at plan time.
    """
    directory = Path(media_dir)
    if not directory.is_dir():
        raise MusicalPlanError(f"media directory not found: {directory}")
    excluded = {Path(path).resolve() for path in exclude}
    found: list[MusicalSource] = []
    seen: set[Path] = set()
    for child in sorted(directory.iterdir()):
        if not child.is_file():
            continue
        suffix = child.suffix.lower().lstrip(".")
        if suffix in IMAGE_EXTENSIONS:
            kind: MediaKind = "image"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "video"
        else:
            continue
        resolved = child.resolve()
        if resolved in excluded or resolved in seen:
            continue
        stem = child.stem.lower()
        if "fixed_audio" in stem or "_remux" in stem:
            continue
        seen.add(resolved)
        found.append(MusicalSource(path=child, kind=kind))
    if not found:
        raise MusicalPlanError(f"no supported media found in {directory}")
    return found


def plan_segments(
    sources: Sequence[MusicalSource],
    music_duration: float,
    *,
    segment_seconds: float,
    transition_seconds: float,
    boundaries: Sequence[float] | None = None,
) -> list[MusicalSegment]:
    """Lay ``sources`` over ``music_duration`` with a crossfade at each boundary.

    Segments cycle through ``sources`` in order, matching the composer's
    ``media_paths[i % len(media_paths)]``. The last segment may be shorter than
    ``segment_seconds`` when the music ends mid-segment.

    ``boundaries`` overrides the uniform grid with explicit interior cut times
    (the beat-quantized path, P4.6); it excludes 0 and ``music_duration``.
    """
    if not sources:
        raise MusicalPlanError("at least one media source is required")
    for index, source in enumerate(sources):
        if source.kind not in {"image", "video"}:
            raise MusicalPlanError(
                f"source {index}: unknown kind {source.kind!r}; use 'image' or 'video'"
            )
        if source.duration is not None and (
            not math.isfinite(source.duration) or source.duration <= 0
        ):
            raise MusicalPlanError(
                f"source {index}: duration must be a positive finite number, "
                f"got {source.duration!r}"
            )
    _require_positive("music_duration", music_duration)
    _require_positive("segment_seconds", segment_seconds)
    if not math.isfinite(transition_seconds) or transition_seconds <= 0:
        raise MusicalPlanError(
            f"transition_seconds must be a positive finite number, got {transition_seconds!r}"
        )
    if transition_seconds >= segment_seconds:
        raise MusicalPlanError(
            "transition_seconds must be less than segment_seconds "
            f"(got {transition_seconds:g} and {segment_seconds:g})"
        )

    if boundaries is None:
        count = max(1, math.ceil(music_duration / segment_seconds))
        edges = [
            0.0,
            *[min(index * segment_seconds, music_duration) for index in range(1, count)],
            music_duration,
        ]
    else:
        cuts = [float(value) for value in boundaries]
        if any(value <= 0.0 or value >= music_duration for value in cuts):
            raise MusicalPlanError("every boundary must sit strictly inside the music")
        if any(later <= earlier for earlier, later in zip(cuts, cuts[1:], strict=False)):
            raise MusicalPlanError("boundaries must be strictly increasing")
        edges = [0.0, *cuts, music_duration]

    spans = [
        (start, end) for start, end in zip(edges, edges[1:], strict=False) if end > start + 1e-9
    ]
    if not spans:
        raise MusicalPlanError("the music duration produced no usable segments")
    count = len(spans)
    source_durations: list[float] = []
    for index, (start, end) in enumerate(spans):
        extra = transition_seconds if index < count - 1 else 0.0
        source_durations.append(min((end - start) + extra, music_duration - start))
    if not source_durations:
        raise MusicalPlanError("the music duration produced no usable segments")

    offsets, transitions, total = _xfade_timeline(source_durations, transition_seconds)

    segments: list[MusicalSegment] = []
    last = len(source_durations) - 1
    for index, clip_duration in enumerate(source_durations):
        start = 0.0 if index == 0 else offsets[index - 1]
        end = total if index == last else offsets[index]
        segments.append(
            MusicalSegment(
                source=sources[index % len(sources)],
                start=start,
                end=end,
                clip_duration=clip_duration,
                transition_in=None if index == 0 else transitions[index - 1],
                motion_index=index,
            )
        )

    _validate_windows(segments, total)
    return segments


def _xfade_timeline(
    source_durations: Sequence[float],
    transition_seconds: float,
) -> tuple[list[float], list[float], float]:
    """Return ``(offsets, transitions, total)`` for the xfade chain.

    Mirrors ``_build_xfade_chain``: each offset is computed from the running
    output length, and each transition is clamped to the material available on
    both sides (bug #193).
    """
    offsets: list[float] = []
    transitions: list[float] = []
    previous = source_durations[0]
    for index in range(len(source_durations) - 1):
        following = source_durations[index + 1]
        transition = min(transition_seconds, previous, following)
        offset = previous - transition
        offsets.append(offset)
        transitions.append(transition)
        previous = offset + following
    return offsets, transitions, previous


def _validate_windows(segments: Sequence[MusicalSegment], total: float) -> None:
    for index, segment in enumerate(segments):
        if segment.end <= segment.start:
            raise MusicalPlanError(
                f"segment {index} ends at {segment.end:.3f}s but starts at {segment.start:.3f}s"
            )
        if index == 0:
            if abs(segment.start) > 1e-9:
                raise MusicalPlanError(f"the first segment starts at {segment.start:.3f}s")
            continue
        previous = segments[index - 1]
        if abs(segment.start - previous.end) > 1e-6:
            raise MusicalPlanError(
                f"segment {index} starts at {segment.start:.3f}s but segment "
                f"{index - 1} ends at {previous.end:.3f}s"
            )
        transition = segment.transition_in
        if transition is None:
            raise MusicalPlanError(f"segment {index} has no incoming transition")
        if transition > min(previous.visible, segment.visible) + 1e-6:
            raise MusicalPlanError(
                f"segment {index}: transition {transition:.3f}s is longer than the "
                f"{min(previous.visible, segment.visible):.3f}s the two scenes share"
            )
    if abs(segments[-1].end - total) > 1e-6:
        raise MusicalPlanError(
            f"the last segment ends at {segments[-1].end:.3f}s but the timeline is "
            f"{total:.3f}s long"
        )


def _require_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise MusicalPlanError(f"{name} must be a positive finite number, got {value!r}")
