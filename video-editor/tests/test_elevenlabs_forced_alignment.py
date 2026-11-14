"""Tests for the ElevenLabs forced-alignment provider (Session V).

Forced alignment gives character-level and word-level timestamps
for a known audio + transcript pair via
``POST /v1/forced-alignment``. The tests below mirror the
pattern in ``test_elevenlabs_music.py`` and ``test_elevenlabs_sfx.py``:

- Mock the POST to a synthetic JSON response
- Verify the result shape and metadata
- Test per-request parameter overrides
- Test the missing-API-key error path
- Test the registry resolution
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_asset_ingestion.providers.alignment.elevenlabs_forced_alignment import (
    ElevenLabsForcedAlignmentProvider,
)
from video_editor_asset_ingestion.providers.alignment.stub import StubForcedAlignmentProvider
from video_editor_asset_ingestion.scene_assets import alignment_request

from tests.conftest import _require_ffmpeg

# These providers are disabled in real use (ElevenLabs is voice-over only); test their HTTP code anyway.
pytestmark = pytest.mark.usefixtures("allow_elevenlabs_non_voice")


def _make_real_audio(path: Path, seconds: float = 1.0) -> bytes:
    """Synthesize a short audio file so the alignment test has a real file to upload."""
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


def _fake_alignment_response(text: str = "hello world") -> dict[str, object]:
    """Build a JSON response that matches ElevenLabs' ``/v1/forced-alignment`` shape."""
    words_list = text.split()
    return {
        "words": [
            {
                "text": word,
                "start": i * 0.5,
                "end": (i + 1) * 0.5,
                "alignment": {
                    "character_start": sum(len(w) + 1 for w in words_list[:i]),
                    "character_end": sum(len(w) + 1 for w in words_list[:i]) + len(word),
                    "characters": [{"text": ch, "start": 0.0, "end": 0.0} for ch in word],
                },
            }
            for i, word in enumerate(words_list)
        ],
        "characters": [],
        "transcript": text,
        "language": "en",
    }


@pytest.fixture
def alignment_request_fixture(tmp_path: Path) -> AssetRequest:
    """Build a job-level alignment request, mirroring how the CLI builds one."""
    audio_path = tmp_path / "audio.mp3"
    _make_real_audio(audio_path, seconds=1.0)
    return alignment_request(
        job_id="job-align",
        audio_path=str(audio_path),
        text="hello world",
    )


@pytest.mark.asyncio
async def test_elevenlabs_alignment_with_mock(
    tmp_path: Path,
    alignment_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock the alignment endpoint and verify the result shape."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    payload = _fake_alignment_response("hello world")

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=payload, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    output_path = tmp_path / "out.json"
    result = await provider.generate(alignment_request_fixture, output_path)

    # The result is an ALIGNMENT AssetResult.
    assert result.asset_type == AssetType.ALIGNMENT
    assert result.metadata.get("provider") == "elevenlabs_forced_alignment"
    assert result.metadata.get("word_count") == 2
    # The audio duration is derived from the last word's end time.
    assert result.duration_ms == int(1.0 * 1000)  # "world" ends at 1.0
    # The full payload is stashed in metadata so downstream consumers
    # (e.g. the CaptionsSkill) can pick it up without re-parsing the file.
    assert result.metadata.get("alignment") == payload
    # The JSON file was written.
    assert output_path.exists()
    written = json.loads(output_path.read_text())
    assert written == payload


@pytest.mark.asyncio
async def test_elevenlabs_alignment_missing_audio_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alignment request without ``params['audio_path']`` raises a clear ValueError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    request = AssetRequest(
        asset_id="job_alignment",
        job_id="job-align",
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt="hello world",
        provider="elevenlabs_forced_alignment",
        output_ext="json",
        # params without audio_path
        params={"text": "hello world"},
    )
    with pytest.raises(ValueError, match="audio_path"):
        await provider.generate(request, tmp_path / "out.json")


@pytest.mark.asyncio
async def test_elevenlabs_alignment_missing_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alignment request without ``text`` raises a clear ValueError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    audio_path = tmp_path / "audio.mp3"
    _make_real_audio(audio_path, seconds=1.0)

    request = AssetRequest(
        asset_id="job_alignment",
        job_id="job-align",
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt="",  # empty
        provider="elevenlabs_forced_alignment",
        output_ext="json",
        params={"audio_path": str(audio_path)},
    )
    with pytest.raises(ValueError, match="text"):
        await provider.generate(request, tmp_path / "out.json")


@pytest.mark.asyncio
async def test_elevenlabs_alignment_audio_file_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing audio file on disk raises a clear FileNotFoundError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    request = AssetRequest(
        asset_id="job_alignment",
        job_id="job-align",
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt="hello world",
        provider="elevenlabs_forced_alignment",
        output_ext="json",
        params={"audio_path": str(tmp_path / "no_such_audio.mp3"), "text": "hello"},
    )
    with pytest.raises(FileNotFoundError, match="no_such_audio"):
        await provider.generate(request, tmp_path / "out.json")


@pytest.mark.asyncio
async def test_elevenlabs_alignment_invalid_response(
    tmp_path: Path,
    alignment_request_fixture: AssetRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response without a 'words' array raises a clear RuntimeError."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    provider.api_key = "test-key"

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"language": "en"}, request=request)

    client = await provider._get_client()
    monkeypatch.setattr(client, "post", fake_post)

    with pytest.raises(RuntimeError, match="words"):
        await provider.generate(alignment_request_fixture, tmp_path / "out.json")


