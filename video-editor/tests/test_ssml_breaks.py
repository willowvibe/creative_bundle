"""Tests for SSML break-tag handling in the TTS pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import ElevenLabsTTSProvider
from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider
from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider
from video_editor_asset_ingestion.providers.tts.ssml_breaks import (
    TextChunk,
    extract_breaks,
    has_break_tags,
    strip_unsupported_ssml,
    synthesize_with_breaks,
    wrap_for_elevenlabs,
)
from video_editor_ffmpeg.probe import MediaInfo


def _req(prompt: str, provider: str = "openai_tts") -> AssetRequest:
    return AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt=prompt,
        provider=provider,
        params={"voice": "alloy"},
    )


def test_extract_breaks_splits_text_and_records_pauses() -> None:
    chunks = extract_breaks(
        "Hello world. <break time='500ms'/> How are you? <break time=\"1s\"> Done."
    )
    assert chunks == [
        TextChunk("Hello world. ", 500),
        TextChunk(" How are you? ", 1000),
        TextChunk(" Done.", 0),
    ]


def test_extract_breaks_no_breaks_returns_single_chunk() -> None:
    assert extract_breaks("No breaks here.") == [TextChunk("No breaks here.", 0)]


def test_extract_breaks_strips_unsupported_tags() -> None:
    chunks = extract_breaks(
        "<speak>Hello.</speak> <break time='200ms'/> <emphasis>World</emphasis>"
    )
    assert chunks == [
        TextChunk("Hello. ", 200),
        TextChunk(" World", 0),
    ]


def test_has_break_tags() -> None:
    assert has_break_tags("Hi <break time='1s'/> there")
    assert not has_break_tags("No tags")


def test_wrap_for_elevenlabs_wraps_breaks() -> None:
    assert (
        wrap_for_elevenlabs("Hi <break time='1s'/> there")
        == "<speak>Hi <break time='1s'/> there</speak>"
    )
    assert wrap_for_elevenlabs("<speak>already</speak>") == "<speak>already</speak>"
    assert wrap_for_elevenlabs("plain text") == "plain text"


def test_strip_unsupported_ssml() -> None:
    assert strip_unsupported_ssml("<speak>Hello</speak>") == "Hello"
    assert strip_unsupported_ssml('<emphasis level="strong">Hi</emphasis>') == "Hi"


@pytest.mark.asyncio
async def test_synthesize_with_breaks_concatenates_chunks(tmp_path: Path) -> None:
    """Mock the upstream provider and ffmpeg concat to verify break padding."""
    provider = MagicMock(spec=OpenAITTSProvider)
    provider.provider_name = "openai_tts"

    async def fake_generate(request: AssetRequest, output_path: Path) -> AssetResult:
        # Each chunk gets a deterministic 1s audio file.
        output_path.write_bytes(b"FAKE")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            scene_id=request.scene_id,
            duration_ms=1000,
            metadata={},
        )

    provider.generate = AsyncMock(side_effect=fake_generate)

    request = _req("Part one. <break time='500ms'/> Part two.")
    output_path = tmp_path / "out.mp3"

    with (
        patch(
            "video_editor_asset_ingestion.providers.tts.ssml_breaks.run_ffmpeg"
        ) as mock_run_ffmpeg,
        patch(
            "video_editor_asset_ingestion.providers.tts.ssml_breaks.probe_media",
            return_value=MediaInfo(output_path, {"format": {"duration": "2.5"}}),
        ),
    ):
        result = await synthesize_with_breaks(provider, request, output_path)

    assert result.asset_type == AssetType.TTS
    assert result.duration_ms == 2500
    assert provider.generate.call_count == 2
    mock_run_ffmpeg.assert_called_once()
    # Verify the ffmpeg command received two audio inputs and one lavfi silence input.
    cmd = mock_run_ffmpeg.call_args[0][0]
    assert cmd.count("-i") == 3  # chunk1 + silence + chunk2


@pytest.mark.asyncio
async def test_synthesize_with_breaks_fast_path_single_chunk(tmp_path: Path) -> None:
    provider = MagicMock(spec=OpenAITTSProvider)
    provider.provider_name = "openai_tts"

    async def fake_generate(request: AssetRequest, output_path: Path) -> AssetResult:
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(output_path),
            scene_id=request.scene_id,
            duration_ms=1200,
            metadata={},
        )

    provider.generate = AsyncMock(side_effect=fake_generate)

    request = _req("No breaks here.")
    output_path = tmp_path / "out.mp3"
    result = await synthesize_with_breaks(provider, request, output_path)

    assert result.duration_ms == 1200
    assert provider.generate.call_count == 1


@pytest.mark.asyncio
async def test_elevenlabs_keeps_breaks_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flash models get the SSML wrapped inline; v3/v4 get "..." instead of ``<break>``."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    captured: dict[str, str] = {}

    async def fake_post(self, path, **kwargs):  # noqa: ARG001
        captured["text"] = kwargs.get("json", {}).get("text", "")

        class FakeResp:
            @property
            def content(self):
                return b"\xff\xfb" + b"0" * 64

            def raise_for_status(self):
                pass

            @property
            def headers(self):
                return {"content-type": "audio/mpeg"}

        return FakeResp()

    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch(
            "video_editor_asset_ingestion.providers.tts.elevenlabs_tts.probe_media",
            return_value=MediaInfo(tmp_path / "out.mp3", {"format": {"duration": "1.0"}}),
        ),
    ):
        provider = ElevenLabsTTSProvider(
            config=ProviderConfig(
                api_key_env="ELEVENLABS_API_KEY", default_model="eleven_flash_v2_5"
            )
        )
        await provider.generate(
            _req("Hello <break time='500ms'/> world.", "elevenlabs_tts"), tmp_path / "out.mp3"
        )
        assert "<break" in captured["text"]
        assert captured["text"].startswith("<speak>")

        # v3/v4 (the default) have no SSML: the break becomes a spoken pause.
        v4 = ElevenLabsTTSProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
        await v4.generate(
            _req("Hello <break time='500ms'/> world.", "elevenlabs_tts"), tmp_path / "out.mp3"
        )
        assert captured["text"] == "Hello ... world."


@pytest.mark.asyncio
async def test_openai_delegates_when_breaks_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI provider detects breaks and delegates to synthesize_with_breaks."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    request = _req("Part one. <break time='500ms'/> Part two.", "openai_tts")

    with (
        patch(
            "video_editor_asset_ingestion.providers.tts.openai_tts.synthesize_with_breaks"
        ) as mock_breaks,
        patch(
            "video_editor_asset_ingestion.providers.tts.openai_tts.OpenAITTSProvider._get_client",
            new=AsyncMock(),
        ),
    ):
        mock_breaks.return_value = AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(tmp_path / "out.mp3"),
            scene_id=request.scene_id,
            duration_ms=2500,
            metadata={},
        )
        provider = OpenAITTSProvider(config=ProviderConfig(api_key_env="OPENAI_API_KEY"))
        result = await provider.generate(request, tmp_path / "out.mp3")

    assert result.duration_ms == 2500
    mock_breaks.assert_awaited_once()


@pytest.mark.asyncio
async def test_sarvam_delegates_when_breaks_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sarvam provider detects breaks and delegates to synthesize_with_breaks."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    request = _req("Part one. <break time='500ms'/> Part two.", "sarvam_tts")
    request.params = {"voice_id": "sarvam:shubh", "language": "hi"}

    with patch(
        "video_editor_asset_ingestion.providers.tts.sarvam_tts.synthesize_with_breaks"
    ) as mock_breaks:
        mock_breaks.return_value = AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            local_path=str(tmp_path / "out.mp3"),
            scene_id=request.scene_id,
            duration_ms=2500,
            metadata={},
        )
        provider = SarvamTTSProvider()
        result = await provider.generate(request, tmp_path / "out.mp3")

    assert result.duration_ms == 2500
    mock_breaks.assert_awaited_once()
