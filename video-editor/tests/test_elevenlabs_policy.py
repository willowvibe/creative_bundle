"""ElevenLabs is used for voice-over only (user rule, 2026-10-30)."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.audio.elevenlabs_music import ElevenLabsMusicProvider
from video_editor_asset_ingestion.providers.audio.elevenlabs_sfx import ElevenLabsSFXProvider
from video_editor_asset_ingestion.providers.elevenlabs_policy import (
    ElevenLabsVoiceOnlyError,
    ensure_voice_only,
)

NON_VOICE = [
    "elevenlabs_music",
    "elevenlabs_sfx",
    "elevenlabs_forced_alignment",
    "elevenlabs_isolation",
    "elevenlabs_speech_to_speech",
]


@pytest.mark.parametrize("name", NON_VOICE)
def test_non_voice_providers_are_refused(name: str) -> None:
    with pytest.raises(ElevenLabsVoiceOnlyError, match="voice-over only"):
        ensure_voice_only(name)


@pytest.mark.parametrize(
    "name", ["elevenlabs_tts", "elevenlabs_clone", "poyo_music", "freesound_sfx"]
)
def test_voice_and_other_providers_pass(name: str) -> None:
    ensure_voice_only(name)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cls", "asset_type"), [(ElevenLabsMusicProvider, "music"), (ElevenLabsSFXProvider, "sfx")]
)
async def test_a_direct_construction_cannot_bypass_the_rule(
    cls: type, asset_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = cls(ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    request = AssetRequest(
        job_id="j", asset_id="a", scene_id="", asset_type=asset_type, prompt="rain"
    )
    with pytest.raises(ElevenLabsVoiceOnlyError):
        await provider.generate(request, tmp_path / "out.mp3")


def test_the_manager_refuses_an_explicit_elevenlabs_music_request(tmp_path: Path) -> None:
    from video_editor_state.job import JobContext

    ctx = JobContext(job_id="j", work_root=tmp_path, output_root=tmp_path)
    manager = AssetIngestionManager(ctx)
    request = AssetRequest(
        job_id="j",
        asset_id="a",
        scene_id="",
        asset_type=AssetType.MUSIC,
        prompt="calm",
        provider="elevenlabs_music",
    )
    with pytest.raises(ElevenLabsVoiceOnlyError):
        manager._resolve_provider(request)
