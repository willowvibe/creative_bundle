"""Tests for the SarvamTTSProvider (mocked Sarvam API)."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from video_editor_asset_ingestion.models import AssetRequest, AssetType


@pytest.mark.asyncio
async def test_sarvam_tts_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_mp3_bytes = b"\x49\x44\x33" + b"\x03\x00" + b"\x00" * 32 + b"\x00" * 128
    fake_b64 = base64.b64encode(fake_mp3_bytes).decode("ascii")

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"request_id": "r-1", "audios": [fake_b64]}
        return resp

    out = tmp_path / "scene_001.mp3"
    req = AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="राजा बोले",
        provider="sarvam_tts",
        params={"voice_id": "sarvam:shubh", "language": "hi", "model": "bulbul:v3"},
    )
    with patch("httpx.AsyncClient.post", new=fake_post):
        provider = SarvamTTSProvider()
        result = await provider.generate(req, out)

    assert result.metadata["provider"] == "sarvam_tts"
    assert out.exists()
    assert out.read_bytes() == fake_mp3_bytes


@pytest.mark.asyncio
async def test_sarvam_tts_falls_back_to_v3_on_model_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_b64 = base64.b64encode(b"\x49\x44\x33\x04\x00" + b"\x00" * 64).decode("ascii")
    used_models: list[str] = []

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        body = kwargs.get("json") or {}
        used_models.append(body.get("model", ""))
        if body.get("model") == "bulbul:v4-flash":
            req = httpx.Request("POST", "https://api.sarvam.ai/text-to-speech")
            err_resp = httpx.Response(503, request=req)
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "model_unavailable", request=req, response=err_resp
                )
            )
        else:
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"request_id": "r-2", "audios": [fake_b64]}
        return resp

    out = tmp_path / "scene_001.mp3"
    req = AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="hello",
        provider="sarvam_tts",
        params={"voice_id": "sarvam:anushka", "language": "en", "model": "bulbul:v4-flash"},
    )
    with patch("httpx.AsyncClient.post", new=fake_post):
        provider = SarvamTTSProvider()
        result = await provider.generate(req, out)

    assert used_models == ["bulbul:v4-flash", "bulbul:v3"]
    assert result.metadata["model"] == "bulbul:v3"


def test_sarvam_tts_raises_on_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SARAVAM_API", raising=False)
    monkeypatch.delenv("SARVAM_API", raising=False)
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    with pytest.raises(RuntimeError, match="SARVAM_API"):
        SarvamTTSProvider()


def test_sarvam_tts_accepts_correctly_spelled_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SARVAM_API`` (the real product name) works even though config says ``SARAVAM_API``."""
    monkeypatch.delenv("SARAVAM_API", raising=False)
    monkeypatch.setenv("SARVAM_API", "correct-spelling-key")
    from video_editor_asset_ingestion.config import ProviderConfig
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    assert SarvamTTSProvider().api_key == "correct-spelling-key"
    configured = SarvamTTSProvider(ProviderConfig(api_key_env="SARAVAM_API"))
    assert configured.api_key == "correct-spelling-key"


def test_sarvam_tts_prefers_configured_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARAVAM_API", "legacy-key")
    monkeypatch.setenv("SARVAM_API", "new-key")
    from video_editor_asset_ingestion.config import ProviderConfig
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    assert SarvamTTSProvider(ProviderConfig(api_key_env="SARAVAM_API")).api_key == "legacy-key"


@pytest.mark.asyncio
async def test_sarvam_tts_probes_duration_ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sarvam must ffprobe the rendered file and forward ``duration_ms`` (Bug I1).

    Previously Sarvam returned ``duration_ms=0`` which made Sarvam-scene audio
    desync from TTS in FableCut. Mirror the ElevenLabs provider: probe the
    file, put the real ``duration_ms`` in the returned ``AssetResult``.
    """
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_mp3_bytes = b"\x49\x44\x33" + b"\x03\x00" + b"\x00" * 32 + b"\x00" * 128
    fake_b64 = base64.b64encode(fake_mp3_bytes).decode("ascii")

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"request_id": "r-1", "audios": [fake_b64]}
        return resp

    # Mock probe_media so no real ffprobe subprocess is needed.
    fake_info = MagicMock()
    fake_info.duration_ms.return_value = 2345
    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch(
            "video_editor_asset_ingestion.providers.tts.sarvam_tts.probe_media",
            return_value=fake_info,
        ) as mock_probe,
    ):
        provider = SarvamTTSProvider()
        out = tmp_path / "scene_001.mp3"
        req = AssetRequest(
            asset_id="scene_001_tts",
            job_id="job-1",
            scene_id="scene_001",
            asset_type=AssetType.TTS,
            prompt="hello",
            provider="sarvam_tts",
            params={"voice_id": "sarvam:shubh", "language": "hi"},
        )
        result = await provider.generate(req, out)

    assert result.duration_ms == 2345
    mock_probe.assert_called_once_with(out)


@pytest.mark.asyncio
async def test_sarvam_tts_probe_failure_falls_back_to_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``probe_media`` raises, Sarvam must fall back to ``duration_ms=0``
    (logging a warning) rather than crashing the whole TTS request.
    """
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    fake_mp3_bytes = b"\x49\x44\x33" + b"\x03\x00" + b"\x00" * 32 + b"\x00" * 128
    fake_b64 = base64.b64encode(fake_mp3_bytes).decode("ascii")

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"request_id": "r-1", "audios": [fake_b64]}
        return resp

    def failing_probe(_path):  # noqa: ARG001
        raise RuntimeError("ffprobe not installed")

    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch(
            "video_editor_asset_ingestion.providers.tts.sarvam_tts.probe_media",
            side_effect=failing_probe,
        ),
    ):
        provider = SarvamTTSProvider()
        out = tmp_path / "scene_001.mp3"
        req = AssetRequest(
            asset_id="scene_001_tts",
            job_id="job-1",
            scene_id="scene_001",
            asset_type=AssetType.TTS,
            prompt="hello",
            provider="sarvam_tts",
            params={"voice_id": "sarvam:shubh", "language": "hi"},
        )
        result = await provider.generate(req, out)

    assert result.duration_ms == 0
    assert out.exists()


