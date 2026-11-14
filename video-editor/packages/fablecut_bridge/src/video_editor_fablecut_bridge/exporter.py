"""Export a ``ScenePlan`` to a FableCut project folder."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import structlog
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan

from video_editor_fablecut_bridge.lyrics import load_lyrics_for_plan
from video_editor_fablecut_bridge.models import (
    FableCutClip,
    FableCutKeyframe,
    FableCutMedia,
    FableCutProject,
    FableCutProps,
)

logger = structlog.get_logger(__name__)


def _new_id(prefix: str) -> str:
    """Return a deterministic-ish but collision-resistant id."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _resolve_visual_asset(scene: Scene) -> AssetResult | None:
    """Pick the first usable image or video asset from a scene."""
    for asset in scene.assets:
        if asset.asset_type in {AssetType.IMAGE, AssetType.VIDEO}:
            return asset
    return None


def _resolve_audio_asset(scene: Scene) -> AssetResult | None:
    """Pick the first usable TTS/audio asset from a scene."""
    for asset in scene.assets:
        if asset.asset_type in {AssetType.TTS, AssetType.MUSIC, AssetType.SFX}:
            return asset
    return None


def _media_kind(asset_type: AssetType) -> str:
    """Map engine asset types to FableCut media kinds."""
    mapping: dict[AssetType, str] = {
        AssetType.IMAGE: "image",
        AssetType.VIDEO: "video",
        AssetType.TTS: "audio",
        AssetType.MUSIC: "audio",
        AssetType.SFX: "audio",
    }
    return mapping.get(asset_type, "video")


def _copy_asset_to_project(
    src_path: Path,
    project_root: Path,
    asset_id: str,
) -> Path:
    """Copy a media file into the project's ``media/`` folder.

    FableCut expects media under ``./media/`` relative to ``project.json``.
    """
    media_dir = project_root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    dst = media_dir / f"{asset_id}_{src_path.name}"
    shutil.copy2(src_path, dst)
    return dst


def _export_scene_visual(
    scene: Scene,
    start_time: float,
    project_root: Path,
    media_registry: dict[str, FableCutMedia],
) -> FableCutClip | None:
    """Convert one scene's visual asset into a FableCut clip on V1."""
    visual_asset = _resolve_visual_asset(scene)
    if visual_asset is None:
        logger.warning(
            "fablecut_no_visual_asset",
            scene_id=scene.id,
            scene_type=scene.type,
        )
        return None

    src_path = visual_asset.resolved_path()
    if not src_path.is_file():
        logger.warning(
            "fablecut_missing_source_file",
            scene_id=scene.id,
            path=str(src_path),
        )
        return None

    media_id = _new_id("m")
    kind = _media_kind(visual_asset.asset_type)
    dst = _copy_asset_to_project(src_path, project_root, media_id)
    rel_src = f"/media/{dst.name}"

    media_registry[media_id] = FableCutMedia(
        id=media_id,
        name=dst.name,
        kind=kind,
        src=rel_src,
    )

    # For still images, add a slow Ken Burns push so the clip isn't static.
    # For videos, mute the original audio so only the supplied music is heard.
    keyframes: dict[str, list[FableCutKeyframe]] = {}
    if kind == "image":
        keyframes = {
            "scale": [
                FableCutKeyframe(t=0, v=1.0, ease="linear"),
                FableCutKeyframe(t=scene.duration_seconds, v=1.12, ease="linear"),
            ],
            "x": [
                FableCutKeyframe(t=0, v=0.0, ease="linear"),
                FableCutKeyframe(t=scene.duration_seconds, v=-40.0, ease="linear"),
            ],
        }
        props = FableCutProps(fit="cover")
    else:
        props = FableCutProps(volume=0.0)

    clip_id = _new_id("c")
    return FableCutClip(
        id=clip_id,
        mediaId=media_id,
        kind=kind,
        track="V1",
        start=start_time,
        duration=scene.duration_seconds,
        name=f"{scene.id}: {scene.narration[:40]}",
        props=props,
        keyframes=keyframes,
    )


def _export_scene_caption(
    scene: Scene,
    start_time: float,
    text: str,
) -> FableCutClip | None:
    """Create a lower-bottom pop-style caption text clip on V2."""
    if not text.strip():
        return None

    clip_id = _new_id("c")
    props = FableCutProps(
        text=text,
        fontSize=72,
        color="#ffffff",
        font="Noto Sans Devanagari",
        align="center",
        textAnim="word-pop",
        wordRate=0.15,
        strokeWidth=4,
        strokeColor="#000000",
        bgColor="#000000",
        bgOpacity=0.5,
        y=380,  # lower-bottom for 1080p canvas (center offset from H/2)
    )
    return FableCutClip(
        id=clip_id,
        mediaId=None,
        kind="text",
        track="V2",
        start=start_time,
        duration=scene.duration_seconds,
        name=f"caption:{scene.id}",
        props=props,
    )


