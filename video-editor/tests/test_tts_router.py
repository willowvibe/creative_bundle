"""Tests for the TTSRouter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from video_editor_asset_ingestion.models import (
    AssetRequest,
    AssetResult,
    AssetType,
)


def _req(voice_id: str, language: str) -> AssetRequest:
    return AssetRequest(
        asset_id="scene_001_tts",
        job_id="job-1",
        scene_id="scene_001",
        asset_type=AssetType.TTS,
        prompt="hello",
        provider="tts_router",
        params={"voice_id": voice_id, "language": language},
    )


@pytest.mark.asyncio
async def test_indian_language_routes_to_sarvam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts", "model": "bulbul:v3"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts", "model": "eleven_v3"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("sarvam:shubh", "hi"), tmp_path / "a.mp3")

    assert called_with == ["sarvam"]


@pytest.mark.asyncio
async def test_english_routes_to_elevenlabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts", "model": "bulbul:v3"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts", "model": "eleven_v3"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("elevenlabs:Brian", "en"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


@pytest.mark.asyncio
async def test_explicit_prefix_overrides_language_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An English scene asking for sarvam:anushka must use Sarvam regardless."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts", "model": "bulbul:v3"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts", "model": "eleven_v3"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("sarvam:anushka", "en"), tmp_path / "a.mp3")

    assert called_with == ["sarvam"]


@pytest.mark.asyncio
async def test_kokoro_prefix_routes_to_kokoro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``kokoro:`` voice prefix routes to the Kokoro provider regardless of language."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_kokoro(self, request, output_path):  # noqa: ARG001
        called_with.append("kokoro")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "kokoro_tts", "voice_id": "af_bella"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.kokoro_tts",
                fromlist=["KokoroTTSProvider"],
            ).KokoroTTSProvider,
            "generate",
            new=fake_kokoro,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("kokoro:af_bella", "en"), tmp_path / "a.mp3")

    assert called_with == ["kokoro"]


@pytest.mark.asyncio
async def test_no_prefix_hindi_routes_to_elevenlabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prefix + lang ``hi`` → ElevenLabs, the default voice-over (2026-10-30)."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("shubh", "hi"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


@pytest.mark.asyncio
async def test_no_prefix_unsupported_language_routes_to_elevenlabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prefix + lang not in Sarvam or Kokoro sets → ElevenLabs fallback."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("Brian", "ru"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


@pytest.mark.asyncio
async def test_no_prefix_bcp47_hindi_in_routes_to_elevenlabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prefix + BCP-47 lang ``hi-IN`` → Sarvam (root lang is in the Sarvam set)."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_sarvam(self, request, output_path):  # noqa: ARG001
        called_with.append("sarvam")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "sarvam_tts"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.sarvam_tts",
                fromlist=["SarvamTTSProvider"],
            ).SarvamTTSProvider,
            "generate",
            new=fake_sarvam,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("shubh", "hi-IN"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


class TestDefaultRouting:
    """ElevenLabs is the default voice-over (2026-10-30); only an explicit prefix picks another provider."""

    @pytest.mark.parametrize(
        "language", ["en", "en-US", "fr", "ja", "hi", "hi-IN", "te", "ta", "ru"]
    )
    def test_no_prefix_routes_to_elevenlabs(self, language):
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider

        assert _pick_provider("af_bella", language) == "elevenlabs"

    def test_explicit_prefixes_still_win(self):
        from video_editor_asset_ingestion.providers.tts.tts_router import _pick_provider

        assert _pick_provider("kokoro:af_bella", "hi") == "kokoro"
        assert _pick_provider("sarvam:shubh", "en") == "sarvam"
        assert _pick_provider("kitten:x", "en") == "kitten_tts"
        assert _pick_provider("moss:x", "en") == "moss_tts"

    def test_default_voice_follows_language(self):
        from video_editor_asset_ingestion.providers.tts.tts_router import default_voice

        assert default_voice("te-IN") == "elevenlabs:RoLT1qx7XJRXxWSIG5hS"
        assert default_voice("ta") == "elevenlabs:yrFqUM5ku2rYJCdiBKFU"
        assert default_voice("en") == "elevenlabs:Brian"


@pytest.mark.asyncio
async def test_no_prefix_english_routes_to_elevenlabs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No prefix + lang ``en`` → Kokoro (English is in Kokoro's supported set)."""
    monkeypatch.setenv("SARAVAM_API", "test-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    called_with: list[str] = []

    async def fake_kokoro(self, request, output_path):  # noqa: ARG001
        called_with.append("kokoro")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "kokoro_tts", "voice_id": "af_bella"},
        )

    async def fake_elevenlabs(self, request, output_path):  # noqa: ARG001
        called_with.append("elevenlabs")
        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.TTS,
            scene_id=request.scene_id,
            local_path=str(output_path),
            duration_ms=1000,
            metadata={"provider": "elevenlabs_tts"},
        )

    with (
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.kokoro_tts",
                fromlist=["KokoroTTSProvider"],
            ).KokoroTTSProvider,
            "generate",
            new=fake_kokoro,
        ),
        patch.object(
            __import__(
                "video_editor_asset_ingestion.providers.tts.elevenlabs_tts",
                fromlist=["ElevenLabsTTSProvider"],
            ).ElevenLabsTTSProvider,
            "generate",
            new=fake_elevenlabs,
        ),
    ):
        router = TTSRouter()
        await router.generate(_req("af_bella", "en"), tmp_path / "a.mp3")

    assert called_with == ["elevenlabs"]


def test_the_routed_elevenlabs_delegate_finds_the_conventional_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the router used to build its delegate with a default
    ``ProviderConfig`` (empty ``api_key_env``), so a routed English request
    failed with "ElevenLabs API key is not configured" despite the key."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    from video_editor_asset_ingestion.providers.tts.tts_router import TTSRouter

    delegate = TTSRouter()._get("elevenlabs")

    assert delegate.api_key == "env-key"
