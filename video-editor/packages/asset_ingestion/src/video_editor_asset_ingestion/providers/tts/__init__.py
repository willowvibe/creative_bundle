"""Text-to-speech provider connectors."""

from __future__ import annotations

from video_editor_asset_ingestion.providers.tts.elevenlabs_clone import (
    ElevenLabsVoiceCloneProvider as ElevenLabsVoiceCloneProvider,
)
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider
from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider
from video_editor_asset_ingestion.providers.tts.sarvam_tts import (
    SarvamTTSProvider as SarvamTTSProvider,
)
from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider
from video_editor_asset_ingestion.providers.tts.tts_router import (
    TTSRouter as TTSRouter,
)

__all__ = [
    "ElevenLabsVoiceCloneProvider",
    "KittenTTSProvider",
    "MossTTSProvider",
    "OpenAITTSProvider",
    "SarvamTTSProvider",
    "StubTTSProvider",
    "TTSRouter",
]
