"""Tests for the MusicProvider protocol + MusicResult data class."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_music_result_carries_required_fields() -> None:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicResult

    r = MusicResult(
        provider="poyo_music",
        model="minimax-music-2.6",
        path=Path("/tmp/x.mp3"),
        duration_seconds=30.0,
    )
    assert r.provider == "poyo_music"
    assert r.duration_seconds == 30.0


def test_concrete_provider_must_implement_generate_music() -> None:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider

    class Bad(MusicProvider):
        provider_name = "bad"

    with pytest.raises(TypeError):
        Bad()  # type: ignore[abstract]
