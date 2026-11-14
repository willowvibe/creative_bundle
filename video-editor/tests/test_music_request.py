"""Tests for the new music + SFX request helpers in scene_assets."""

from __future__ import annotations

from video_editor_asset_ingestion.models import AssetType
from video_editor_asset_ingestion.scene_assets import music_request, sfx_requests_for_scene
from video_editor_core.models.scene_plan import Scene


def _make_scene(scene_id: str, visual: str = "", narration: str = "") -> Scene:
    return Scene(
        id=scene_id,
        type="narrative",
        visual_description=visual,
        narration=narration,
        duration_seconds=4.0,
    )


def test_music_request_basic_shape() -> None:
    """``music_request`` returns a job-level MUSIC request with the right fields."""
    req = music_request(
        job_id="job-1",
        prompt="calm lo-fi, 30s, no vocals",
        duration_seconds=30,
        provider="elevenlabs_music",
    )
    assert req.asset_id == "job_music"
    assert req.job_id == "job-1"
    assert req.scene_id == "job"
    assert req.asset_type == AssetType.MUSIC
    assert req.prompt == "calm lo-fi, 30s, no vocals"
    assert req.provider == "elevenlabs_music"
    assert req.output_ext == "mp3"
    assert req.params == {"duration_seconds": 30}


class TestSfxRequestsForScene:
    """Behavior of the per-scene SFX helper."""

    def test_empty_provider_returns_empty_list(self) -> None:
        """When the provider is empty, no request is emitted (opt-out)."""
        scene = _make_scene("s1", visual="A rolling green hill.")
        assert sfx_requests_for_scene(scene, "job-1", sfx_provider="") == []

    def test_empty_visual_description_returns_empty_list(self) -> None:
        """When the scene has no visual_description, no SFX request is emitted."""
        scene = _make_scene("s1", visual="")
        assert sfx_requests_for_scene(scene, "job-1", sfx_provider="elevenlabs_sfx") == []

    def test_provider_and_visual_yields_one_request(self) -> None:
        """When both provider and visual are set, a single SFX request is emitted."""
        scene = _make_scene("s1", visual="A rolling green hill.")
        requests = sfx_requests_for_scene(
            scene, "job-1", sfx_provider="elevenlabs_sfx", duration_seconds=5.0
        )
        assert len(requests) == 1
        req = requests[0]
        assert req.asset_type == AssetType.SFX
        assert req.scene_id == "s1"
        assert req.provider == "elevenlabs_sfx"
        assert req.prompt == "A rolling green hill."
        assert req.output_ext == "mp3"
        assert req.params == {"duration_seconds": 5.0}
