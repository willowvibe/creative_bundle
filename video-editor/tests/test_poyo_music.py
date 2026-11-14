"""Tests for the PoyoMusicProvider (mocked PoYo API)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_poyo_music_happy_path(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    fake_mp3 = b"ID3" + b"\x00" * 64

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"data": {"task_id": "t-1"}}
        return resp

    async def fake_get(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if url.endswith("/status/t-1"):
            resp.json.return_value = {
                "data": {"status": "finished", "files": [{"file_url": "https://cdn.example/m.mp3"}]}
            }
        else:
            resp.json.return_value = {}
        return resp

    def fake_stream_get(self, url, *args, **kwargs):  # noqa: ARG001
        class _StreamResp:
            status_code = 200

            def raise_for_status(self_inner) -> None:  # noqa: N805
                return None

            async def aiter_bytes(self_inner):  # noqa: N805
                yield fake_mp3

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *exc):  # noqa: N805
                return None

        return _StreamResp()

    out = tmp_path / "track.mp3"
    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch("httpx.AsyncClient.get", new=fake_get),
        patch("httpx.AsyncClient.stream", new=fake_stream_get),
    ):
        provider = PoyoMusicProvider()
        result = await provider.generate_music("a calm chant", 30.0, out)

    assert result.provider == "poyo_music"
    assert result.model == "generate-music"
    assert out.exists() and out.read_bytes() == fake_mp3


@pytest.mark.asyncio
async def test_poyo_music_model_not_found_falls_back_to_minimax(tmp_path: Path) -> None:
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    fallback_model = "minimax-music-2.6"
    used_models: list[str] = []

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        body = kwargs.get("json") or {}
        used_models.append(body.get("model", ""))
        if body.get("model") == "generate-music":
            resp.json.return_value = {"data": {"task_id": None, "error": "model_not_found"}}
        else:
            resp.json.return_value = {"data": {"task_id": "t-2"}}
        return resp

    async def fake_get(self, url, *args, **kwargs):  # noqa: ARG001
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": {"status": "finished", "files": [{"file_url": "https://cdn.example/m.mp3"}]}
        }
        return resp

    def fake_stream(self, url, *args, **kwargs):  # noqa: ARG001
        class _StreamResp:
            status_code = 200

            def raise_for_status(self_inner) -> None:  # noqa: N805
                return None

            async def aiter_bytes(self_inner):  # noqa: N805
                yield b"ID3" + b"\x00" * 32

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *exc):  # noqa: N805
                return None

        return _StreamResp()

    out = tmp_path / "track.mp3"
    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch("httpx.AsyncClient.get", new=fake_get),
        patch("httpx.AsyncClient.stream", new=fake_stream),
    ):
        provider = PoyoMusicProvider(fallback_music_model=fallback_model)
        result = await provider.generate_music("chant", 30.0, out)

    assert used_models[0] == "generate-music"
    assert used_models[1] == "minimax-music-2.6"
    assert result.model == "minimax-music-2.6"


def test_each_backend_gets_the_input_shape_it_validates() -> None:
    """PoYo validates ``input`` per model, so one shared body cannot work.

    MiniMax rejects Suno's ``custom_mode``/``mv`` as unsupported fields and
    asks for ``is_instrumental``; Suno answers a bare prompt with
    "mv is required" (the field the provider used to omit entirely).
    """
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    provider = PoyoMusicProvider()
    minimax = provider._build_input("minimax-music-2.6", "calm pad")
    assert minimax == {"prompt": "calm pad", "is_instrumental": True}

    suno = provider._build_input("generate-music", "calm pad")
    assert suno["prompt"] == "calm pad"
    assert suno["custom_mode"] is False
    assert suno["mv"]
    assert "is_instrumental" not in suno


def test_the_default_model_ids_are_ones_poyo_serves() -> None:
    """Regression: the minimax id uses a dot and the default is Suno.

    ``minimax-music-2-6`` (hyphens) answers "Model not found", which is why
    every default music request failed outright. Suno is the default because
    both cost the same and it renders in seconds rather than minutes.
    """
    from video_editor_asset_ingestion.providers.audio.poyo_music import (
        DEFAULT_FALLBACK_MODEL,
        DEFAULT_MODEL,
        SUNO_MODELS,
    )

    assert DEFAULT_MODEL == "generate-music"
    assert DEFAULT_MODEL in SUNO_MODELS
    assert DEFAULT_FALLBACK_MODEL == "minimax-music-2.6"
    assert DEFAULT_FALLBACK_MODEL not in SUNO_MODELS
    assert "-2-6" not in DEFAULT_FALLBACK_MODEL


@pytest.mark.asyncio
async def test_a_rejected_model_id_falls_back_instead_of_aborting(tmp_path: Path) -> None:
    """PoYo reports an unknown model as HTTP 400, not as a missing task_id.

    ``raise_for_status`` used to abort the call, so the fallback model was
    never tried and the whole music request failed.
    """
    import httpx
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    used_models: list[str] = []

    def _response(status: int, **kwargs) -> httpx.Response:
        request = httpx.Request("POST", "https://api.poyo.ai/api/generate/submit")
        return httpx.Response(status, request=request, **kwargs)

    async def fake_post(self, url, *args, **kwargs):  # noqa: ARG001
        model = (kwargs.get("json") or {}).get("model")
        used_models.append(model)
        if model == "generate-music":
            return _response(400, json={"code": 400, "error": {"message": "Model not found"}})
        return _response(200, json={"data": {"task_id": "t-9"}})

    async def fake_get(self, url, *args, **kwargs):  # noqa: ARG001
        return _response(
            200,
            json={
                "data": {
                    "status": "finished",
                    # Suno reports ``audio_url``; the provider used to read
                    # only ``file_url`` and lost every finished Suno track.
                    "files": [{"audio_url": "https://cdn.example/m.mp3"}],
                }
            },
        )

    def fake_stream(self, url, *args, **kwargs):  # noqa: ARG001, ANN001
        class _StreamResp:
            status_code = 200

            def raise_for_status(self_inner) -> None:  # noqa: N805
                return None

            async def aiter_bytes(self_inner):  # noqa: N805
                yield b"ID3" + b"\x00" * 32

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *exc):  # noqa: N805
                return None

        return _StreamResp()

    out = tmp_path / "track.mp3"
    with (
        patch("httpx.AsyncClient.post", new=fake_post),
        patch("httpx.AsyncClient.get", new=fake_get),
        patch("httpx.AsyncClient.stream", new=fake_stream),
    ):
        result = await PoyoMusicProvider().generate_music("calm pad", 30.0, out)

    assert used_models == ["generate-music", "minimax-music-2.6"]
    assert result.model == "minimax-music-2.6"
    assert out.read_bytes().startswith(b"ID3")


def test_a_project_configured_model_is_not_silently_dropped() -> None:
    """``music_model:`` is not a ``ProviderConfig`` field.

    A YAML block shaped ``poyo_music: {music_model: ...}`` parses fine and
    loses the value, so the provider kept calling its built-in default while
    the project believed it had chosen a model. ``default_model`` (a real
    field) and ``provider_options.music_model`` must both be honoured.
    """
    from video_editor_asset_ingestion.config import ProviderConfig
    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    from_yaml_field = PoyoMusicProvider(ProviderConfig(default_model="minimax-music-2.6"))
    assert from_yaml_field.music_model == "minimax-music-2.6"

    from_options = PoyoMusicProvider(
        ProviderConfig(provider_options={"music_model": "generate-music"})
    )
    assert from_options.music_model == "generate-music"


def test_the_delivered_track_length_is_measured_not_the_requested_one(tmp_path: Path) -> None:
    """PoYo returns a whole composition, so the request is not the length.

    A 30 s request came back as a 224 s file. Reporting 30 s would make every
    downstream loop/trim decision wrong about what is on disk.
    """
    import subprocess

    from video_editor_asset_ingestion.providers.audio.poyo_music import PoyoMusicProvider

    from tests.conftest import _require_ffmpeg

    _require_ffmpeg()
    track = tmp_path / "track.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=2",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(track),
        ],
        check=True,
        capture_output=True,
    )

    measured = PoyoMusicProvider._track_duration(track, requested_seconds=30.0)
    assert measured == pytest.approx(2.0, abs=0.2)

    # An unprobeable file must not fail the build; it falls back to the request.
    unknown = tmp_path / "not-audio.mp3"
    unknown.write_bytes(b"not audio")
    assert PoyoMusicProvider._track_duration(unknown, requested_seconds=30.0) == 30.0


def _patch_poyo_http(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> dict[str, object]:
    """Stub the provider's HTTP/network boundary, not the manager."""
    from video_editor_asset_ingestion.providers.audio import poyo_music

    recorded: dict[str, object] = {}

    async def fake_get_client(self):  # noqa: ANN001
        return None

    async def fake_submit(self, client, model, prompt):  # noqa: ANN001, ARG001
        recorded["api_key"] = self.api_key
        recorded["model"] = model
        recorded["prompt"] = prompt
        return "task-1"

    async def fake_poll(self, client, task_id):  # noqa: ANN001, ARG001
        return "https://cdn.example/track.mp3"

    async def fake_download(self, url, output_path):  # noqa: ANN001, ARG001
        Path(output_path).write_bytes(payload)

    monkeypatch.setattr(poyo_music.PoyoMusicProvider, "_get_client", fake_get_client)
    monkeypatch.setattr(poyo_music.PoyoMusicProvider, "_submit", fake_submit)
    monkeypatch.setattr(poyo_music.PoyoMusicProvider, "_poll", fake_poll)
    monkeypatch.setattr(poyo_music.PoyoMusicProvider, "_download", fake_download)
    return recorded


