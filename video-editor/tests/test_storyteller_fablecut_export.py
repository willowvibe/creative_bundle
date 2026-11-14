"""Tests for the storyteller FableCut export/import round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.models import AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan


def _make_scene(
    scene_id: str,
    speaker: str,
    language: str,
    voice_id: str,
    needs_motion: bool,
    image_path: Path,
    audio_path: Path,
) -> Scene:
    from video_editor_asset_ingestion.models import AssetResult

    return Scene(
        id=scene_id,
        type="story",
        duration_seconds=5.0,
        narration="hello",
        visual_description="a wide shot",
        asset_query="",
        props={
            "speaker": speaker,
            "language": language,
            "voice_id": voice_id,
            "needs_motion": needs_motion,
            "render_track": "video" if needs_motion else "image",
            "image_model": "gpt-image-2",
            "motion_model": "seedance-2-fast" if needs_motion else None,
            "sfx_prompts": [],
        },
        assets=[
            AssetResult(
                asset_id=f"{scene_id}_visual",
                asset_type=AssetType.VIDEO if needs_motion else AssetType.IMAGE,
                local_path=str(image_path),
                scene_id=scene_id,
                duration_ms=4000,
                metadata={},
            ),
            AssetResult(
                asset_id=f"{scene_id}_audio",
                asset_type=AssetType.TTS,
                local_path=str(audio_path),
                scene_id=scene_id,
                duration_ms=2000,
                metadata={"voice_id": voice_id, "language": language},
            ),
        ],
    )


@pytest.mark.asyncio
async def test_storyteller_to_fablecut_writes_project_json(tmp_path: Path) -> None:
    from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut

    img1 = tmp_path / "img1.png"
    img1.write_bytes(b"FAKE")
    aud1 = tmp_path / "aud1.mp3"
    aud1.write_bytes(b"FAKE")
    plan = ScenePlan(
        job_id="s1",
        script="",
        scenes=[_make_scene("scene_001", "narrator", "en", "elevenlabs:Brian", False, img1, aud1)],
    )
    project_dir = tmp_path / "fablecut"
    project = await storyteller_to_fablecut(plan, project_dir, aspect_ratio="16:9")
    assert (project_dir / "project.json").exists()
    assert "tracks" in project.model_dump()


@pytest.mark.asyncio
async def test_round_trip_preserves_speaker_and_language(tmp_path: Path) -> None:
    from video_editor_fablecut_bridge.storyteller_export import (
        fablecut_to_storyteller,
        storyteller_to_fablecut,
    )

    img = tmp_path / "img.png"
    img.write_bytes(b"FAKE")
    aud = tmp_path / "aud.mp3"
    aud.write_bytes(b"FAKE")
    original = ScenePlan(
        job_id="s1",
        script="",
        scenes=[_make_scene("scene_001", "king", "hi-IN", "sarvam:shubh", False, img, aud)],
    )
    project_dir = tmp_path / "fablecut"
    project = await storyteller_to_fablecut(original, project_dir, aspect_ratio="16:9")
    reimported = fablecut_to_storyteller(project, original)
    assert reimported.scenes[0].props["speaker"] == "king"
    assert reimported.scenes[0].props["language"] == "hi-IN"
    assert reimported.scenes[0].props["voice_id"] == "sarvam:shubh"


@pytest.mark.asyncio
async def test_clip_paths_point_at_the_correct_assets(tmp_path: Path) -> None:
    from video_editor_fablecut_bridge.storyteller_export import storyteller_to_fablecut

    img = tmp_path / "img1.png"
    img.write_bytes(b"FAKE")
    aud = tmp_path / "aud1.mp3"
    aud.write_bytes(b"FAKE")
    plan = ScenePlan(
        job_id="s1",
        script="",
        scenes=[_make_scene("scene_001", "narrator", "en", "elevenlabs:Brian", False, img, aud)],
    )
    project_dir = tmp_path / "fablecut"
    project = await storyteller_to_fablecut(plan, project_dir, aspect_ratio="16:9")
    visual_clip = project.tracks["V1"][0]
    audio_clip = project.tracks["A1"][0]
    assert visual_clip["path"].endswith("scene_001_img1.png"), visual_clip["path"]
    assert audio_clip["path"].endswith("scene_001_aud1.mp3"), audio_clip["path"]
