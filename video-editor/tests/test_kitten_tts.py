"""Tests for KittenTTS provider."""

from __future__ import annotations

import sys
import types
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.tts import kitten_tts
from video_editor_asset_ingestion.providers.tts.kitten_tts import KittenTTSProvider
from video_editor_ffmpeg.probe import MediaInfo


class TestKittenTTSProvider:
    def test_provider_name(self):
        provider = KittenTTSProvider()
        assert provider.provider_name == "kitten_tts"

    def test_default_voice(self):
        provider = KittenTTSProvider()
        assert provider._voice == "expr-voice-2-f"

    def test_custom_voice_from_config(self):
        config = ProviderConfig(provider_options={"voice": "expr-voice-4-m", "speed": 0.9})
        provider = KittenTTSProvider(config=config)
        assert provider._voice == "expr-voice-4-m"
        assert provider._speed == 0.9

    def test_legacy_voice_alias_is_mapped(self):
        assert kitten_tts._resolve_voice("en_us_female_1") == "expr-voice-2-f"
        assert kitten_tts._resolve_voice("en_gb_male_1") == "expr-voice-4-m"
        assert kitten_tts._resolve_voice("expr-voice-5-f") == "expr-voice-5-f"

    def test_unknown_voice_fails_with_the_supported_list(self):
        with pytest.raises(RuntimeError, match="unknown KittenTTS voice"):
            kitten_tts._resolve_voice("ja_female_1")


def _request(
    voice_id: str = "expr-voice-2-f",
    prompt: str = "The strait carries a third of the world's trade.",
) -> AssetRequest:
    return AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt=prompt,
        provider="kitten_tts",
        params={"voice_id": voice_id, "language": "en"},
    )


def _fake_module(*, with_new_api: bool, audio: np.ndarray | None = None) -> types.ModuleType:
    module = types.ModuleType("kittentts")
    if with_new_api:

        class KittenTTS:
            def generate(self, text: str, voice: str, speed: float) -> np.ndarray:  # noqa: ARG002
                return audio if audio is not None else np.zeros(2400, dtype=np.float32)

        module.KittenTTS = KittenTTS  # type: ignore[attr-defined]
    else:

        class TTS:
            def __init__(self, voice: str) -> None:
                self.voice = voice

            def synthesize(self, text: str, speed: float) -> bytes:  # noqa: ARG002
                return b"RIFF-legacy-bytes"

        module.TTS = TTS  # type: ignore[attr-defined]
    return module


@pytest.mark.asyncio
async def test_generate_writes_a_wav_from_the_current_package_api(tmp_path: Path) -> None:
    """kittentts 0.1 returns floats; the provider must encode a WAV itself."""
    output_path = tmp_path / "out.wav"
    tones = np.linspace(-0.5, 0.5, 4800, dtype=np.float32)  # 0.2 s at 24 kHz

    with (
        patch.dict(sys.modules, {"kittentts": _fake_module(with_new_api=True, audio=tones)}),
        patch.object(
            kitten_tts,
            "probe_media",
            return_value=MediaInfo(output_path, {"format": {"duration": "0.2"}}),
        ),
    ):
        result = await KittenTTSProvider().generate(_request(), output_path)

    assert isinstance(result, AssetResult)
    assert result.asset_type == AssetType.TTS
    assert result.duration_ms == 200
    assert result.metadata["provider"] == "kitten_tts"
    assert result.metadata["voice"] == "expr-voice-2-f"

    with wave.open(str(output_path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 24000
        assert handle.getnframes() == 4800


@pytest.mark.asyncio
async def test_generate_still_supports_the_legacy_bytes_api(tmp_path: Path) -> None:
    output_path = tmp_path / "out.wav"
    with (
        patch.dict(sys.modules, {"kittentts": _fake_module(with_new_api=False)}),
        patch.object(kitten_tts, "probe_media", side_effect=RuntimeError("not audio")),
    ):
        result = await KittenTTSProvider().generate(_request(), output_path)

    assert output_path.read_bytes() == b"RIFF-legacy-bytes"
    assert result.duration_ms == 0


@pytest.mark.asyncio
async def test_generate_requires_the_package(tmp_path: Path) -> None:
    with (
        patch.dict(sys.modules, {"kittentts": None}),
        pytest.raises(RuntimeError, match="kittentts package not installed"),
    ):
        await KittenTTSProvider().generate(_request(), tmp_path / "out.wav")


@pytest.mark.asyncio
async def test_empty_audio_is_an_error_not_a_silent_asset(tmp_path: Path) -> None:
    with (
        patch.dict(
            sys.modules,
            {"kittentts": _fake_module(with_new_api=True, audio=np.zeros(0, dtype=np.float32))},
        ),
        pytest.raises(RuntimeError, match="no audio samples"),
    ):
        await KittenTTSProvider().generate(_request(), tmp_path / "out.wav")


def test_a_short_narration_is_one_chunk() -> None:
    assert kitten_tts._split_for_model("One short line.") == ["One short line."]


def test_a_long_narration_is_split_below_the_model_limit() -> None:
    text = " ".join(["The strait carries a third of the world's trade."] * 20)
    chunks = kitten_tts._split_for_model(text)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= kitten_tts.MAX_CHUNK_CHARS for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_a_single_over_long_sentence_splits_on_spaces() -> None:
    sentence = " ".join(["word"] * 200)
    chunks = kitten_tts._split_for_model(sentence)
    assert len(chunks) > 1
    assert all(len(chunk) <= kitten_tts.MAX_CHUNK_CHARS for chunk in chunks)
    assert " ".join(chunks).split() == sentence.split()


@pytest.mark.asyncio
async def test_long_text_is_synthesized_in_chunks_with_a_gap(tmp_path: Path) -> None:
    """The nano ONNX graph rejects long inputs; the provider must chunk them."""
    output_path = tmp_path / "out.wav"
    calls: list[str] = []

    class ChunkedKitten:
        def generate(self, text: str, voice: str, speed: float) -> np.ndarray:  # noqa: ARG002
            calls.append(text)
            return np.zeros(2400, dtype=np.float32)

    module = types.ModuleType("kittentts")
    module.KittenTTS = ChunkedKitten  # type: ignore[attr-defined]
    text = " ".join(["A sentence about the strait."] * 40)

    with (
        patch.dict(sys.modules, {"kittentts": module}),
        patch.object(kitten_tts, "probe_media", side_effect=RuntimeError("not audio")),
    ):
        await KittenTTSProvider().generate(_request(prompt=text), output_path)

    assert len(calls) > 1
    assert all(len(chunk) <= kitten_tts.MAX_CHUNK_CHARS for chunk in calls)
    with wave.open(str(output_path), "rb") as handle:
        frames = handle.getnframes()
    gap_frames = int(kitten_tts.DEFAULT_SAMPLE_RATE * kitten_tts.CHUNK_GAP_SECONDS)
    assert frames == 2400 * len(calls) + gap_frames * (len(calls) - 1)