@pytest.mark.asyncio
async def test_a_sarvam_clone_voice_routes_to_the_clone_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import (
        CLONE_PREFIX,
        SarvamTTSProvider,
    )

    fake_b64 = base64.b64encode(b"\x49\x44\x33\x04\x00" + b"\x00" * 64).decode("ascii")
    seen: dict[str, object] = {}

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        seen["url"] = url
        seen["files"] = kwargs.get("files")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"audio_b64": fake_b64, "request_id": "r-2"}
        return resp

    request = AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="Raja spoke.",
        provider="sarvam_tts",
        params={"voice_id": f"{CLONE_PREFIX}:svc-123", "language": "en-IN"},
    )
    with patch("httpx.AsyncClient.post", new=fake_post):
        result = await SarvamTTSProvider().generate(request, tmp_path / "clone.mp3")

    assert seen["url"] == "/voices/clone"
    files = seen["files"]
    assert isinstance(files, dict)
    assert files["voice_id"] == (None, "svc-123")
    assert files["text"] == (None, "Raja spoke.")
    assert files["language_code"] == (None, "en-IN")
    assert result.metadata["voice_id"] == f"{CLONE_PREFIX}:svc-123"


@pytest.mark.asyncio
async def test_a_clone_line_over_1000_chars_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import (
        MAX_CLONE_CHARS,
        SarvamTTSProvider,
    )

    request = AssetRequest(
        asset_id="long",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="x" * (MAX_CLONE_CHARS + 1),
        provider="sarvam_tts",
        params={"voice_id": "sarvam_clone:svc-123"},
    )
    with pytest.raises(RuntimeError, match="at most 1000 characters"):
        await SarvamTTSProvider().generate(request, tmp_path / "long.mp3")


def _pace_request(params: dict[str, object]) -> AssetRequest:
    return AssetRequest(
        asset_id="job_narration",
        job_id="job-1",
        scene_id="",
        asset_type=AssetType.TTS,
        prompt="The Rann of Kutch.",
        provider="sarvam_tts",
        params={"voice_id": "sarvam:sarang_en_narration", "language": "en", **params},
    )


async def _sent_payload(tmp_path: Path, request: AssetRequest) -> dict[str, object]:
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import SarvamTTSProvider

    sent: dict[str, object] = {}
    fake_b64 = base64.b64encode(b"\x49\x44\x33" + b"\x00" * 160).decode("ascii")

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        sent.update(kwargs.get("json") or {})
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"audios": [fake_b64]}
        return resp

    with patch("httpx.AsyncClient.post", new=fake_post):
        await SarvamTTSProvider().generate(request, tmp_path / "n.mp3")
    return sent


@pytest.mark.asyncio
async def test_a_pace_param_reaches_the_sarvam_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    sent = await _sent_payload(tmp_path, _pace_request({"pace": 1.15}))
    assert sent["pace"] == 1.15


@pytest.mark.asyncio
async def test_no_pace_param_leaves_the_api_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARVAM_API", "test-key")
    sent = await _sent_payload(tmp_path, _pace_request({}))
    assert "pace" not in sent


def _wav_bytes(seconds: float = 0.2, rate: int = 22050) -> bytes:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def test_wav_from_sarvam_is_converted_when_the_target_is_mp3(tmp_path):
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import write_audio

    out = tmp_path / "narration.mp3"
    write_audio(_wav_bytes(), out)
    data = out.read_bytes()
    assert not data.startswith(b"RIFF")
    assert data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert list(tmp_path.iterdir()) == [out]  # the temporary WAV is removed


def test_wav_target_and_non_wav_bytes_are_written_unchanged(tmp_path):
    from video_editor_asset_ingestion.providers.tts.sarvam_tts import write_audio

    wav = _wav_bytes()
    write_audio(wav, tmp_path / "a.wav")
    assert (tmp_path / "a.wav").read_bytes() == wav
    write_audio(b"ID3\x04fake", tmp_path / "b.mp3")
    assert (tmp_path / "b.mp3").read_bytes() == b"ID3\x04fake"
