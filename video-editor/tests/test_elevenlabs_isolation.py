"""Tests for the ElevenLabs audio isolation / voice extraction provider."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers._media import MediaContentError
from video_editor_asset_ingestion.providers.audio.elevenlabs_isolation import (
    ElevenLabsIsolationProvider,
    _reconcile_file_format,
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
def isolation_request_fixture(tmp_path: Path) -> tuple[AssetRequest, Path]:
    audio_path = tmp_path / "input.mp3"
    _make_real_mp3(audio_path, seconds=0.5)
    request = AssetRequest(
        asset_id="isolation-1",
        job_id="job-isolation",
        scene_id="job",
        asset_type=AssetType.ISOLATION,
        prompt="",
        provider="elevenlabs_isolation",
        params={"audio_path": str(audio_path)},
        output_ext="mp3",
    )
    return request, audio_path


@pytest.mark.asyncio
async def test_elevenlabs_isolation_with_mock(
    tmp_path: Path,
    isolation_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the ElevenLabs isolation endpoint and verify the result shape."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsIsolationProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    real_mp3 = tmp_path / "real.mp3"
    mp3_bytes = _make_real_mp3(real_mp3, seconds=0.5)

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, content=mp3_bytes, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    request, _ = isolation_request_fixture
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert result.asset_type == AssetType.ISOLATION
    assert result.duration_ms > 0
    assert result.metadata.get("provider") == "elevenlabs_isolation"
    assert result.metadata.get("file_format") == "other"


@pytest.mark.asyncio
async def test_elevenlabs_isolation_missing_api_key(
    tmp_path: Path,
    isolation_request_fixture: tuple[AssetRequest, Path],
) -> None:
    """Missing API key raises a clear RuntimeError."""
    provider = ElevenLabsIsolationProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = None
    output_path = tmp_path / "out.mp3"
    request, _ = isolation_request_fixture
    with pytest.raises(RuntimeError, match="ElevenLabs API key is not configured"):
        await provider.generate(request, output_path)


@pytest.mark.asyncio
async def test_elevenlabs_isolation_missing_audio_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request without ``audio_path`` raises ValueError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsIsolationProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"
    request = AssetRequest(
        asset_id="isolation-bad",
        job_id="job-isolation",
        scene_id="job",
        asset_type=AssetType.ISOLATION,
        prompt="",
        provider="elevenlabs_isolation",
        output_ext="mp3",
        params={},
    )
    with pytest.raises(ValueError, match="requires params\\['audio_path'\\]"):
        await provider.generate(request, tmp_path / "out.mp3")


@pytest.mark.asyncio
async def test_elevenlabs_isolation_rejects_html_error_body(
    tmp_path: Path,
    isolation_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #69 regression: an HTML error page must not be saved as .mp3."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsIsolationProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
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
    request, _ = isolation_request_fixture
    with pytest.raises(MediaContentError, match="Expected audio response"):
        await provider.generate(request, output_path)
    assert not output_path.exists()


def test_elevenlabs_isolation_registry_resolution() -> None:
    """The registry can resolve the new isolation provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_isolation")
    assert cls is ElevenLabsIsolationProvider


@pytest.mark.asyncio
async def test_stub_isolation_copies_input(tmp_path: Path) -> None:
    """The stub provider copies the input file so downstream stages get real audio."""
    from video_editor_asset_ingestion.providers.audio.stub import StubIsolationProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)
    request = AssetRequest(
        asset_id="isolation-stub",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.ISOLATION,
        prompt="",
        provider="stub_isolation",
        output_ext="mp3",
        params={"audio_path": str(input_path)},
    )
    provider = StubIsolationProvider()
    output_path = tmp_path / "out.mp3"
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert output_path.read_bytes() == input_path.read_bytes()
    assert result.asset_type == AssetType.ISOLATION
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_elevenlabs_isolation_surfaces_http_error_body(
    tmp_path: Path,
    isolation_request_fixture: tuple[AssetRequest, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #190: a 4xx/5xx response body is included in the raised error."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsIsolationProvider(config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY"))
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(
            400,
            json={"detail": "file too large"},
            request=request,
        )

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.mp3"
    request, _ = isolation_request_fixture
    with pytest.raises(RuntimeError, match="file too large"):
        await provider.generate(request, output_path)


def test_reconcile_file_format_basics() -> None:
    """Bug #175: output extension must coerce the ElevenLabs file_format."""
    assert _reconcile_file_format("other", ".mp3") == "other"
    assert _reconcile_file_format("other", "mp3") == "other"
    assert _reconcile_file_format("other", ".wav") == "pcm_s16le_16"
    assert _reconcile_file_format("other", "wav") == "pcm_s16le_16"
    assert _reconcile_file_format("pcm_s16le_16", ".wav") == "pcm_s16le_16"
    assert _reconcile_file_format("other", ".flac") == "pcm_s16le_16"
    assert _reconcile_file_format("other", ".aiff") == "pcm_s16le_16"
    assert _reconcile_file_format("bogus", ".mp3") == "other"


def test_isolation_request_builder_reconciles_format() -> None:
    """Bug #175: builder coerces file_format to pcm_s16le_16 for .wav output."""
    from video_editor_asset_ingestion.scene_assets import isolation_request

    req = isolation_request(
        job_id="job-1",
        audio_path="/tmp/input.mp3",
        output_ext="wav",
    )
    assert req.output_ext == "wav"
    assert req.params["file_format"] == "pcm_s16le_16"


def test_isolation_request_builder_explicit_format() -> None:
    """An explicit file_format is preserved when consistent with the extension."""
    from video_editor_asset_ingestion.scene_assets import isolation_request

    req = isolation_request(
        job_id="job-1",
        audio_path="/tmp/input.mp3",
        file_format="pcm_s16le_16",
        output_ext="wav",
    )
    assert req.asset_type == AssetType.ISOLATION
    assert req.asset_id == "job_isolation"
    assert req.scene_id == "job"
    assert req.provider == ""  # no default: ElevenLabs isolation is off (voice-over only)
    assert req.output_ext == "wav"
    assert req.params["audio_path"] == "/tmp/input.mp3"
    assert req.params["file_format"] == "pcm_s16le_16"


@pytest.mark.asyncio
async def test_stub_isolation_respects_wav_output_extension(
    tmp_path: Path,
) -> None:
    """Bug #175: stub isolation must produce WAV bytes when output_ext='wav'."""
    _require_ffmpeg()
    from video_editor_asset_ingestion.providers.audio.stub import StubIsolationProvider

    input_path = tmp_path / "input.mp3"
    _make_real_mp3(input_path, seconds=0.4)
    request = AssetRequest(
        asset_id="isolation-stub-wav",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.ISOLATION,
        prompt="",
        provider="stub_isolation",
        output_ext="wav",
        params={"audio_path": str(input_path)},
    )
    provider = StubIsolationProvider()
    output_path = tmp_path / "out.wav"
    result = await provider.generate(request, output_path)

    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"RIFF")
    assert result.asset_type == AssetType.ISOLATION
    assert result.duration_ms > 0
