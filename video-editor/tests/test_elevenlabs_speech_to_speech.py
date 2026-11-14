"""Tests for the ElevenLabs speech-to-speech / voice conversion provider."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.audio.elevenlabs_speech_to_speech import (
    DEFAULT_OUTPUT_FORMAT,
    ElevenLabsSpeechToSpeechProvider,
    _reconcile_output_format,
)

from tests.conftest import _require_ffmpeg

# These providers are disabled in real use (ElevenLabs is voice-over only); test their HTTP code anyway.
pytestmark = pytest.mark.usefixtures("allow_elevenlabs_non_voice")


def _make_real_mp3(path: Path, seconds: float = 0.5) -> bytes:
    _require_ffmpeg()
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture
def sts_request_fixture(tmp_path: Path) -> tuple[AssetRequest, Path]:
    audio_path = tmp_path / "input.mp3"
    _make_real_mp3(audio_path, seconds=0.5)
    request = AssetRequest(
        asset_id="sts-1",
        job_id="job-sts",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice-id",
        provider="elevenlabs_speech_to_speech",
        params={
            "audio_path": str(audio_path),
            "voice_id": "target-voice-id",
            "model_id": "eleven_english_sts_v2",
        },
        output_ext="mp3",
    )
    return request, audio_path


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_with_mock(
    tmp_path: Path,
    sts_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the ElevenLabs STS endpoint and verify the result shape."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.5)

    captured: dict[str, Any] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["params"] = kwargs.get("params")
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    request, _ = sts_request_fixture
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.SPEECH_TO_SPEECH
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "elevenlabs_speech_to_speech"
    assert result.metadata.get("voice_id") == "target-voice-id"
    assert captured["url"] == "/v1/speech-to-speech/target-voice-id"
    assert captured["params"] == {"output_format": "mp3_44100_128"}
    assert captured["data"]["model_id"] == "eleven_english_sts_v2"


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_missing_api_key(
    tmp_path: Path,
    sts_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing API key raises a clear RuntimeError."""
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = None
    output_path = tmp_path / "out.mp3"
    request, _ = sts_request_fixture
    with pytest.raises(RuntimeError, match="ElevenLabs API key is not configured"):
        await provider.generate(request, output_path)


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_missing_voice_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request without a target voice id raises ValueError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"
    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.3)
    request = AssetRequest(
        asset_id="sts-bad",
        job_id="job-sts",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="",
        provider="elevenlabs_speech_to_speech",
        output_ext="mp3",
        params={"audio_path": str(input_path)},
    )
    with pytest.raises(ValueError, match="requires params\\['voice_id'\\]"):
        await provider.generate(request, tmp_path / "out.mp3")


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_rejects_html_error_body(
    tmp_path: Path,
    sts_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #69 regression: an HTML error page must not be saved as .mp3."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            text="<html><body>Internal Server Error</body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    request, _ = sts_request_fixture
    with pytest.raises(MediaContentError, match="Expected audio response"):
        await provider.generate(request, output_path)
    assert not output_path.exists()


def test_elevenlabs_speech_to_speech_registry_resolution() -> None:
    """The registry can resolve the new STS provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_speech_to_speech")
    assert cls is ElevenLabsSpeechToSpeechProvider


@pytest.mark.asyncio
async def test_stub_speech_to_speech_copies_input(tmp_path: Path) -> None:
    """The stub provider copies the input audio by default."""
    from video_editor_asset_ingestion.providers.audio.stub import StubSpeechToSpeechProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)
    request = AssetRequest(
        asset_id="sts-stub",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice",
        provider="stub_speech_to_speech",
        output_ext="mp3",
        params={"audio_path": str(input_path), "voice_id": "target-voice"},
    )
    provider = StubSpeechToSpeechProvider()
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert output_path.read_bytes() == input_path.read_bytes()
    assert result.asset_type == AssetType.SPEECH_TO_SPEECH
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_stub_speech_to_speech_applies_pitch_shift(tmp_path: Path) -> None:
    """When shift_semitones is set, the stub output differs from the input."""
    _require_ffmpeg()
    from video_editor_asset_ingestion.providers.audio.stub import StubSpeechToSpeechProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)
    request = AssetRequest(
        asset_id="sts-stub-shift",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice",
        provider="stub_speech_to_speech",
        output_ext="mp3",
        params={
            "audio_path": str(input_path),
            "voice_id": "target-voice",
            "shift_semitones": 2,
        },
    )
    provider = StubSpeechToSpeechProvider()
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert output_path.read_bytes() != input_path.read_bytes()
    assert result.asset_type == AssetType.SPEECH_TO_SPEECH
    assert result.duration_ms > 0


def test_speech_to_speech_request_builder() -> None:
    """``speech_to_speech_request()`` produces a job-level SPEECH_TO_SPEECH request."""
    from video_editor_asset_ingestion.scene_assets import speech_to_speech_request

    req = speech_to_speech_request(
        job_id="job-1",
        audio_path="/tmp/input.mp3",
        voice_id="target-voice",
        provider="elevenlabs_speech_to_speech",
        model_id="eleven_multilingual_sts_v2",
        remove_background_noise=True,
        output_ext="wav",
    )
    assert req.asset_type == AssetType.SPEECH_TO_SPEECH
    assert req.asset_id == "job_speech_to_speech"
    assert req.scene_id == "job"
    assert req.prompt == "target-voice"
    assert req.provider == "elevenlabs_speech_to_speech"
    assert req.output_ext == "wav"
    assert req.params["voice_id"] == "target-voice"
    assert req.params["model_id"] == "eleven_multilingual_sts_v2"
    assert req.params["remove_background_noise"] is True


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_uses_config_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #177: provider falls back to config default_model/default_voice."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    config = ProviderConfig(
        api_key_env="ELEVENLABS_API_KEY",
        default_model="eleven_multilingual_sts_v2",
        default_voice="cfg-voice",
    )
    provider = ElevenLabsSpeechToSpeechProvider(config=config)
    provider.api_key = "test-key"

    audio_path = tmp_path / "input.mp3"
    _make_real_mp3(audio_path, seconds=0.3)
    request = AssetRequest(
        asset_id="sts-defaults",
        job_id="job-sts",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="",
        provider="elevenlabs_speech_to_speech",
        output_ext="mp3",
        params={"audio_path": str(audio_path)},
    )

    captured_url: str = ""

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        nonlocal captured_url
        captured_url = url
        fake_request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            content=_make_real_mp3(tmp_path / "fake.mp3", seconds=0.3),
            request=fake_request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    await provider.generate(request, output_path)

    assert captured_url == "/v1/speech-to-speech/cfg-voice"


@pytest.mark.asyncio
async def test_elevenlabs_speech_to_speech_surfaces_http_error_body(
    tmp_path: Path,
    sts_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #190: a 4xx/5xx response body is included in the raised error."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            500,
            json={"detail": "model overloaded"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    request, _ = sts_request_fixture
    with pytest.raises(RuntimeError, match="model overloaded"):
        await provider.generate(request, output_path)


@pytest.mark.asyncio
async def test_stub_speech_to_speech_respects_wav_output_extension(
    tmp_path: Path,
) -> None:
    """Bug #179: stub STS must produce WAV bytes when output_ext='wav'."""
    _require_ffmpeg()
    from video_editor_asset_ingestion.providers.audio.stub import StubSpeechToSpeechProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)
    request = AssetRequest(
        asset_id="sts-stub-wav",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice",
        provider="stub_speech_to_speech",
        output_ext="wav",
        params={"audio_path": str(input_path), "voice_id": "target-voice"},
    )
    provider = StubSpeechToSpeechProvider()
    output_path = tmp_path / "out.wav"
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"RIFF")
    assert result.asset_type == AssetType.SPEECH_TO_SPEECH
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_stub_speech_to_speech_pitch_shift_uses_semitones(
    tmp_path: Path,
) -> None:
    """Bug #176: shift_semitones must convert to a rubberband pitch ratio."""
    _require_ffmpeg()
    from video_editor_asset_ingestion.providers.audio.stub import StubSpeechToSpeechProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)

    provider = StubSpeechToSpeechProvider()

    def make_request(shift: int) -> tuple[AssetRequest, Path]:
        request = AssetRequest(
            asset_id=f"sts-stub-{shift}",
            job_id="job-stub",
            scene_id="job",
            asset_type=AssetType.SPEECH_TO_SPEECH,
            prompt="target-voice",
            provider="stub_speech_to_speech",
            output_ext="mp3",
            params={
                "audio_path": str(input_path),
                "voice_id": "target-voice",
                "shift_semitones": shift,
            },
        )
        return request, tmp_path / f"out_{shift}.mp3"

    req_0, path_0 = make_request(0)
    await provider.generate(req_0, path_0)

    req_1, path_1 = make_request(1)
    await provider.generate(req_1, path_1)

    req_12, path_12 = make_request(12)
    await provider.generate(req_12, path_12)

    # 0 semitones is a copy; 1 and 12 semitones are different from the copy
    # and from each other.
    assert path_0.read_bytes() == input_path.read_bytes()
    assert path_1.read_bytes() != input_path.read_bytes()
    assert path_12.read_bytes() != input_path.read_bytes()
    assert path_1.read_bytes() != path_12.read_bytes()