@pytest.mark.asyncio
async def test_the_default_music_request_resolves_through_the_real_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manager calls ``generate(request, path)`` on every provider.

    ``poyo_music`` is the explainer pipeline's default, so it must expose that
    entry point and return an ``AssetResult`` like the other audio providers.
    """
    from video_editor_asset_ingestion.config import AssetProviderConfig
    from video_editor_asset_ingestion.manager import AssetIngestionManager
    from video_editor_asset_ingestion.models import AssetResult, AssetType
    from video_editor_asset_ingestion.scene_assets import music_request
    from video_editor_state.job import JobContext

    monkeypatch.setenv("POYO_API_KEY", "test-key")
    recorded = _patch_poyo_http(monkeypatch, b"ID3" + b"\x00" * 64)

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")
    config = AssetProviderConfig.model_validate(
        {"providers": {"poyo_music": {"api_key_env": "POYO_API_KEY"}}}
    )
    manager = AssetIngestionManager(ctx, config=config)
    request = music_request(
        "job", prompt="warm minimal bed", duration_seconds=18, provider="poyo_music"
    )

    results = await manager.resolve([request])

    # The manager hands the provider its configured credentials.
    assert recorded["api_key"] == "test-key"

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, AssetResult)
    assert result.asset_type == AssetType.MUSIC
    assert result.asset_id == "job_music"
    assert result.scene_id == "job"
    assert result.metadata["provider"] == "poyo_music"
    assert result.metadata["model"] == "generate-music"
    assert result.duration_ms == 18_000
    assert Path(result.local_path).is_file()
    assert Path(result.local_path).read_bytes().startswith(b"ID3")


@pytest.mark.asyncio
async def test_music_generate_handler_resolves_poyo_music(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ve music-generate --provider poyo_music`` goes through the manager."""
    from video_editor_pipelines.handlers import _handle_music_generate
    from video_editor_state.job import JobContext

    _patch_poyo_http(monkeypatch, b"ID3" + b"\x00" * 32)

    ctx = JobContext(job_id="job", work_root=tmp_path / "work", output_root=tmp_path / "out")

    await _handle_music_generate(
        ctx, {"prompt": "warm bed", "provider": "poyo_music", "duration_seconds": 12}
    )

    generated = ctx.work_dir / "music" / "generated_music.mp3"
    assert generated.is_file()
    assert generated.read_bytes().startswith(b"ID3")
