"""Export a ``ScenePlan`` to a ComfyStudio project folder."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import structlog
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan

from video_editor_comfystudio_bridge.models import (
    ComfyStudioAsset,
    ComfyStudioClip,
    ComfyStudioClipTransform,
    ComfyStudioProject,
    ComfyStudioTextProperties,
    ComfyStudioTimeline,
    ComfyStudioTrack,
)

logger = structlog.get_logger(__name__)


def _new_id(prefix: str) -> str:
    """Return a deterministic-ish but collision-resistant clip/asset id."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _resolve_visual_asset(scene: Scene) -> AssetResult | None:
    """Pick the first usable image or video asset from a scene."""
    for asset in scene.assets:
        if asset.asset_type in {AssetType.IMAGE, AssetType.VIDEO}:
            return asset
    return None


def _pick_asset_type(asset_type: AssetType) -> str:
    """Map engine asset types to ComfyStudio asset types."""
    mapping: dict[AssetType, str] = {
        AssetType.IMAGE: "image",
        AssetType.VIDEO: "video",
        AssetType.TTS: "audio",
        AssetType.MUSIC: "audio",
        AssetType.SFX: "audio",
    }
    return mapping.get(asset_type, "video")


def _asset_folder(asset_type: str) -> str:
    """Map a singular asset type to the ComfyStudio assets sub-folder name."""
    return {"image": "images", "video": "video", "audio": "audio"}.get(asset_type, asset_type)


def _copy_asset_to_project(
    src_path: Path,
    project_root: Path,
    asset_id: str,
    asset_type: str,
) -> Path:
    """Copy a media file into the project's ``assets/`` folder.

    The layout mirrors the ComfyStudio convention:
    ``assets/{video,audio,images}/{asset_id}_{original_name}``.
    """
    folder = project_root / "assets" / _asset_folder(asset_type)
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"{asset_id}_{src_path.name}"
    shutil.copy2(src_path, dst)
    return dst


def _export_scene_to_clip(
    scene: Scene,
    start_time: float,
    project_root: Path,
    exported_assets: dict[str, ComfyStudioAsset],
) -> tuple[ComfyStudioClip | None, list[ComfyStudioClip]]:
    """Convert one scene into a primary visual clip plus caption clips.

    Returns ``(visual_clip, caption_clips)``. If the scene has no visual asset,
    the visual clip is ``None`` and only caption clips are returned.
    """
    visual_asset = _resolve_visual_asset(scene)
    caption_clips: list[ComfyStudioClip] = []

    if visual_asset is None:
        logger.warning(
            "comfystudio_no_visual_asset",
            scene_id=scene.id,
            scene_type=scene.type,
        )
        return None, caption_clips

    asset_id = _new_id("asset")
    src_path = visual_asset.resolved_path()
    if not src_path.is_file():
        logger.warning(
            "comfystudio_missing_source_file",
            scene_id=scene.id,
            path=str(src_path),
        )
        return None, caption_clips

    asset_type = _pick_asset_type(visual_asset.asset_type)
    dst = _copy_asset_to_project(src_path, project_root, asset_id, asset_type)
    rel_url = f"assets/{_asset_folder(asset_type)}/{dst.name}"

    exported_assets[asset_id] = ComfyStudioAsset(
        id=asset_id,
        type=asset_type,
        name=dst.name,
        url=rel_url,
        source="IMP",
    )

    clip_id = _new_id("clip")
    visual_clip = ComfyStudioClip(
        id=clip_id,
        trackId="track_video_main",
        type=asset_type,
        assetId=asset_id,
        name=f"{scene.id}: {scene.narration[:40]}",
        startTime=start_time,
        duration=scene.duration_seconds,
        sourceDuration=scene.duration_seconds,
    )

    # If the scene has a narration string, add a caption text clip on a text track.
    if scene.narration.strip():
        caption_clip = ComfyStudioClip(
            id=_new_id("clip"),
            trackId="track_captions",
            type="text",
            name=f"caption:{scene.id}",
            startTime=start_time,
            duration=scene.duration_seconds,
            textProperties=ComfyStudioTextProperties(
                text=scene.narration.strip(),
                fontSize=40,
                alignment="center",
                color="#FFFFFF",
                backgroundColor="#000000",
                backgroundOpacity=0.55,
            ),
            transform=ComfyStudioClipTransform(
                positionX=0.0,
                positionY=80.0,  # lower-bottom-ish percentage offset handled by ComfyStudio
            ),
        )
        caption_clips.append(caption_clip)

    return visual_clip, caption_clips