def test_reconcile_output_format_basics() -> None:
    """Bug #178: extension must coerce the requested ElevenLabs format."""
    assert _reconcile_output_format("mp3_44100_128", ".mp3") == "mp3_44100_128"
    assert _reconcile_output_format("mp3_44100_128", "mp3") == "mp3_44100_128"
    assert _reconcile_output_format("mp3_44100_128", ".wav") == "pcm_44100"
    assert _reconcile_output_format("mp3_44100_128", "wav") == "pcm_44100"
    assert _reconcile_output_format("pcm_24000", ".wav") == "pcm_24000"
    assert _reconcile_output_format("ulaw_8000", ".wav") == "ulaw_8000"
    assert _reconcile_output_format("mp3_44100_128", ".flac") == "pcm_44100"
    assert _reconcile_output_format("mp3_44100_128", ".aiff") == "pcm_44100"
    assert _reconcile_output_format("mp3_44100_128", ".pcm") == "pcm_44100"


def test_speech_to_speech_request_builder_reconciles_format() -> None:
    """Bug #178: builder must send ElevenLabs a PCM format for .wav output."""
    from video_editor_asset_ingestion.scene_assets import speech_to_speech_request

    req = speech_to_speech_request(
        job_id="job-1",
        audio_path="/tmp/input.mp3",
        voice_id="target-voice",
        output_format="mp3_44100_128",
        output_ext="wav",
    )
    assert req.output_ext == "wav"
    assert req.params["output_format"] == "pcm_44100"


