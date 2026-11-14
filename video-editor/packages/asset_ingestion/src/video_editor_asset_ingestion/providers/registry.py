"""Registry mapping provider names to provider classes."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from video_editor_asset_ingestion.providers.alignment.elevenlabs_forced_alignment import (
    ElevenLabsForcedAlignmentProvider,
)
from video_editor_asset_ingestion.providers.alignment.stub import StubForcedAlignmentProvider
from video_editor_asset_ingestion.providers.audio.earshot_sfx import EarshotSFXProvider
from video_editor_asset_ingestion.providers.audio.elevenlabs_isolation import (
    ElevenLabsIsolationProvider,
)
from video_editor_asset_ingestion.providers.audio.elevenlabs_music import ElevenLabsMusicProvider
from video_editor_asset_ingestion.providers.audio.elevenlabs_sfx import ElevenLabsSFXProvider
from video_editor_asset_ingestion.providers.audio.elevenlabs_speech_to_speech import (
    ElevenLabsSpeechToSpeechProvider,
)
from video_editor_asset_ingestion.providers.audio.freesound_sfx import FreesoundSFXProvider
from video_editor_asset_ingestion.providers.audio.openverse_sfx import OpenverseSFXProvider
from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider
from video_editor_asset_ingestion.providers.audio.stub import (
    StubIsolationProvider,
    StubMusicProvider,
    StubSFXProvider,
    StubSpeechToSpeechProvider,
)
from video_editor_asset_ingestion.providers.tts.elevenlabs_clone import (
    ElevenLabsVoiceCloneProvider,
)
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider
from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider
from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider
from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider
from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider
from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter
from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageProvider
from video_editor_asset_ingestion.providers.visual.coverr import CoverrProvider
from video_editor_asset_ingestion.providers.visual.data_viz import MatplotlibDataVizProvider
from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider
from video_editor_asset_ingestion.providers.visual.flow_image import FlowImageProvider
from video_editor_asset_ingestion.providers.visual.hyperframes_video import (
    HyperFramesVideoProvider,
    StubHyperFramesVideoProvider,
)
from video_editor_asset_ingestion.providers.visual.local_media import (
    LocalMediaProvider,
    StubLocalMediaProvider,
)
from video_editor_asset_ingestion.providers.visual.mixkit import MixkitProvider
from video_editor_asset_ingestion.providers.visual.openrouter import OpenRouterProvider
from video_editor_asset_ingestion.providers.visual.pexels import PexelsProvider
from video_editor_asset_ingestion.providers.visual.pixabay import PixabayProvider
from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider
from video_editor_asset_ingestion.providers.visual.replicate import ReplicateImageProvider
from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

if TYPE_CHECKING:
    from video_editor_asset_ingestion.providers.audio.music_protocol import MusicProvider
    from video_editor_asset_ingestion.providers.base import GenerativeProvider

PROVIDER_REGISTRY: dict[str, type[GenerativeProvider | MusicProvider]] = {
    ElevenLabsTTSProvider.provider_name: ElevenLabsTTSProvider,
    ElevenLabsVoiceCloneProvider.provider_name: ElevenLabsVoiceCloneProvider,
    KokoroTTSProvider.provider_name: KokoroTTSProvider,
    KittenTTSProvider.provider_name: KittenTTSProvider,
    MossTTSProvider.provider_name: MossTTSProvider,
    OpenAITTSProvider.provider_name: OpenAITTSProvider,
    SarvamTTSProvider.provider_name: SarvamTTSProvider,
    StubTTSProvider.provider_name: StubTTSProvider,
    TTSRouter.provider_name: TTSRouter,
    AGYImageProvider.provider_name: AGYImageProvider,
    FlowImageProvider.provider_name: FlowImageProvider,
    FalAIProvider.provider_name: FalAIProvider,
    PoyoAIProvider.provider_name: PoyoAIProvider,
    OpenRouterProvider.provider_name: OpenRouterProvider,
    ReplicateImageProvider.provider_name: ReplicateImageProvider,
    StubImageProvider.provider_name: StubImageProvider,
    LocalMediaProvider.provider_name: LocalMediaProvider,
    StubLocalMediaProvider.provider_name: StubLocalMediaProvider,
    PexelsProvider.provider_name: PexelsProvider,
    PixabayProvider.provider_name: PixabayProvider,
    CoverrProvider.provider_name: CoverrProvider,
    MixkitProvider.provider_name: MixkitProvider,
    MatplotlibDataVizProvider.provider_name: MatplotlibDataVizProvider,
    ElevenLabsMusicProvider.provider_name: ElevenLabsMusicProvider,
    PoyoMusicProvider.provider_name: PoyoMusicProvider,
    ElevenLabsSFXProvider.provider_name: ElevenLabsSFXProvider,
    EarshotSFXProvider.provider_name: EarshotSFXProvider,
    FreesoundSFXProvider.provider_name: FreesoundSFXProvider,
    OpenverseSFXProvider.provider_name: OpenverseSFXProvider,
    ElevenLabsIsolationProvider.provider_name: ElevenLabsIsolationProvider,
    ElevenLabsSpeechToSpeechProvider.provider_name: ElevenLabsSpeechToSpeechProvider,
    StubMusicProvider.provider_name: StubMusicProvider,
    StubSFXProvider.provider_name: StubSFXProvider,
    StubIsolationProvider.provider_name: StubIsolationProvider,
    StubSpeechToSpeechProvider.provider_name: StubSpeechToSpeechProvider,
    ElevenLabsForcedAlignmentProvider.provider_name: ElevenLabsForcedAlignmentProvider,
    StubForcedAlignmentProvider.provider_name: StubForcedAlignmentProvider,
    HyperFramesVideoProvider.provider_name: HyperFramesVideoProvider,
    StubHyperFramesVideoProvider.provider_name: StubHyperFramesVideoProvider,
}


def get_provider(name: str) -> type[GenerativeProvider | MusicProvider]:
    """Return the provider class registered under ``name``."""
    if name not in PROVIDER_REGISTRY:
        raise KeyError(f"Unknown provider: {name}")
    return PROVIDER_REGISTRY[name]


def register_provider(name: str, cls: type[GenerativeProvider | MusicProvider]) -> None:
    """Register a custom provider class.

    Overwriting an existing provider name emits a warning so that
    accidental collisions are visible, while still allowing deliberate
    overrides (for example in tests).
    """
    if name in PROVIDER_REGISTRY:
        warnings.warn(
            f"Provider {name!r} is already registered as "
            f"{PROVIDER_REGISTRY[name].__name__}; overwriting with "
            f"{cls.__name__}.",
            stacklevel=2,
        )
    PROVIDER_REGISTRY[name] = cls
