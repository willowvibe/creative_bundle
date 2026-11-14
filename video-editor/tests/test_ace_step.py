"""Tests for ACE-Step music provider."""

from unittest.mock import AsyncMock, patch

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.providers.audio.ace_step import ACEStepMusicProvider


def _mock_response(**attrs):
    """Create a mock httpx Response with the given attributes."""
    # raise_for_status is a method, so it needs to accept self
    defaults: dict = {"raise_for_status": lambda self: None}
    defaults.update(attrs)
    return type("Response", (), defaults)()


class TestACEStepMusicProvider:
    def test_provider_name(self):
        provider = ACEStepMusicProvider()
        assert provider.provider_name == "ace_step"

    def test_init_with_config(self):
        config = ProviderConfig(
            api_key_env="ACE_STEP_API_KEY",
            provider_options={"use_local": True},
        )
        provider = ACEStepMusicProvider(config=config)
        assert provider._use_local is True

    def test_init_defaults(self):
        provider = ACEStepMusicProvider()
        assert provider._use_local is False
        assert provider._base_url == "https://api.acemusic.ai"
        assert provider._poll_interval == 5.0
        assert provider._poll_timeout == 300.0

    def test_init_custom_poll_settings(self):
        config = ProviderConfig(
            provider_options={
                "poll_interval_seconds": 10.0,
                "poll_timeout_seconds": 600.0,
            },
        )
        provider = ACEStepMusicProvider(config=config)
        assert provider._poll_interval == 10.0
        assert provider._poll_timeout == 600.0

    @pytest.mark.asyncio
    async def test_generate_music_api_flow(self, tmp_path):
        """Test the cloud API generation flow with mocked HTTP."""
        output_path = tmp_path / "test_music.mp3"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()

            mock_client.post = AsyncMock(
                return_value=_mock_response(json=lambda self: {"id": "job-123"}),
            )
            mock_client.get = AsyncMock(
                side_effect=[
                    _mock_response(
                        json=lambda self: {
                            "status": "completed",
                            "output_url": "https://example.com/audio.mp3",
                        },
                    ),
                    _mock_response(content=b"fake-audio-data"),
                ],
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            provider = ACEStepMusicProvider()
            result = await provider.generate_music(
                "upbeat electronic",
                30.0,
                output_path,
            )

            assert result.provider == "ace_step"
            assert result.model == "ace-step-1.5"
            assert result.duration_seconds == 30.0
            assert result.path == output_path
            assert output_path.exists()
            assert output_path.read_bytes() == b"fake-audio-data"

    @pytest.mark.asyncio
    async def test_generate_music_api_failure(self, tmp_path):
        """Test that a failed job raises RuntimeError."""
        output_path = tmp_path / "test_music.mp3"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                return_value=_mock_response(json=lambda self: {"id": "job-456"}),
            )
            mock_client.get = AsyncMock(
                return_value=_mock_response(
                    json=lambda self: {
                        "status": "failed",
                        "error": "generation failed",
                    },
                ),
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            provider = ACEStepMusicProvider()
            with pytest.raises(RuntimeError, match="job-456.*failed"):
                await provider.generate_music(
                    "test prompt",
                    30.0,
                    output_path,
                )

    @pytest.mark.asyncio
    async def test_generate_music_no_job_id(self, tmp_path):
        """Test that missing job ID raises RuntimeError."""
        output_path = tmp_path / "test_music.mp3"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(
                return_value=_mock_response(
                    json=lambda self: {"error": "something went wrong"},
                ),
            )
            mock_client_class.return_value.__aenter__.return_value = mock_client

            provider = ACEStepMusicProvider()
            with pytest.raises(RuntimeError, match="no job ID"):
                await provider.generate_music(
                    "test prompt",
                    30.0,
                    output_path,
                )