@pytest.mark.asyncio
async def test_elevenlabs_sts_reconciles_format_for_wav_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #178: provider coerces output_format to PCM when output_ext='wav'."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    audio_path = tmp_path / "input.mp3"
    _make_real_mp3(audio_path, seconds=0.3)

    captured_params: dict[str, Any] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured_params.update(cast(dict[str, Any], kwargs.get("params") or {}))
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            content=_make_real_mp3(tmp_path / "fake.mp3", seconds=0.3),
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="sts-wav",
        job_id="job-sts",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice",
        provider="elevenlabs_speech_to_speech",
        output_ext="wav",
        params={
            "audio_path": str(audio_path),
            "voice_id": "target-voice",
            "model_id": "eleven_english_sts_v2",
            "output_format": DEFAULT_OUTPUT_FORMAT,
        },
    )
    output_path = tmp_path / "out.wav"
    await provider.generate(request, output_path)

    assert captured_params["output_format"] == "pcm_44100"


@pytest.mark.asyncio
async def test_elevenlabs_sts_sends_voice_settings_and_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #180: provider forwards voice_settings and seed in the multipart form."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsSpeechToSpeechProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    audio_path = tmp_path / "input.mp3"
    _make_real_mp3(audio_path, seconds=0.3)

    captured_data: dict[str, Any] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured_data.update(cast(dict[str, Any], kwargs.get("data") or {}))
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            content=_make_real_mp3(tmp_path / "fake.mp3", seconds=0.3),
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    request = AssetRequest(
        asset_id="sts-settings",
        job_id="job-sts",
        scene_id="job",
        asset_type=AssetType.SPEECH_TO_SPEECH,
        prompt="target-voice",
        provider="elevenlabs_speech_to_speech",
        output_ext="mp3",
        params={
            "audio_path": str(audio_path),
            "voice_id": "target-voice",
            "model_id": "eleven_english_sts_v2",
            "voice_settings": {"stability": 0.75, "similarity_boost": 0.85},
            "seed": 42,
        },
    )
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)

    assert captured_data["voice_settings"] == '{"stability": 0.75, "similarity_boost": 0.85}'
    assert captured_data["seed"] == "42"
    assert result.metadata.get("seed") == 42