@pytest.mark.asyncio
async def test_elevenlabs_alignment_missing_api_key(
    tmp_path: Path,
    alignment_request_fixture: AssetRequest,
) -> None:
    """Missing API key raises a clear RuntimeError (matches the music/sfx tests)."""
    provider = ElevenLabsForcedAlignmentProvider(
        config=ProviderConfig(api_key_env="ELEVENLABS_API_KEY")
    )
    # Deliberately leave api_key unset.
    provider.api_key = None
    with pytest.raises(RuntimeError, match="ElevenLabs API key is not configured"):
        await provider.generate(alignment_request_fixture, tmp_path / "out.json")


def test_elevenlabs_alignment_registry_resolution() -> None:
    """The registry can resolve the new alignment provider by name."""
    from video_editor_asset_ingestion.providers.registry import get_provider

    cls = get_provider("elevenlabs_forced_alignment")
    assert cls is ElevenLabsForcedAlignmentProvider
    cls2 = get_provider("stub_alignment")
    assert cls2 is StubForcedAlignmentProvider


# ---------------------------------------------------------------------------
# Stub provider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_alignment_writes_alignment_shape(tmp_path: Path) -> None:
    """The stub writes a JSON in the same shape as ElevenLabs' real response."""
    request = AssetRequest(
        asset_id="job_alignment",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt="hello world",
        provider="stub_alignment",
        output_ext="json",
        params={"text": "hello world", "audio_path": str(tmp_path / "audio.mp3")},
    )
    provider = StubForcedAlignmentProvider(seconds_per_word=0.25)
    output_path = tmp_path / "out.json"
    result = await provider.generate(request, output_path)

    assert result.asset_type == AssetType.ALIGNMENT
    assert result.metadata.get("provider") == "stub_alignment"
    assert result.metadata.get("synthetic") is True
    assert result.metadata.get("word_count") == 2
    # 2 words * 0.25s = 500ms
    assert result.duration_ms == 500

    payload = json.loads(output_path.read_text())
    assert payload["transcript"] == "hello world"
    assert payload["language"] == "en"
    assert len(payload["words"]) == 2
    assert payload["words"][0]["text"] == "hello"
    assert payload["words"][0]["start"] == 0.0
    assert payload["words"][0]["end"] == 0.25
    assert payload["words"][1]["text"] == "world"
    assert payload["words"][1]["start"] == 0.25
    assert payload["words"][1]["end"] == 0.5


@pytest.mark.asyncio
async def test_stub_alignment_requires_text(tmp_path: Path) -> None:
    """The stub provider raises a clear ValueError when no text is supplied."""
    request = AssetRequest(
        asset_id="job_alignment",
        job_id="job-stub",
        scene_id="job",
        asset_type=AssetType.ALIGNMENT,
        prompt="",
        provider="stub_alignment",
        output_ext="json",
        params={},
    )
    provider = StubForcedAlignmentProvider()
    with pytest.raises(ValueError, match="text"):
        await provider.generate(request, tmp_path / "out.json")
