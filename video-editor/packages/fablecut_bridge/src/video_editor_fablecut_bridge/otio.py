"""OpenTimelineIO (OTIO) interchange for FableCut projects.

Converts a :class:`FableCutProject` to/from an OTIO ``Timeline`` so timelines can
round-trip through the wider editorial ecosystem (Resolve, Premiere, etc.) in
addition to FableCut's native ``project.json``.

The mapping is lossless for the fields this bridge cares about:

* Timeline name + width/height/fps/background/revision live in
  ``timeline.metadata["fablecut"]``.
* Each FableCut track (``V1``/``A1`` …) becomes an OTIO ``Track`` (Video/Audio);
  absolute clip start is preserved both as a leading ``Gap`` (for standard OTIO
  consumers) and in ``clip.metadata["fablecut"]`` (for exact round-trips).
* Marker positions map to OTIO ``Marker``s on the timeline.

Requires the ``opentimelineio`` package.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import structlog

from video_editor_fablecut_bridge.models import (
    FableCutClip,
    FableCutMarker,
    FableCutMedia,
    FableCutProject,
    FableCutProps,
    FableCutTransition,
)

logger = structlog.get_logger(__name__)

_META_KEY = "fablecut"


def _track_kind(track_name: str) -> str:
    return "Video" if track_name.upper().startswith("V") else "Audio"


def _sorted_track_names(project: FableCutProject) -> list[str]:
    """Track names in first-appearance order (stable)."""
    seen: list[str] = []
    for clip in project.clips:
        if clip.track not in seen:
            seen.append(clip.track)
    return seen


def _clip_metadata(clip: FableCutClip) -> dict[str, object]:
    return {
        "id": clip.id,
        "kind": clip.kind,
        "mediaId": clip.mediaId,
        "track": clip.track,
        "start": clip.start,
        "in": clip.in_,
        "duration": clip.duration,
        "name": clip.name,
        "props": clip.props.model_dump(),
        "transitionIn": clip.transitionIn.model_dump() if clip.transitionIn else None,
        "transitionOut": clip.transitionOut.model_dump() if clip.transitionOut else None,
    }


def to_otio(
    project: FableCutProject, *, media_root: Path | str | None = None
) -> otio.schema.Timeline:
    """Convert a FableCut project to an OTIO ``Timeline``.

    When ``media_root`` is given, each clip's media reference is resolved to an
    absolute path (``media_root / src``) so the timeline opens in other editors;
    otherwise the stored relative ``src`` (e.g. ``/media/clip.png``) is used.
    """
    rate = float(project.fps or 30)

    def rt(seconds: float) -> otio.opentime.RationalTime:
        return otio.opentime.RationalTime(seconds * rate, rate)

    timeline = otio.schema.Timeline(name=project.name)
    timeline.global_start_time = otio.opentime.RationalTime(0, rate)
    timeline.metadata[_META_KEY] = {
        "name": project.name,
        "width": project.width,
        "height": project.height,
        "fps": project.fps,
        "background": project.background,
        "revision": project.revision,
        # OTIO 0.18 dropped Timeline.markers; keep an authoritative copy here and
        # also mirror onto the first track for interop consumers.
        "markers": [{"t": m.t, "label": m.label} for m in project.markers],
    }

    media_by_id = {m.id: m for m in project.media}
    for track_name in _sorted_track_names(project):
        track = otio.schema.Track(name=track_name, kind=_track_kind(track_name))
        cursor = 0.0
        for clip in sorted(
            (c for c in project.clips if c.track == track_name), key=lambda c: c.start
        ):
            if clip.start > cursor:
                track.append(
                    otio.schema.Gap(
                        source_range=otio.opentime.TimeRange(
                            otio.opentime.RationalTime(0, rate),
                            rt(clip.start - cursor),
                        )
                    )
                )
            media = media_by_id.get(clip.mediaId or "")
            if media is not None:
                target = media.src
                if media_root is not None and target:
                    target = str((Path(media_root) / target.lstrip("/")).resolve())
                reference: otio.schema.MediaReference = otio.schema.ExternalReference(
                    target_url=target
                )
            else:
                reference = otio.schema.MissingReference()
            oclip = otio.schema.Clip(
                name=clip.name or clip.id,
                media_reference=reference,
                source_range=otio.opentime.TimeRange(rt(clip.in_), rt(clip.duration)),
            )
            oclip.metadata[_META_KEY] = _clip_metadata(clip)
            track.append(oclip)
            cursor = clip.start + clip.duration
        timeline.tracks.append(track)

    for marker in project.markers:
        marker_otio = otio.schema.Marker(
            name=marker.label,
            marked_range=otio.opentime.TimeRange(rt(marker.t), otio.opentime.RationalTime(0, rate)),
        )
        if len(timeline.tracks) > 0:
            timeline.tracks[0].markers.append(marker_otio)
    return timeline


def _media_kind_from_target(target_url: str) -> str:
    suffix = Path(target_url).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "svg"}:
        return "image"
    if suffix in {"mp3", "wav", "m4a", "aac", "flac", "ogg"}:
        return "audio"
    return "video"


def from_otio(timeline: otio.schema.Timeline) -> FableCutProject:
    """Convert an OTIO ``Timeline`` back to a FableCut project."""
    meta = timeline.metadata.get(_META_KEY, {}) if timeline.metadata else {}
    fps = int(meta.get("fps") or timeline.global_start_time.rate or 30)

    project = FableCutProject(
        name=str(meta.get("name") or timeline.name or ""),
        width=int(meta.get("width", 1920)),
        height=int(meta.get("height", 1080)),
        fps=fps,
        background=str(meta.get("background", "#000000")),
        revision=int(meta.get("revision", 1)),
    )

    media_by_id: dict[str, FableCutMedia] = {}
    clips: list[FableCutClip] = []
    for track in timeline.tracks:
        track_name = track.name or "V1"
        for item in track:
            cm = item.metadata.get(_META_KEY, {}) if item.metadata else {}
            if isinstance(item, otio.schema.Gap) or not cm:
                continue
            media_id = cm.get("mediaId")
            if media_id and media_id not in media_by_id:
                target = getattr(item.media_reference, "target_url", "") or ""
                media_by_id[media_id] = FableCutMedia(
                    id=str(media_id),
                    name=Path(target).name,
                    kind=str(cm.get("kind") or _media_kind_from_target(target)),
                    src=target,
                )
            props = cm.get("props") or {}
            clip = FableCutClip(
                id=str(cm.get("id") or item.name),
                mediaId=media_id,
                kind=str(cm.get("kind") or "video"),
                track=str(cm.get("track") or track_name),
                start=float(cm.get("start", 0.0)),
                duration=float(cm.get("duration", 0.0)),
                name=str(cm.get("name", item.name)),
                props=FableCutProps(**props) if props else FableCutProps(),
                transitionIn=(
                    FableCutTransition(**cm["transitionIn"]) if cm.get("transitionIn") else None
                ),
                transitionOut=(
                    FableCutTransition(**cm["transitionOut"]) if cm.get("transitionOut") else None
                ),
            )
            clip.in_ = float(cm.get("in", 0.0))
            clips.append(clip)

    meta_markers = meta.get("markers")
    if isinstance(meta_markers, list) and meta_markers:
        for entry in meta_markers:
            project.markers.append(
                FableCutMarker(t=float(entry.get("t", 0.0)), label=str(entry.get("label", "")))
            )
    else:
        for track in timeline.tracks:
            for otio_marker in track.markers:
                project.markers.append(
                    FableCutMarker(
                        t=otio_marker.marked_range.start_time.to_seconds()
                        if otio_marker.marked_range
                        else 0.0,
                        label=otio_marker.name or "",
                    )
                )

    project.media = list(media_by_id.values())
    project.clips = clips
    return project


def export_otio(
    project: FableCutProject,
    path: Path | str,
    *,
    media_root: Path | str | None = None,
) -> Path:
    """Write ``project`` to ``path`` as an OTIO timeline file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(to_otio(project, media_root=media_root), str(path))
    logger.info("otio_export_complete", path=str(path), clips=len(project.clips))
    return path


def import_otio(path: Path | str) -> FableCutProject:
    """Read an OTIO timeline file into a FableCut project."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"OTIO file not found: {path}")
    timeline = otio.adapters.read_from_file(str(path))
    project = from_otio(timeline)
    logger.info("otio_import_complete", path=str(path), clips=len(project.clips))
    return project
