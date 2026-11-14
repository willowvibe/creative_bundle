"""Audio generative providers (music + sound effects).

Wired up in Session T to surface ElevenLabs' ``/v1/music/generate``
and ``/v1/sound-generation`` endpoints as first-class ``AssetType``
providers, alongside stub providers for offline tests.
"""

from __future__ import annotations

from video_editor_asset_ingestion.providers.audio.ace_step import (
    ACEStepMusicProvider as ACEStepMusicProvider,
)
from video_editor_asset_ingestion.providers.audio.music_protocol import (
    MusicProvider as MusicProvider,
)
from video_editor_asset_ingestion.providers.audio.music_protocol import MusicResult as MusicResult
from video_editor_asset_ingestion.providers.audio.poyo_music import (
    PoyoMusicProvider as PoyoMusicProvider,
)
