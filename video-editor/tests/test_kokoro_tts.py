"""Tests for the Kokoro local TTS provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY, get_provider
from video_editor_ffmpeg.probe import MediaInfo


def _request(
    prompt: str = "hello world",
    voice_id: str = "kokoro:af_bella",
    language: str = "en",
) -> AssetRequest:
    return AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt=prompt,
        provider="kokoro_tts",
        params={"voice_id": voice_id, "language": language},
    )


def test_kokoro_tts_is_in_provider_registry() -> None:
    from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider

    assert "kokoro_tts" in PROVIDER_REGISTRY
    assert get_provider("kokoro_tts") is KokoroTTSProvider


@pytest.mark.asyncio
async def test_kokoro_generate_produces_probeable_audio(tmp_path: Path) -> None:
    """Mock the kokoro/soundfile/torch stack and verify the provider returns a
    valid AssetResult with the expected metadata and duration."""
    from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider

    output_path = tmp_path / "out.wav"

    # Build a minimal fake kokoro module backed by real numpy.
    fake_np = MagicMock()
    fake_np.concatenate = MagicMock(side_effect=lambda chunks: np.concatenate(chunks))
    fake_np.asarray = MagicMock(side_effect=lambda x: np.asarray(x))

    fake_kokoro = MagicMock()
    fake_kokoro.np = fake_np

    fake_audio = MagicMock()
    fake_audio.detach.return_value.cpu.return_value.numpy.return_value = np.array([0.5])
    fake_generator = iter([("hello", "həˈloʊ", fake_audio)])

    fake_pipeline_instance = MagicMock(return_value=fake_generator)
    fake_pipeline_instance.sr = 24000

    def fake_pipeline_constructor(lang_code: str):  # noqa: ARG001
        return fake_pipeline_instance

    fake_kokoro.KPipeline = fake_pipeline_constructor

    fake_sf = MagicMock()

    fake_torch = MagicMock()
    fake_torch.Tensor = type(fake_audio)

    with (
        patch.dict(
            "sys.modules",
            {
                "kokoro": fake_kokoro,
                "soundfile": fake_sf,
                "torch": fake_torch,
            },
        ),
        patch(
            "video_editor_asset_ingestion.providers.tts.kokoro_tts.probe_media",
            return_value=MediaInfo(output_path, {"format": {"duration": "1.5"}}),
        ),
    ):
        provider = KokoroTTSProvider()
        result = await provider.generate(_request(), output_path)

    assert isinstance(result, AssetResult)
    assert result.asset_type == AssetType.TTS
    assert result.duration_ms == 1500
    assert result.metadata["provider"] == "kokoro_tts"
    assert result.metadata["voice_id"] == "af_bella"
    assert result.metadata["lang_code"] == "a"
    fake_sf.write.assert_called_once()


@pytest.mark.asyncio
async def test_kokoro_generate_requires_package(tmp_path: Path) -> None:
    """When kokoro is not installed, generate raises a helpful RuntimeError."""
    from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider

    output_path = tmp_path / "out.wav"
    provider = KokoroTTSProvider()

    with (
        patch.dict("sys.modules", {"kokoro": None}),
        pytest.raises(RuntimeError, match="Kokoro TTS requires"),
    ):
        await provider.generate(_request(), output_path)


@pytest.mark.asyncio
async def test_kokoro_generate_handles_no_audio_chunks(tmp_path: Path) -> None:
    """When kokoro emits no chunks, generate raises RuntimeError."""
    from video_editor_asset_ingestion.providers.tts.kokoro_tts import KokoroTTSProvider

    output_path = tmp_path / "out.wav"

    fake_kokoro = MagicMock()
    fake_pipeline_instance = MagicMock()
    fake_pipeline_instance.sr = 24000
    fake_pipeline_instance.__call__ = lambda text, voice, speed, split_pattern: iter([])
    fake_kokoro.KPipeline = lambda lang_code: fake_pipeline_instance
    fake_kokoro.np = MagicMock()

    fake_sf = MagicMock()
    fake_torch = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "kokoro": fake_kokoro,
            "soundfile": fake_sf,
            "torch": fake_torch,
        },
    ):
        provider = KokoroTTSProvider()
        with pytest.raises(RuntimeError, match="no audio chunks"):
            await provider.generate(_request(), output_path)


def test_kokoro_voice_prefix_stripped() -> None:
    from video_editor_asset_ingestion.providers.tts import kokoro_tts

    assert kokoro_tts._parse_voice_id("kokoro:af_bella") == "af_bella"
    assert kokoro_tts._parse_voice_id("af_bella") == "af_bella"


def test_kokoro_lang_code_mapping() -> None:
    from video_editor_asset_ingestion.providers.tts import kokoro_tts

    assert kokoro_tts._lang_code_for_request("en") == "a"
    assert kokoro_tts._lang_code_for_request("en-GB") == "b"
    assert kokoro_tts._lang_code_for_request("ja") == "j"
    assert kokoro_tts._lang_code_for_request("unknown") == "a"
    assert kokoro_tts._lang_code_for_request(None) == "a"