def _export_scene_audio(
    scene: Scene,
    start_time: float,
    project_root: Path,
    media_registry: dict[str, FableCutMedia],
) -> FableCutClip | None:
    """Convert one scene's narration/TTS asset into a FableCut audio clip on A1."""
    audio_asset = _resolve_audio_asset(scene)
    if audio_asset is None:
        return None

    src_path = audio_asset.resolved_path()
    if not src_path.is_file():
        logger.warning(
            "fablecut_missing_audio_file",
            scene_id=scene.id,
            path=str(src_path),
        )
        return None

    media_id = _new_id("m")
    kind = _media_kind(audio_asset.asset_type)
    dst = _copy_asset_to_project(src_path, project_root, media_id)
    rel_src = f"/media/{dst.name}"

    media_registry[media_id] = FableCutMedia(
        id=media_id,
        name=dst.name,
        kind=kind,
        src=rel_src,
    )

    clip_id = _new_id("c")
    return FableCutClip(
        id=clip_id,
        mediaId=media_id,
        kind="audio",
        track="A1",
        start=start_time,
        duration=scene.duration_seconds,
        name=f"audio:{scene.id}",
        props=FableCutProps(volume=1.0),
    )


def build_fablecut_project(
    scene_plan: ScenePlan,
    project_root: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    music_path: Path | None = None,
    lyrics_path: Path | None = None,
) -> FableCutProject:
    """Build a :class:`FableCutProject` for ``scene_plan``.

    Copies referenced media into ``project_root / "media"`` as a side effect;
    the caller persists ``project.json`` / ``project.otio`` from the result.
    """
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "media").mkdir(exist_ok=True)

    durations = [s.duration_seconds for s in scene_plan.scenes]
    scene_lyrics = (
        load_lyrics_for_plan(lyrics_path, durations)
        if lyrics_path is not None and lyrics_path.is_file()
        else [""] * len(scene_plan.scenes)
    )

    media_registry: dict[str, FableCutMedia] = {}
    clips: list[FableCutClip] = []

    cursor = 0.0
    for i, scene in enumerate(scene_plan.scenes):
        visual_clip = _export_scene_visual(scene, cursor, project_root, media_registry)
        if visual_clip is not None:
            clips.append(visual_clip)

        caption_clip = _export_scene_caption(scene, cursor, scene_lyrics[i])
        if caption_clip is not None:
            clips.append(caption_clip)

        audio_clip = _export_scene_audio(scene, cursor, project_root, media_registry)
        if audio_clip is not None:
            clips.append(audio_clip)

        cursor += scene.duration_seconds

    total_duration = cursor

    if music_path is not None and music_path.is_file():
        music_id = _new_id("m")
        music_dst = _copy_asset_to_project(music_path, project_root, music_id)
        media_registry[music_id] = FableCutMedia(
            id=music_id,
            name=music_dst.name,
            kind="audio",
            src=f"/media/{music_dst.name}",
        )
        clips.append(
            FableCutClip(
                id=_new_id("c"),
                mediaId=music_id,
                kind="audio",
                track="A3",
                start=0.0,
                duration=total_duration,
                name="music",
                props=FableCutProps(volume=0.3),
            )
        )

    return FableCutProject(
        name=scene_plan.job_id,
        width=width,
        height=height,
        fps=fps,
        revision=1,
        media=list(media_registry.values()),
        clips=clips,
    )


def export_to_fablecut(
    scene_plan: ScenePlan,
    project_root: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    music_path: Path | None = None,
    lyrics_path: Path | None = None,
) -> Path:
    """Write a FableCut project folder for ``scene_plan``.

    The returned path is the project root. The main project file is written as
    ``project_root / "project.json"``. Media assets are copied into
    ``project_root / "media"``.
    """
    project = build_fablecut_project(
        scene_plan,
        project_root,
        fps=fps,
        width=width,
        height=height,
        music_path=music_path,
        lyrics_path=lyrics_path,
    )
    total_duration = max((c.start + c.duration for c in project.clips), default=0.0)

    project_file = project_root / "project.json"
    project_file.write_text(
        project.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )

    # Also emit an OpenTimelineIO timeline alongside project.json so the edit can
    # round-trip through the wider editorial ecosystem (Resolve/Premiere/etc.).
    from video_editor_fablecut_bridge.otio import export_otio

    export_otio(project, project_root / "project.otio")

    bridge_meta = {
        "engine_version": "2.0",
        "source_scene_plan": "<work_dir>/plan/scene_plan.json",
        "note": "Edit this project in FableCut, then re-import via ve import-fablecut.",
    }
    (project_root / "engine_bridge.json").write_text(
        json.dumps(bridge_meta, indent=2), encoding="utf-8"
    )

    logger.info(
        "fablecut_export_complete",
        project_root=str(project_root),
        scenes=len(scene_plan.scenes),
        media=len(project.media),
        duration=total_duration,
    )
    return project_root