def export_to_comfystudio(
    scene_plan: ScenePlan,
    project_root: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    music_path: Path | None = None,
) -> Path:
    """Write a ComfyStudio project folder for ``scene_plan``.

    The returned path is the project root. The main project file is written as
    ``project_root / "project.comfystudio"``. Media assets are copied into
    ``project_root / "assets"``.
    """
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "cache").mkdir(exist_ok=True)
    (project_root / "renders").mkdir(exist_ok=True)
    (project_root / "thumbnails").mkdir(exist_ok=True)
    (project_root / "autosave").mkdir(exist_ok=True)

    resolution = {"width": width, "height": height}
    exported_assets: dict[str, ComfyStudioAsset] = {}

    video_track = ComfyStudioTrack(
        id="track_video_main",
        name="Video",
        type="video",
    )
    caption_track = ComfyStudioTrack(
        id="track_captions",
        name="Captions",
        type="video",
    )
    audio_track = ComfyStudioTrack(
        id="track_audio_main",
        name="Audio",
        type="audio",
    )

    cursor = 0.0
    for scene in scene_plan.scenes:
        visual_clip, caption_clips = _export_scene_to_clip(
            scene, cursor, project_root, exported_assets
        )
        if visual_clip is not None:
            video_track.clips.append(visual_clip)
        caption_track.clips.extend(caption_clips)
        cursor += scene.duration_seconds

    total_duration = cursor

    if music_path is not None and music_path.is_file():
        music_asset_id = _new_id("asset")
        music_dst = _copy_asset_to_project(music_path, project_root, music_asset_id, "audio")
        rel_url = f"assets/audio/{music_dst.name}"
        exported_assets[music_asset_id] = ComfyStudioAsset(
            id=music_asset_id,
            type="audio",
            name=music_dst.name,
            url=rel_url,
            source="IMP",
        )
        audio_track.clips.append(
            ComfyStudioClip(
                id=_new_id("clip"),
                trackId="track_audio_main",
                type="audio",
                assetId=music_asset_id,
                name="music",
                startTime=0.0,
                duration=total_duration,
                sourceDuration=total_duration,
            )
        )

    # Drop empty tracks so ComfyStudio's UI stays tidy.
    tracks: list[ComfyStudioTrack] = []
    if video_track.clips:
        tracks.append(video_track)
    if caption_track.clips:
        tracks.append(caption_track)
    if audio_track.clips:
        tracks.append(audio_track)

    timeline = ComfyStudioTimeline(
        id=_new_id("timeline"),
        name="Main",
        resolution=resolution,
        fps=fps,
        duration=total_duration,
        tracks=tracks,
    )

    project = ComfyStudioProject(
        name=scene_plan.job_id,
        resolution=resolution,
        fps=fps,
        assets=list(exported_assets.values()),
        timelines=[timeline],
    )

    project_file = project_root / "project.comfystudio"
    project_file.write_text(
        project.model_dump_json(indent=2, by_alias=True),
        encoding="utf-8",
    )

    # Also write a small human-readable manifest explaining the bridge.
    bridge_meta = {
        "engine_version": "2.0",
        "source_scene_plan": "<work_dir>/plan/scene_plan.json",
        "note": "Edit this project in ComfyStudio, then re-import via ve import-comfystudio.",
    }
    (project_root / "engine_bridge.json").write_text(
        json.dumps(bridge_meta, indent=2), encoding="utf-8"
    )

    logger.info(
        "comfystudio_export_complete",
        project_root=str(project_root),
        scenes=len(scene_plan.scenes),
        assets=len(exported_assets),
        duration=total_duration,
    )
    return project_root
