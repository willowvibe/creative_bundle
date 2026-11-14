"""Tests for MOSS-TTS provider."""

from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.tts.moss_tts import MossTTSProvider


class TestMossTTSProvider:
    def test_provider_name(self):
        provider = MossTTSProvider()
        assert provider.provider_name == "moss_tts"

    def test_default_voice(self):
        provider = MossTTSProvider()
        assert provider._voice == "default"

    def test_custom_voice_from_config(self):
        config = ProviderConfig(provider_options={"voice": "custom_voice", "speed": 1.2})
        provider = MossTTSProvider(config=config)
        assert provider._voice == "custom_voice"
        assert provider._speed == 1.2
