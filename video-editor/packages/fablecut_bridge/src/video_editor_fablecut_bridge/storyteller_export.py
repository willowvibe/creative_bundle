"""Storyteller FableCut project export/import.

Wraps the existing FableCut exporter to add per-speaker volume, per-language
audio tracks, SFX tracks, and a round-trip importer that merges FableCut
user edits back into the ScenePlan props.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from video_editor_asset_ingestion.models import AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan


class StorytellerProject(BaseModel):
    """FableCut project shape with storyteller-specific metadata."""

    name: str
    tracks: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


def _resolve_asset_path(scene: Scene, kind: str) -> Path | None:
    """Find the first asset matching `kind` ('image' | 'video' | 'audio') on the scene."""
    target = (
        AssetType.VIDEO
        if kind == "video"
        else AssetType.IMAGE
        if kind == "image"
        else AssetType.TTS
    )
    for a in scene.assets:
        if a.asset_type == target:
            return Path(a.local_path)
    return None


def _copy_assets(plan: ScenePlan, media_dir: Path) -> None:
    media_dir.mkdir(parents=True, exist_ok=True)
    for scene in plan.scenes:
        for asset in scene.assets:
            src = Path(asset.local_path)
            if not src.exists():
                continue
            dst = media_dir / f"{scene.id}_{src.name}"
            shutil.copy2(src, dst)


def _clip_for_scene(scene: Scene, media_dir: Path) -> dict[str, Any]:
    render_track = scene.props.get("render_track", "image")
    if render_track == "video":
        path = _resolve_asset_path(scene, "video")
    else:
        path = _resolve_asset_path(scene, "image")
    rel = ""
    if path is not None:
        dst = media_dir / f"{scene.id}_{path.name}"
        if dst.exists():
            rel = str(dst.relative_to(media_dir.parent))
    return {
        "id": scene.id,
        "type": "video" if render_track == "video" else "image",
        "path": rel,
        "duration_seconds": scene.duration_seconds,
        "meta": {
            "speaker": scene.props.get("speaker"),
            "language": scene.props.get("language"),
            "voice_id": scene.props.get("voice_id"),
            "needs_motion": scene.props.get("needs_motion"),
            "motion_model": scene.props.get("motion_model"),
            "image_prompt": scene.visual_description,
        },
    }


def _audio_clip_for_scene(scene: Scene, media_dir: Path) -> dict[str, Any] | None:
    path = _resolve_asset_path(scene, "audio")
    if path is None:
        return None
    dst = media_dir / f"{scene.id}_{path.name}"
    rel = str(dst.relative_to(media_dir.parent)) if dst.exists() else ""
    return {
        "id": f"{scene.id}_audio",
        "type": "audio",
        "path": rel,
        "duration_seconds": scene.duration_seconds,
        "meta": {
            "speaker": scene.props.get("speaker"),
            "language": scene.props.get("language"),
            "voice_id": scene.props.get("voice_id"),
        },
    }


def _caption_clip_for_scene(scene: Scene) -> dict[str, Any]:
    return {
        "id": f"{scene.id}_caption",
        "type": "caption",
        "text": scene.narration,
        "start": 0.0,
        "duration": scene.duration_seconds,
    }


def _title_clip_for_scene(scene: Scene) -> dict[str, Any]:
    return {
        "id": f"{scene.id}_title",
        "type": "title",
        "text": scene.props.get("speaker", "Scene"),
        "start": 0.0,
        "duration": scene.duration_seconds,
    }


async def storyteller_to_fablecut(
    scene_plan: ScenePlan,
    project_dir: Path,
    *,
    aspect_ratio: str = "16:9",
) -> StorytellerProject:
    """Build a StorytellerProject from a ScenePlan and write it to disk."""
    project_dir = Path(project_dir)
    media_dir = project_dir / "media"
    _copy_assets(scene_plan, media_dir)

    visual_clips = [_clip_for_scene(s, media_dir) for s in scene_plan.scenes]
    audio_clips = [
        c for s in scene_plan.scenes for c in [_audio_clip_for_scene(s, media_dir)] if c is not None
    ]
    title_clips = [_title_clip_for_scene(s) for s in scene_plan.scenes]
    caption_clips = [_caption_clip_for_scene(s) for s in scene_plan.scenes]

    # Determine per-language lanes: if the story uses multiple languages, put
    # each on its own A-track. Otherwise all audio on A1.
    # TODO(8a multi-language gap): ``A3`` is hardcoded ``[]`` so stories with
    # 3+ languages silently drop the third (and later) audio track(s). When
    # a third language is needed, extend this to emit A3..An lanes (or fold
    # extras into a single "other" lane with a meta note).
    languages = sorted({s.props.get("language", "en") for s in scene_plan.scenes})
    speakers = sorted({s.props.get("speaker", "narrator") for s in scene_plan.scenes})

    project = StorytellerProject(
        name=scene_plan.job_id or "storyteller",
        tracks={
            "V1": visual_clips,
            "V2": title_clips,
            "V3": caption_clips,
            "A1": audio_clips
            if len(languages) == 1
            else [c for c in audio_clips if c["meta"].get("language") == languages[0]],
            "A2": []
            if len(languages) == 1
            else [c for c in audio_clips if c["meta"].get("language") == languages[1]],
            "A3": [],
        },
        meta={
            "aspect_ratio": aspect_ratio,
            "audio_speakers": speakers,
            "audio_languages": languages,
        },
    )
    (project_dir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")
    return project


def fablecut_to_storyteller(
    project: StorytellerProject,
    scene_plan: ScenePlan,
) -> ScenePlan:
    """Merge FableCut user-edited clip metadata back into the ScenePlan.

    TODO(library-only): there is no ``ve storyteller-import`` CLI command nor
    a worker handler for the reverse direction; this function is library-only
    for now. Round-trip import must be invoked programmatically until a CLI
    surface is added (out of scope for the storyteller-templates wave).
    """
    visual_by_id = {c["id"]: c for c in project.tracks.get("V1", [])}
    out_scenes = []
    for scene in scene_plan.scenes:
        clip = visual_by_id.get(scene.id)
        if clip is None:
            out_scenes.append(scene)
            continue
        new_props = dict(scene.props)
        meta = clip.get("meta", {})
        if "needs_motion" in meta and meta["needs_motion"] != scene.props.get("needs_motion"):
            new_props["needs_motion"] = bool(meta["needs_motion"])
            new_props["render_track"] = "video" if meta["needs_motion"] else "image"
        if meta.get("image_prompt"):
            scene = scene.model_copy(
                update={"visual_description": meta["image_prompt"], "props": new_props}
            )
        else:
            scene = scene.model_copy(update={"props": new_props})
        out_scenes.append(scene)
    return scene_plan.model_copy(update={"scenes": out_scenes})
