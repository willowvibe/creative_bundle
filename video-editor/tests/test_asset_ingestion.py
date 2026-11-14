"""Tests for the generative asset ingestion pipeline."""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.scene_assets import requests_from_scene
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _clear_asset_manager_class_state() -> None:
    """Isolate class-level semaphores and circuit breakers between tests."""
    AssetIngestionManager._semaphores.clear()
    AssetIngestionManager._circuit_breakers.clear()


class TestModelsPhase1:
    """Phase 1: core scaffolding and state schema."""

    def test_cache_paths_are_absolute_with_a_relative_work_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Providers child-process resolve paths; a relative work root must not leak."""
        monkeypatch.chdir(tmp_path)
        ctx = JobContext(
            job_id="job-abs",
            work_root=Path("projects"),
            output_root=Path("outputs"),
        )
        manager = AssetIngestionManager(ctx)
        request = AssetRequest(
            asset_id="a1",
            job_id="job-abs",
            scene_id="scene-1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        media_path, meta_path = manager._cache_paths(request)

        expected_dir = (tmp_path / "projects" / "job-abs" / ".cache" / "assets").resolve()
        assert media_path.is_absolute()
        assert meta_path.is_absolute()
        assert media_path.parent == expected_dir
        assert manager.cache_dir.is_absolute()

    def test_asset_request_cache_key_is_stable(self) -> None:
        request = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.TTS,
            prompt="Hello world",
            provider="openai_tts",
            params={"voice": "alloy", "model": "tts-1"},
            output_ext="mp3",
        )
        assert request.cache_key() == request.cache_key()

    def test_asset_request_cache_key_changes_with_params(self) -> None:
        base = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.TTS,
            prompt="Hello world",
            provider="openai_tts",
            params={"voice": "alloy"},
            output_ext="mp3",
        )
        changed = base.model_copy(update={"params": {"voice": "echo"}})
        assert base.cache_key() != changed.cache_key()

    def test_asset_request_normalizes_extension(self) -> None:
        request = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.IMAGE,
            prompt="A chart",
            output_ext=".png",
        )
        assert request.output_ext == "png"

    def test_asset_result_stores_posix_path(self, tmp_path: Path) -> None:
        target = tmp_path / "sub" / "file.mp3"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        result = AssetResult(
            asset_id="a1",
            asset_type=AssetType.TTS,
            local_path=str(target),
            duration_ms=1234,
        )
        assert "/" in result.local_path
        assert not result.local_path.endswith("\\")
        assert result.resolved_path().exists()

    def test_scene_plan_roundtrips_assets(self, tmp_path: Path) -> None:
        asset = AssetResult(
            asset_id="tts-1",
            asset_type=AssetType.TTS,
            local_path=str(tmp_path / "audio.mp3"),
            duration_ms=1500,
            cached=False,
        )
        scene = Scene(
            id="s1",
            type="narration",
            duration_seconds=5.0,
            narration="Hello",
            assets=[asset],
        )
        plan = ScenePlan(job_id="job-1", script="Hello world", scenes=[scene])

        raw = plan.model_dump(mode="json")
        loaded = ScenePlan(**raw)

        assert len(loaded.scenes) == 1
        assert len(loaded.scenes[0].assets) == 1
        assert loaded.scenes[0].assets[0].asset_type == AssetType.TTS
        assert loaded.scenes[0].assets[0].duration_ms == 1500


class TestTTSProviderPhase2:
    """Phase 2: TTS provider integration."""

    @pytest.fixture
    def tts_request(self) -> AssetRequest:
        return AssetRequest(
            asset_id="tts-1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.TTS,
            prompt="Hello world",
            provider="openai_tts",
            params={"voice": "alloy", "model": "tts-1"},
            output_ext="mp3",
        )

    @pytest.mark.asyncio
    async def test_openai_tts_provider_with_mock(
        self,
        tmp_path: Path,
        tts_request: AssetRequest,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _require_ffmpeg()
        import subprocess

        import httpx
        from video_editor_asset_ingestion.config import ProviderConfig
        from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider

        provider = OpenAITTSProvider(config=ProviderConfig(api_key_env="OPENAI_API_KEY"))
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        provider.api_key = "test-key"

        # Generate a real tiny MP3 so probe_media can parse duration.
        real_mp3 = tmp_path / "real.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.5",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(real_mp3),
            ],
            check=True,
            capture_output=True,
        )
        mp3_bytes = real_mp3.read_bytes()

        async def fake_post(url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            return httpx.Response(200, content=mp3_bytes, request=request)

        client = await provider._get_client()
        monkeypatch.setattr(client, "post", fake_post)

        output_path = tmp_path / "out.mp3"
        result = await provider.generate(tts_request, output_path)

        assert output_path.exists()
        assert result.asset_type == AssetType.TTS
        assert result.duration_ms > 0
        assert result.metadata.get("provider") == "openai_tts"

    @pytest.mark.asyncio
    async def test_openai_tts_rejects_json_error_body(
        self,
        tmp_path: Path,
        tts_request: AssetRequest,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #69 regression: a JSON error body must not be saved as .mp3."""
        import httpx
        from video_editor_asset_ingestion.providers._media import MediaContentError
        from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider

        provider = OpenAITTSProvider(config=ProviderConfig(api_key_env="OPENAI_API_KEY"))
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        provider.api_key = "test-key"

        async def fake_post(url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"error": "voice unavailable"},
                headers={"content-type": "application/json"},
                request=request,
            )

        client = await provider._get_client()
        monkeypatch.setattr(client, "post", fake_post)

        output_path = tmp_path / "out.mp3"
        with pytest.raises(MediaContentError, match="Expected audio response"):
            await provider.generate(tts_request, output_path)
        assert not output_path.exists()

    @pytest.mark.asyncio
    async def test_stub_tts_provider_generates_audio(
        self,
        tmp_path: Path,
        tts_request: AssetRequest,
    ) -> None:
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        provider = StubTTSProvider(duration_seconds=0.5)
        tts_request.params["duration"] = 0.5
        output_path = tmp_path / "stub.mp3"
        result = await provider.generate(tts_request, output_path)

        assert output_path.exists()
        assert result.asset_type == AssetType.TTS
        assert result.duration_ms >= 400
        assert result.metadata.get("provider") == "stub_tts"

    def test_provider_registry_resolves_openai_tts(self) -> None:
        from video_editor_asset_ingestion.providers.registry import get_provider
        from video_editor_asset_ingestion.providers.tts.openai_tts import OpenAITTSProvider

        cls = get_provider("openai_tts")
        assert cls is OpenAITTSProvider


class TestVisualProvidersPhase3:
    """Phase 3: visual generation and data visualization."""

    @pytest.mark.asyncio
    async def test_matplotlib_data_viz_renders_png(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.providers.visual.data_viz import MatplotlibDataVizProvider

        request = AssetRequest(
            asset_id="dv-1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.DATA_VIZ,
            prompt="Revenue chart",
            provider="matplotlib_data_viz",
            params={
                "data": {
                    "chart_type": "bar",
                    "title": "Q1 Revenue",
                    "labels": ["Jan", "Feb", "Mar"],
                    "values": [10, 20, 15],
                },
            },
            output_ext="png",
        )
        provider = MatplotlibDataVizProvider(width=640, height=360, dpi=72)
        output_path = tmp_path / "chart.png"
        result = await provider.generate(request, output_path)

        assert output_path.exists()
        assert output_path.stat().st_size > 0
        assert result.asset_type == AssetType.DATA_VIZ
        assert result.metadata.get("chart_type") == "bar"

    @pytest.mark.asyncio
    async def test_stub_image_provider_generates_image(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

        request = AssetRequest(
            asset_id="img-1",
            job_id="job-1",
            scene_id="scene-1",
            asset_type=AssetType.IMAGE,
            prompt="A gradient background",
            provider="stub_image",
            output_ext="png",
        )
        provider = StubImageProvider(width=320, height=240)
        output_path = tmp_path / "image.png"
        result = await provider.generate(request, output_path)

        assert output_path.exists()
        assert output_path.stat().st_size > 0
        assert result.asset_type == AssetType.IMAGE
        assert result.metadata.get("width") == 320


class TestManagerPhase4:
    """Phase 4: orchestrator manager and cache."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        ctx = JobContext(
            job_id="manager-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        return AssetIngestionManager(ctx)

    @pytest.mark.asyncio
    async def test_manager_resolves_multiple_assets_concurrently(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
    ) -> None:
        requests = [
            AssetRequest(
                asset_id="tts-1",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.TTS,
                prompt="First",
                provider="stub_tts",
                params={"duration": 0.3},
                output_ext="mp3",
            ),
            AssetRequest(
                asset_id="img-1",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.IMAGE,
                prompt="A background",
                provider="stub_image",
                output_ext="png",
            ),
        ]
        results = await manager.resolve(requests)

        assert len(results) == 2
        assert all(r.resolved_path().exists() for r in results)
        assert results[0].asset_type == AssetType.TTS
        assert results[1].asset_type == AssetType.IMAGE

        # Cache meta files were written.
        cache_files = list(manager.cache_dir.iterdir())
        assert any(p.suffix == ".json" for p in cache_files)

    @pytest.mark.asyncio
    async def test_manager_uses_cache_on_second_run(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
    ) -> None:
        request = AssetRequest(
            asset_id="dv-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.DATA_VIZ,
            prompt="Chart",
            provider="matplotlib_data_viz",
            params={
                "data": {
                    "chart_type": "line",
                    "labels": ["a", "b"],
                    "values": [1, 2],
                },
            },
            output_ext="png",
        )
        first = await manager.resolve([request])
        assert first[0].cached is False

        second = await manager.resolve([request])
        assert second[0].cached is True
        assert second[0].local_path == first[0].local_path

    @pytest.mark.asyncio
    async def test_manager_constructs_agy_image_with_provider_config(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
        from video_editor_asset_ingestion.models import AssetRequest

        ctx = JobContext(
            job_id="agy-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(
            providers={"agy_image": ProviderConfig(provider_options={"agy_bin": "/bin/true"})}
        )
        manager = AssetIngestionManager(ctx, config=config)
        request = AssetRequest(
            asset_id="img-1",
            job_id="agy-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="agy_image",
            output_ext="png",
        )

        provider = manager._resolve_provider(request)

        assert provider.provider_name == "agy_image"
        assert provider.agy_bin == "/bin/true"


class TestManagerBatchAndConcurrency:
    """Regression tests for Batch G asset-manager improvements:
    partial batch results (#83), provider instance reuse (#84), bounded
    per-provider concurrency (#85), and circuit breaker (#82).
    """

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        # Isolate class-level state so test ordering doesn't affect results.
        AssetIngestionManager._semaphores.clear()
        AssetIngestionManager._circuit_breakers.clear()
        ctx = JobContext(
            job_id="batch-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        return AssetIngestionManager(ctx)

    @pytest.mark.asyncio
    async def test_resolve_partial_returns_successes_and_failures(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #83: a batch with one failure must still return the successes."""
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        async def flaky_generate(
            self_obj: Any,
            request: AssetRequest,
            output_path: Path,
        ) -> AssetResult:
            if request.asset_id == "fail":
                raise RuntimeError("boom")
            return cast(AssetResult, await original_generate(self_obj, request, output_path))

        original_generate: Any = StubTTSProvider.generate
        monkeypatch.setattr(StubTTSProvider, "generate", flaky_generate)

        requests = [
            AssetRequest(
                asset_id="ok",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.TTS,
                prompt="ok",
                provider="stub_tts",
                params={"duration": 0.1},
                output_ext="mp3",
            ),
            AssetRequest(
                asset_id="fail",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.TTS,
                prompt="fail",
                provider="stub_tts",
                params={"duration": 0.1},
                output_ext="mp3",
            ),
        ]
        batch = await manager.resolve_partial(requests)

        assert {r.asset_id for r in batch.results} == {"ok"}
        assert batch.failed_asset_ids == {"fail"}

    @pytest.mark.asyncio
    async def test_provider_instance_is_reused_within_manager(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #84: the manager must create only one provider instance per
        provider name, even when resolving multiple requests."""
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        calls: list[Any] = []
        original_init: Any = StubTTSProvider.__init__

        def counting_init(self_obj: Any, *args: Any, **kwargs: Any) -> None:
            calls.append(self_obj)
            original_init(self_obj, *args, **kwargs)

        monkeypatch.setattr(StubTTSProvider, "__init__", counting_init)

        requests = [
            AssetRequest(
                asset_id=f"tts-{i}",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.TTS,
                prompt="hi",
                provider="stub_tts",
                params={"duration": 0.1},
                output_ext="mp3",
            )
            for i in range(3)
        ]
        results = await manager.resolve(requests)
        assert len(results) == 3
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_per_provider_concurrency_is_bounded(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #85: no more than the configured limit of requests for a single
        provider should run concurrently.
        """
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        active = 0
        max_active = 0
        original_generate: Any = StubTTSProvider.generate

        async def slow_generate(
            self_obj: Any,
            request: AssetRequest,
            output_path: Path,
        ) -> AssetResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return cast(AssetResult, await original_generate(self_obj, request, output_path))

        monkeypatch.setattr(StubTTSProvider, "generate", slow_generate)
        # Force the stub_tts semaphore to a limit of 1 for this test.
        manager._semaphores["stub_tts"] = asyncio.Semaphore(1)

        requests = [
            AssetRequest(
                asset_id=f"tts-{i}",
                job_id=manager.ctx.job_id,
                scene_id="s1",
                asset_type=AssetType.TTS,
                prompt="hi",
                provider="stub_tts",
                params={"duration": 0.1},
                output_ext="mp3",
            )
            for i in range(3)
        ]
        await manager.resolve(requests)
        assert max_active == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_repeated_failures(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #82: after ``failure_threshold`` consecutive final failures,
        the circuit breaker opens and fast-fails new requests without calling
        the provider again.
        """
        from video_editor_asset_ingestion.manager import CircuitBreakerOpenError
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        async def always_fail(*_args: object, **_kwargs: object) -> AssetResult:
            raise RuntimeError("provider is down")

        monkeypatch.setattr(StubTTSProvider, "generate", always_fail)

        request = AssetRequest(
            asset_id=" doomed",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.TTS,
            prompt="hi",
            provider="stub_tts",
            params={"duration": 0.1},
            output_ext="mp3",
        )
        # Three failures should trip the breaker. Generic RuntimeError is not
        # retryable, so each call fails immediately without tenacity backoff.
        for _ in range(3):
            with pytest.raises(RuntimeError, match="provider is down"):
                await manager._resolve_one(request)

        # The next call must raise CircuitBreakerOpenError without running generate.
        with pytest.raises(CircuitBreakerOpenError, match="Circuit breaker open"):
            await manager._resolve_one(request)


class TestManagerRetryAndFallback:
    """Regression tests for the tenacity retry predicate and FAL placeholder fallback."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        ctx = JobContext(
            job_id="retry-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        return AssetIngestionManager(ctx)

    @pytest.mark.asyncio
    async def test_fal_failure_falls_back_to_stub(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #10 regression: when FAL fails, fall back to a stub placeholder.

        The manager should not let a single FAL outage take down the whole
        job — it should warn-log the failure, generate a stub image, and
        return a valid ``AssetResult`` so downstream stages have something
        to render.
        """
        # The lenient mode under test; ``assets.strict`` (the default) raises (#281).
        manager.config.strict = False
        import httpx
        from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

        # Build a request that resolves to a real FalAIProvider.
        request = AssetRequest(
            asset_id="fal-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="A scenic mountain",
            provider="fal_ai",
            output_ext="png",
        )

        # Patch FalAIProvider.generate to always raise a connection-style error.
        async def always_fail(*_args: object, **_kwargs: object) -> AssetResult:
            raise httpx.ConnectError("FAL is down")

        monkeypatch.setattr(FalAIProvider, "generate", always_fail)

        # Ensure provider resolution still finds FalAIProvider (registry intact).
        # We do not need to call resolve() — call _generate_one directly with
        # a pre-resolved provider.
        media_path = manager.cache_dir / f"{request.cache_key()}.png"
        meta_path = manager.cache_dir / f"{request.cache_key()}.meta.json"

        # _generate_one is decorated with @retry, but the fallback happens
        # *after* retries are exhausted, so it still raises once before
        # falling back. We invoke the underlying coroutine through
        # ``manager._generate_one.__wrapped__`` to call it without retries
        # for the test (so we don't have to wait for the backoff).
        underlying = manager._generate_one.__wrapped__  # type: ignore[attr-defined]
        result, cost_status = await underlying(manager, request, media_path, meta_path)

        assert cost_status == "fallback"
        assert result.asset_id == request.asset_id
        assert result.asset_type == AssetType.IMAGE
        assert result.metadata.get("provider") == "stub_image"
        assert media_path.exists()
        assert media_path.stat().st_size > 0
        # Bug #53 regression: fallback results must NOT be cached, otherwise
        # a single outage permanently poisons the cache with stub output.
        assert not meta_path.exists()

    def test_called_process_error_is_not_retryable(self) -> None:
        """Bug #121: subprocess failures must not be retried.

        A failing ffmpeg probe or local command is a caller-side problem.
        Retrying it wastes time and masks the real error.
        """
        import subprocess

        from video_editor_asset_ingestion.manager import _is_retryable

        assert not _is_retryable(subprocess.CalledProcessError(1, "ffmpeg"))

    def test_transport_errors_are_retryable(self) -> None:
        """Bug #121: transport-level failures should still be retried."""
        import httpx
        from video_editor_asset_ingestion.manager import _is_retryable

        assert _is_retryable(httpx.ConnectError("network down"))

    @pytest.mark.asyncio
    async def test_non_fal_failure_is_not_swallowed(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only FAL failures should fall back; other provider errors should propagate."""
        from video_editor_asset_ingestion.providers.visual.stub import StubImageProvider

        request = AssetRequest(
            asset_id="stub-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="A scenic mountain",
            provider="stub_image",
            output_ext="png",
        )

        async def always_fail(*_args: object, **_kwargs: object) -> AssetResult:
            raise RuntimeError("simulated stub failure")

        monkeypatch.setattr(StubImageProvider, "generate", always_fail)

        media_path = manager.cache_dir / f"{request.cache_key()}.png"
        meta_path = manager.cache_dir / f"{request.cache_key()}.meta.json"
        underlying = manager._generate_one.__wrapped__  # type: ignore[attr-defined]

        with pytest.raises(RuntimeError, match="simulated stub failure"):
            await underlying(manager, request, media_path, meta_path)

    @pytest.mark.asyncio
    async def test_fal_fallback_still_trips_circuit_breaker(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """C13 regression: a FAL fallback result must still count as a primary
        provider failure so the circuit breaker can open during an outage."""
        # The lenient mode under test; ``assets.strict`` (the default) raises (#281).
        manager.config.strict = False
        import httpx
        from video_editor_asset_ingestion.manager import CircuitBreakerOpenError
        from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

        monkeypatch.setenv("FAL_KEY", "test-key")

        async def always_fail(*_args: object, **_kwargs: object) -> AssetResult:
            raise httpx.ConnectError("FAL is down")

        monkeypatch.setattr(FalAIProvider, "generate", always_fail)

        request = AssetRequest(
            asset_id="fal-cb-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="A mountain",
            provider="fal_ai",
            output_ext="png",
        )

        # Three fallback successes should trip the breaker.
        for _ in range(3):
            result = await manager._resolve_one(request)
            assert result.asset_type == AssetType.IMAGE
            assert result.metadata.get("provider") == "stub_image"
            assert result.metadata.get("_fallback_from") == "fal_ai"

        # The fourth call must fast-fail with an open circuit breaker.
        with pytest.raises(CircuitBreakerOpenError, match="Circuit breaker open"):
            await manager._resolve_one(request)


class TestCacheAtomicityAndDeduplication:
    """Bug #67 regression: atomic cache writes + per-key concurrency dedup."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        ctx = JobContext(
            job_id="atomic-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        return AssetIngestionManager(ctx)

    def test_save_cached_writes_meta_atomically(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
    ) -> None:
        from video_editor_asset_ingestion.models import AssetResult

        result = AssetResult(
            asset_id="a1",
            asset_type=AssetType.TTS,
            local_path=str(tmp_path / "a1.mp3"),
            duration_ms=1000,
        )
        meta_path = manager.cache_dir / "a1.meta.json"
        manager._save_cached(result, meta_path)

        assert meta_path.exists()
        # No temp files should be left behind after atomic rename.
        assert not any(p.name.startswith("a1.meta.json.tmp") for p in manager.cache_dir.iterdir())

    @pytest.mark.asyncio
    async def test_resolve_one_dedups_concurrent_same_key_requests(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two concurrent requests for the same key must generate only once."""
        from video_editor_asset_ingestion.providers.tts.stub import StubTTSProvider

        request = AssetRequest(
            asset_id="tts-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.TTS,
            prompt="Hello",
            provider="stub_tts",
            params={"duration": 0.1},
            output_ext="mp3",
        )

        call_count = 0

        async def slow_generate(
            self: StubTTSProvider,
            request: AssetRequest,
            output_path: Path,
        ) -> AssetResult:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return await original_generate(self, request, output_path)

        original_generate = StubTTSProvider.generate
        monkeypatch.setattr(StubTTSProvider, "generate", slow_generate)

        results = await asyncio.gather(
            manager._resolve_one(request),
            manager._resolve_one(request),
        )
        assert len(results) == 2
        assert call_count == 1
        assert results[0].local_path == results[1].local_path

    @pytest.mark.asyncio
    async def test_key_locks_use_weak_references(
        self,
        tmp_path: Path,
        manager: AssetIngestionManager,
    ) -> None:
        """C15 regression: per-key locks must not be retained forever for
        every distinct cache key processed by a long-running worker."""
        import gc
        import weakref

        assert isinstance(manager._key_locks, weakref.WeakValueDictionary)

        async def acquire_release(key: str) -> None:
            async with manager._key_lock(key):
                pass

        await asyncio.gather(*(acquire_release(f"key-{i}") for i in range(50)))
        gc.collect()

        # After all locks are released and collected, the weak dict should
        # be empty (or at least bounded; it must not hold all 50 entries).
        assert len(manager._key_locks) == 0


class TestProviderConfigValidation:
    """Bug #66 / #70 regression: explicit provider requests must be validated."""

    def test_provider_for_missing_preferred_raises(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.config import (
            AssetProviderConfig,
            ProviderConfig,
            ProviderConfigError,
        )

        config = AssetProviderConfig(
            providers={"openai_tts": ProviderConfig(api_key_env="OPENAI_API_KEY")},
            defaults={"tts": "openai_tts"},
        )
        # Default resolution still works.
        assert config.provider_for("tts")[0] == "openai_tts"

        # Explicit but missing provider should raise, not silently return default.
        with pytest.raises(ProviderConfigError, match="not configured"):
            config.provider_for("tts", preferred="elevenlabs_tts")

    def test_load_warns_on_unknown_assets_keys(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #70 regression: unknown keys in the assets section warn."""

        from video_editor_asset_ingestion.config import AssetProviderConfig

        path = tmp_path / "config.yaml"
        path.write_text(
            "assets:\n  unknown_key: 1\n  providers: {}\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        with pytest.warns(UserWarning, match="unknown keys"):
            AssetProviderConfig.load()


class TestRequestsFromScene:
    """Tests for the ``requests_from_scene`` helper.

    Bug #49 regression: ``requests_from_scene`` used to only create
    TTS/IMAGE/DATA_VIZ requests. The new ``video_provider`` parameter
    adds a VIDEO request (appended before IMAGE so it wins the
    renderer's ``scene.assets.find`` race).
    """

    def _make_scene(
        self,
        *,
        scene_id: str = "s1",
        narration: str = "Hello",
        visual_description: str = "A scenic mountain at dawn",
        asset_query: str = "",
        props: dict[str, Any] | None = None,
    ) -> Scene:
        return Scene(
            id=scene_id,
            type="narration",
            duration_seconds=5.0,
            narration=narration,
            visual_description=visual_description,
            asset_query=asset_query,
            props=props or {},
        )

    def test_no_video_request_when_video_provider_empty(self) -> None:
        """Bug #49: ``video_provider=\"\"`` (default) must not emit a VIDEO request.

        This is the backward-compat behavior — existing call sites that
        don't know about video generation should produce exactly the
        same asset list as before.
        """
        scene = self._make_scene()
        requests = requests_from_scene(scene, job_id="job-1")
        asset_types = [r.asset_type for r in requests]
        assert AssetType.VIDEO not in asset_types
        # IMAGE is still emitted (the visual fallback).
        assert AssetType.IMAGE in asset_types
        assert AssetType.TTS in asset_types

    def test_video_request_appended_when_video_provider_set(self) -> None:
        """Bug #49 fix: a non-empty ``video_provider`` emits a VIDEO request when
        the scene requests real motion (``props.requires_motion=True``).

        The VIDEO request must come **before** the IMAGE request so
        the renderer's ``scene.assets.find(image|video|data_viz)``
        picks the video first. Both are still emitted — the IMAGE is
        a low-cost fallback if the video request fails.
        """
        scene = self._make_scene(props={"requires_motion": True})
        requests = requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        asset_types = [r.asset_type for r in requests]
        # Both VIDEO and IMAGE are present.
        assert AssetType.VIDEO in asset_types
        assert AssetType.IMAGE in asset_types
        # VIDEO must be appended BEFORE IMAGE in the returned list.
        assert asset_types.index(AssetType.VIDEO) < asset_types.index(AssetType.IMAGE)

    def test_no_video_request_without_motion_flag(self) -> None:
        """Without ``props.requires_motion`` only IMAGE is emitted even when a
        video provider is configured. This preserves the hybrid static/motion
        routing: still images get Ken Burns motion at render time."""
        scene = self._make_scene()
        requests = requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        asset_types = [r.asset_type for r in requests]
        assert AssetType.VIDEO not in asset_types
        assert AssetType.IMAGE in asset_types

    def test_video_request_has_correct_fields(self) -> None:
        """VIDEO request carries the right asset_id, scene_id, prompt, ext."""
        scene = self._make_scene(
            scene_id="my-scene",
            visual_description="A timelapse of clouds",
            props={"requires_motion": True},
        )
        requests = requests_from_scene(scene, job_id="job-42", video_provider="fal_ai")
        video_requests = [r for r in requests if r.asset_type == AssetType.VIDEO]
        assert len(video_requests) == 1
        video = video_requests[0]
        assert video.asset_id == "my-scene_video"
        assert video.scene_id == "my-scene"
        assert video.prompt == "A timelapse of clouds"
        assert video.output_ext == "mp4"
        assert video.provider == "fal_ai"
        assert video.job_id == "job-42"
        # Duration flows from the scene.
        assert video.params.get("duration_seconds") == 5.0

    def test_no_video_request_when_visual_description_empty(self) -> None:
        """A scene with no ``visual_description`` gets no VIDEO request.

        This matches the existing IMAGE behavior — without a
        description there's nothing to render.
        """
        scene = self._make_scene(visual_description="", narration="Just talking")
        requests = requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        asset_types = [r.asset_type for r in requests]
        assert AssetType.VIDEO not in asset_types
        assert AssetType.IMAGE not in asset_types
        # Narration is still emitted.
        assert AssetType.TTS in asset_types

    def test_video_request_uses_the_clamped_motion_length(self) -> None:
        """#257: generation length is clamped, not the raw scene duration.

        A 20 s scene cannot ask a 10 s model for a 20 s clip; the renderer
        loops the shortfall. A 1 s scene still requests the 4 s minimum.
        """
        scene = self._make_scene(props={"requires_motion": True})
        scene.duration_seconds = 20.0
        video = next(
            r
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
            if r.asset_type == AssetType.VIDEO
        )
        assert video.params["duration_seconds"] == 10

        scene.duration_seconds = 1.0
        video = next(
            r
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
            if r.asset_type == AssetType.VIDEO
        )
        assert video.params["duration_seconds"] == 4

    def test_motion_length_rounds_up_to_whole_seconds(self) -> None:
        """Providers bill and accept whole seconds; 5.2 s of speech is 6 s."""
        scene = self._make_scene(props={"requires_motion": True})
        scene.duration_seconds = 5.2
        video = next(
            r
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
            if r.asset_type == AssetType.VIDEO
        )
        assert video.params["duration_seconds"] == 6

    def test_storyteller_needs_motion_prop_also_requests_video(self) -> None:
        """#257: a storyteller plan says ``needs_motion``, not
        ``requires_motion``. Both names mean "generate a video" at the asset
        stage, so ``ve assets`` must honour either."""
        scene = self._make_scene(props={"needs_motion": True})
        asset_types = [
            r.asset_type
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        ]
        assert AssetType.VIDEO in asset_types

    def test_explicit_requires_motion_false_wins_over_needs_motion(self) -> None:
        """The intent flag is authoritative when both are present."""
        scene = self._make_scene(props={"requires_motion": False, "needs_motion": True})
        asset_types = [
            r.asset_type
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        ]
        assert AssetType.VIDEO not in asset_types

    def test_needs_motion_accepts_a_serialized_string(self) -> None:
        scene = self._make_scene(props={"needs_motion": "true"})
        asset_types = [
            r.asset_type
            for r in requests_from_scene(scene, job_id="job-1", video_provider="fal_ai")
        ]
        assert AssetType.VIDEO in asset_types


class TestCacheKeySorting:
    """Bug #97 regression: lists inside params must be sorted deterministically."""

    def test_cache_key_includes_asset_and_scene_id(self) -> None:
        """C17 regression: per-scene assets must not share a cache key just
        because their prompt is identical."""
        base = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="s1",
            asset_type=AssetType.SFX,
            prompt="explosion",
            provider="elevenlabs_sfx",
            params={"duration_seconds": 3.0},
            output_ext="mp3",
        )
        same_prompt_other_scene = base.model_copy(update={"asset_id": "a2", "scene_id": "s2"})
        assert base.cache_key() != same_prompt_other_scene.cache_key()

        # Identical request re-hashes to the same key.
        assert base.cache_key() == base.cache_key()

    def test_list_order_does_not_change_cache_key(self) -> None:
        req_a = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="s1",
            asset_type=AssetType.DATA_VIZ,
            prompt="chart",
            params={"data": {"labels": ["z", "a", "m"]}},
            output_ext="png",
        )
        req_b = req_a.model_copy(update={"params": {"data": {"labels": ["a", "z", "m"]}}})
        assert req_a.cache_key() == req_b.cache_key()

    def test_list_of_dicts_sorted_by_normalized_key(self) -> None:
        req_a = AssetRequest(
            asset_id="a1",
            job_id="job-1",
            scene_id="s1",
            asset_type=AssetType.DATA_VIZ,
            prompt="chart",
            params={"series": [{"name": "b", "value": 2}, {"name": "a", "value": 1}]},
            output_ext="png",
        )
        req_b = req_a.model_copy(
            update={"params": {"series": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]}}
        )
        assert req_a.cache_key() == req_b.cache_key()


class TestFalExtractDownloadUrl:
    """Bug #100 regression: FAL may return ``images`` as a list of URL strings."""

    def test_images_list_of_strings(self) -> None:
        from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

        url = FalAIProvider._extract_download_url(
            {"images": ["https://cdn.example.com/img1.png"]}, AssetType.IMAGE
        )
        assert url == "https://cdn.example.com/img1.png"

    def test_images_list_of_dicts_still_works(self) -> None:
        from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

        url = FalAIProvider._extract_download_url(
            {"images": [{"url": "https://cdn.example.com/img2.png"}]},
            AssetType.IMAGE,
        )
        assert url == "https://cdn.example.com/img2.png"

    def test_video_url_takes_priority_over_images(self) -> None:
        from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

        url = FalAIProvider._extract_download_url(
            {
                "video_url": "https://cdn.example.com/vid.mp4",
                "images": ["https://cdn.example.com/img.png"],
            },
            AssetType.VIDEO,
        )
        assert url == "https://cdn.example.com/vid.mp4"


class TestMediaContentSniffing:
    """Regression tests for shared audio-content validation."""

    def test_is_audio_content_detects_mp4_m4a_by_ftyp_offset(self) -> None:
        """C10 regression: real MP4/M4A files store ``ftyp`` at offset 4,
        so ``head.startswith(b'ftyp')`` rejects every valid file."""
        import httpx
        from video_editor_asset_ingestion.providers._media import _is_audio_content

        m4a_bytes = Path("demo/music.m4a").read_bytes()
        response = httpx.Response(200, content=m4a_bytes)
        # Clear the content-type so the sniff path is exercised.
        response.headers.pop("content-type", None)
        assert _is_audio_content(response) is True

    def test_is_audio_content_rejects_json_error_body(self) -> None:
        """A 200-OK JSON error page must not be treated as audio."""
        import httpx
        from video_editor_asset_ingestion.providers._media import _is_audio_content

        response = httpx.Response(
            200,
            content=b'{"error": "over capacity"}',
            headers={"content-type": "application/json"},
        )
        assert _is_audio_content(response) is False


class TestScoredProviderSelection:
    """Regression tests for scored provider routing."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        AssetIngestionManager._semaphores.clear()
        AssetIngestionManager._circuit_breakers.clear()
        ctx = JobContext(
            job_id="scored-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(
            providers={
                "stub_tts": ProviderConfig(),
                "openai_tts": ProviderConfig(),
            },
        )
        return AssetIngestionManager(ctx, config=config)

    @pytest.mark.asyncio
    async def test_manager_auto_provider_uses_scored_selector(
        self,
        manager: AssetIngestionManager,
    ) -> None:
        """provider='auto' resolves through the scored selector."""
        request = AssetRequest(
            asset_id="tts-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.TTS,
            prompt="Hello",
            provider="auto",
            params={"duration": 0.3},
            output_ext="mp3",
        )
        provider = manager._resolve_provider(request)
        assert provider.provider_name == "stub_tts"

    @pytest.mark.asyncio
    async def test_manager_explicit_provider_wins_over_auto_path(
        self,
        manager: AssetIngestionManager,
    ) -> None:
        """An explicit provider skips the scored selector."""
        request = AssetRequest(
            asset_id="tts-1",
            job_id=manager.ctx.job_id,
            scene_id="s1",
            asset_type=AssetType.TTS,
            prompt="Hello",
            provider="openai_tts",
            params={"duration": 0.3},
            output_ext="mp3",
        )
        provider = manager._resolve_provider(request)
        assert provider.provider_name == "openai_tts"


class TestAutoProviderModelRouting:
    """Part A: auto provider selection picks the cheapest provider for a model."""

    def _stub_registry(self, tmp_path: Path) -> Any:
        from video_editor_asset_ingestion import pricing as pricing_mod

        registry = pricing_mod.PricingRegistry()
        registry.last_updated = "2026-10-16"
        registry.providers = {
            "poyo_ai": {"models": {"gpt-image-2": {"cost_per_image": 0.5}}},
            "openrouter": {"models": {"openai/gpt-image-2": {"cost_per_image": 0.01}}},
        }
        registry.model_equivalents = {
            "gpt-image-2": {"poyo_ai": "gpt-image-2", "openrouter": "openai/gpt-image-2"}
        }
        return registry

    def test_auto_picks_cheapest_and_translates_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion import pricing as pricing_mod

        registry = self._stub_registry(tmp_path)
        monkeypatch.setattr(
            pricing_mod.PricingRegistry,
            "load",
            classmethod(lambda cls, path=None: registry),
        )
        monkeypatch.setenv("POYO_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")

        cfg = AssetProviderConfig(
            providers={
                "poyo_ai": ProviderConfig(api_key_env="POYO_API_KEY"),
                "openrouter": ProviderConfig(api_key_env="OPENROUTER_API_KEY"),
            }
        )
        ctx = JobContext(job_id="auto-job", work_root=tmp_path / "w", output_root=tmp_path / "o")
        mgr = AssetIngestionManager(ctx, config=cfg)
        req = AssetRequest(
            asset_id="img",
            job_id="auto-job",
            scene_id="s",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="auto",
            output_ext="png",
            params={"model": "gpt-image-2"},
        )
        name = mgr._resolve_auto_provider(req)
        # openrouter is cheaper here, and the canonical id is translated.
        assert name == "openrouter"
        assert req.params["model"] == "openai/gpt-image-2"


class TestImageChain:
    """Fallback chain: agy_image -> poyo_ai."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AssetIngestionManager:
        from video_editor_asset_ingestion.config import AssetProviderConfig

        ctx = JobContext(
            job_id="chain-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(
            image_chain=["agy_image", "poyo_ai"],
            providers={
                "agy_image": ProviderConfig(),
                "poyo_ai": ProviderConfig(),
            },
        )
        return AssetIngestionManager(ctx, config=config)

    @staticmethod
    def _install(
        monkeypatch: pytest.MonkeyPatch, manager: AssetIngestionManager, providers: dict
    ) -> None:
        def fake_resolve(request: AssetRequest, provider_name: str | None = None):
            name = provider_name or request.provider
            return providers[name]

        monkeypatch.setattr(manager, "_resolve_provider", fake_resolve)

    @pytest.mark.asyncio
    async def test_first_provider_success(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        class Never:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("should not run")

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Never()})
        request = AssetRequest(
            asset_id="img-1",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "agy_image"
        assert "_fallback_from" not in result[0].metadata
        # A clean chain success still records that the chain ran, so it is
        # distinguishable from a request pinned to a single provider.
        assert result[0].metadata["chain_attempted"] == ["agy_image"]

    @pytest.mark.asyncio
    async def test_first_failure_falls_through(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Broken:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("quota exhausted")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Broken(), "poyo_ai": Ok()})
        request = AssetRequest(
            asset_id="img-2",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "poyo_ai"
        assert result[0].metadata["_fallback_from"] == "agy_image"
        assert result[0].metadata["chain_attempted"] == ["agy_image", "poyo_ai"]

    @pytest.mark.asyncio
    async def test_open_breaker_is_skipped(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Never:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AssertionError("breaker should have skipped this provider")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Never(), "poyo_ai": Ok()})
        from video_editor_asset_ingestion.manager import ProviderCircuitBreaker

        breaker = ProviderCircuitBreaker("agy_image", failure_threshold=1, recovery_seconds=3600)
        breaker.record_failure()
        monkeypatch.setattr(
            manager,
            "_circuit_breaker",
            lambda name: breaker if name == "agy_image" else ProviderCircuitBreaker(name),
        )
        request = AssetRequest(
            asset_id="img-3",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        result = await manager.resolve([request])

        # A skipped provider never ran, so it must not be recorded as the
        # provider that failed and triggered the fallback.
        assert "_fallback_from" not in result[0].metadata
        assert result[0].metadata["chain_attempted"] == ["agy_image", "poyo_ai"]

    @pytest.mark.asyncio
    async def test_explicit_provider_bypasses_the_chain(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned provider means exactly that provider — no agy first."""
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        class Never:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("chain should have been bypassed")

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"agy_image": Never(), "poyo_ai": Ok()})
        request = AssetRequest(
            asset_id="img-4",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="poyo_ai",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "poyo_ai"
        assert "_fallback_from" not in result[0].metadata

    @pytest.mark.asyncio
    async def test_all_fail_reaches_the_placeholder(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The final attempt keeps the existing FAL->stub terminal path."""
        from video_editor_asset_ingestion.config import AssetProviderConfig
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        manager.config = AssetProviderConfig(
            image_chain=["agy_image", "fal_ai"],
            providers={
                "agy_image": ProviderConfig(),
                "fal_ai": ProviderConfig(),
            },
            strict=False,  # the placeholder path is the lenient mode (#281)
        )

        class Broken:
            def __init__(self, name: str) -> None:
                self.provider_name = name

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError(f"{self.provider_name} is down")

        self._install(
            monkeypatch,
            manager,
            {"agy_image": Broken("agy_image"), "fal_ai": Broken("fal_ai")},
        )
        request = AssetRequest(
            asset_id="img-5",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "stub_image"
        assert result[0].metadata["_fallback_from"] == "agy_image"
        assert result[0].metadata["chain_attempted"] == ["agy_image", "fal_ai"]

    @pytest.mark.asyncio
    async def test_auto_provider_still_resolves_through_the_scored_selector(
        self, tmp_path: Path, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A request-level "auto" must not be swallowed by the config default."""
        from video_editor_asset_ingestion.config import AssetProviderConfig

        manager.config = AssetProviderConfig(defaults={})
        selected: list[str] = []

        def fake_auto(request: AssetRequest) -> str:
            selected.append(request.asset_id)
            return "poyo_ai"

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"poyo_ai": Ok()})
        monkeypatch.setattr(manager, "_resolve_auto_provider", fake_auto)
        request = AssetRequest(
            asset_id="img-6",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="auto",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert selected == ["img-6"]
        assert result[0].metadata["provider"] == "poyo_ai"
        assert request.provider == "poyo_ai"

    @pytest.mark.asyncio
    async def test_cap_is_inert_without_a_configured_chain(
        self, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A job that never opted into the chain must not hit the image cap."""
        from video_editor_asset_ingestion.config import AssetProviderConfig

        manager.config = AssetProviderConfig(max_images_per_run=2)
        calls: list[str] = []

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                calls.append(request.asset_id)
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"pinned-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"p{i}",
                provider="poyo_ai",
                output_ext="png",
            )
            for i in range(30)
        ]

        results = await manager.resolve(requests)

        assert len(results) == 30
        assert len(calls) == 30
        assert manager._images_started == 0

    @pytest.mark.asyncio
    async def test_pinned_requests_bypass_the_chain_and_the_cap(
        self, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A request that pins a provider does not use the chain, so it is not capped."""
        manager.config.max_images_per_run = 2
        calls: list[str] = []

        class Ok:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                calls.append(request.asset_id)
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai"},
                )

        self._install(monkeypatch, manager, {"poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"pinned-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"p{i}",
                provider="poyo_ai",
                output_ext="png",
            )
            for i in range(5)
        ]

        results = await manager.resolve(requests)

        assert len(results) == 5
        assert len(calls) == 5
        assert manager._images_started == 0

    @pytest.mark.asyncio
    async def test_budget_errors_have_their_own_event_and_spare_the_breaker(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        from video_editor_asset_ingestion.budget import BudgetGovernor
        from video_editor_asset_ingestion.config import AssetProviderConfig

        caplog.set_level(logging.WARNING)
        manager.config = AssetProviderConfig(
            image_chain=["poyo_ai"], providers={"poyo_ai": ProviderConfig()}
        )
        # Legacy credits: one poyo_ai image costs 1.0, so a zero budget refuses.
        manager.budget = BudgetGovernor(limit=0.0)
        request = AssetRequest(
            asset_id="img-budget",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        with pytest.raises(RuntimeError):
            await manager.resolve([request])

        events = [record.getMessage() for record in caplog.records]
        assert any("provider_chain_budget_exceeded" in line for line in events)
        assert not any("provider_chain_failed" in line for line in events)
        # A healthy provider must not be punished for the budget.
        assert manager._circuit_breaker("poyo_ai").failure_count == 0

    @pytest.mark.asyncio
    async def test_configuration_errors_spare_the_breaker(
        self,
        manager: AssetIngestionManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        from video_editor_asset_ingestion.config import AssetProviderConfig

        caplog.set_level(logging.WARNING)
        # No providers configured: resolving the pinned provider is a config
        # error, not a provider failure.
        manager.config = AssetProviderConfig()
        request = AssetRequest(
            asset_id="img-config",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="poyo_ai",
            output_ext="png",
        )

        with pytest.raises(RuntimeError):
            await manager.resolve([request])

        events = [record.getMessage() for record in caplog.records]
        assert any("provider_chain_misconfigured" in line for line in events)
        assert manager._circuit_breaker("poyo_ai").failure_count == 0

    @pytest.mark.asyncio
    async def test_cache_write_failure_returns_the_paid_result(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A paid generation must not look like a provider failure."""
        import logging

        from video_editor_asset_ingestion.config import AssetProviderConfig

        caplog.set_level(logging.WARNING)
        manager.config = AssetProviderConfig(
            image_chain=["agy_image"], providers={"agy_image": ProviderConfig()}
        )

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image", "cost": 0.0},
                )

        self._install(monkeypatch, manager, {"agy_image": Ok()})

        def broken_write(result: AssetResult, meta_path: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(manager, "_save_cached", broken_write)
        request = AssetRequest(
            asset_id="img-cache",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert result[0].metadata["provider"] == "agy_image"
        assert manager._circuit_breaker("agy_image").failure_count == 0
        events = [record.getMessage() for record in caplog.records]
        assert any("asset_cache_write_failed" in line for line in events)

    @pytest.mark.asyncio
    async def test_image_cap_stops_the_run(
        self, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.manager import ImageQuotaError

        manager.config.max_images_per_run = 2
        calls: list[str] = []

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                calls.append(request.asset_id)
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"img-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"p{i}",
                output_ext="png",
            )
            for i in range(3)
        ]

        with pytest.raises(ImageQuotaError, match="cap reached"):
            await manager.resolve(requests)

        # Exactly ``cap`` generations reached the provider; the third attempt
        # was refused before any work happened.
        assert len(calls) == 2
        assert manager._images_started == 2

    @pytest.mark.asyncio
    async def test_image_cap_breach_is_logged(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An operator must be able to see the abort in the job log."""
        import logging

        from video_editor_asset_ingestion.manager import ImageQuotaError

        caplog.set_level(logging.ERROR)
        manager.config.max_images_per_run = 1

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"img-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"p{i}",
                output_ext="png",
            )
            for i in range(2)
        ]

        with pytest.raises(ImageQuotaError):
            await manager.resolve(requests)

        breach = [r for r in caplog.records if "image_quota_exceeded" in r.getMessage()]
        assert len(breach) == 1
        message = breach[0].getMessage()
        assert "'started': 1" in message
        assert "'limit': 1" in message

    @pytest.mark.asyncio
    async def test_local_media_copies_do_not_consume_the_image_cap(
        self, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cap counts generations; copying an existing file is not one."""
        from video_editor_asset_ingestion.config import AssetProviderConfig

        manager.config = AssetProviderConfig(
            image_chain=["local_media"],
            providers={"local_media": ProviderConfig()},
            max_images_per_run=1,
        )

        class Copy:
            provider_name = "local_media"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "local_media"},
                )

        self._install(monkeypatch, manager, {"local_media": Copy()})
        requests = [
            AssetRequest(
                asset_id=f"local-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"/tmp/local-{i}.png",
                output_ext="png",
            )
            for i in range(3)
        ]

        results = await manager.resolve(requests)

        assert len(results) == 3
        assert manager._images_started == 0

    @pytest.mark.parametrize("cap", [0, -1])
    @pytest.mark.asyncio
    async def test_non_positive_cap_means_no_cap(
        self,
        manager: AssetIngestionManager,
        monkeypatch: pytest.MonkeyPatch,
        cap: int,
    ) -> None:
        """``max_images_per_run`` has no lower bound; <= 0 must disable it."""
        from video_editor_asset_ingestion.config import AssetProviderConfig

        default_cap = AssetProviderConfig().max_images_per_run
        manager.config.max_images_per_run = cap

        class Ok:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "agy_image"},
                )

        self._install(monkeypatch, manager, {"agy_image": Ok(), "poyo_ai": Ok()})
        requests = [
            AssetRequest(
                asset_id=f"img-{i}",
                job_id="chain-job",
                scene_id=f"s{i}",
                asset_type=AssetType.IMAGE,
                prompt=f"p{i}",
                output_ext="png",
            )
            for i in range(default_cap + 1)
        ]

        results = await manager.resolve(requests)

        assert len(results) == default_cap + 1
        assert manager._images_started == default_cap + 1

    def test_agy_image_round_trips_two_at_a_time(self) -> None:
        """Agent round-trips are expensive, so agy_image gets a tighter limit."""
        from video_editor_asset_ingestion.manager import _CLOUD_PROVIDER_LIMITS

        assert _CLOUD_PROVIDER_LIMITS["agy_image"] == 2

    @pytest.mark.asyncio
    async def test_auto_request_walks_the_chain_despite_a_concrete_default(
        self, manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """image_chain outranks both a request-level "auto" and a config default."""
        from video_editor_asset_ingestion.config import AssetProviderConfig

        manager.config = AssetProviderConfig(
            image_chain=["agy_image", "poyo_ai"],
            providers={
                "agy_image": ProviderConfig(),
                "poyo_ai": ProviderConfig(),
            },
            defaults={AssetType.IMAGE.value: "poyo_ai"},
        )
        selected: list[str] = []

        def fake_auto(request: AssetRequest) -> str:
            selected.append(request.asset_id)
            return "poyo_ai"

        class Ok:
            def __init__(self, name: str) -> None:
                self.provider_name = name

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": self.provider_name},
                )

        self._install(
            monkeypatch,
            manager,
            {"agy_image": Ok("agy_image"), "poyo_ai": Ok("poyo_ai")},
        )
        monkeypatch.setattr(manager, "_resolve_auto_provider", fake_auto)
        request = AssetRequest(
            asset_id="img-7",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="auto",
            output_ext="png",
        )

        result = await manager.resolve([request])

        assert selected == []
        assert result[0].metadata["provider"] == "agy_image"
        assert "_fallback_from" not in result[0].metadata


class TestAutoImageSelection:
    """``auto`` stills must generate, not fetch stock photography."""

    @staticmethod
    def _manager(tmp_path: Path, providers: dict[str, ProviderConfig]) -> AssetIngestionManager:
        ctx = JobContext(
            job_id="auto-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "out",
        )
        return AssetIngestionManager(ctx, config=AssetProviderConfig(providers=providers))

    @staticmethod
    def _request() -> AssetRequest:
        return AssetRequest(
            asset_id="auto-1",
            job_id="auto-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="auto",
            output_ext="png",
        )

    def test_auto_image_without_a_chain_picks_a_generative_provider(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.providers.base import GenerativeProvider
        from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY

        manager = self._manager(
            tmp_path,
            {"pexels": ProviderConfig(), "poyo_ai": ProviderConfig()},
        )
        # Without the filter the scored selector prefers the free stock library.
        assert manager.config.select_provider("image")[0] == "pexels"

        selected = manager._select_auto_provider(self._request())

        assert selected == "poyo_ai"
        assert issubclass(PROVIDER_REGISTRY[selected], GenerativeProvider)

    def test_auto_image_falls_back_when_nothing_generative_is_configured(
        self, tmp_path: Path
    ) -> None:
        manager = self._manager(
            tmp_path,
            {"pexels": ProviderConfig(), "stub_image": ProviderConfig()},
        )

        # The filtered set is empty, so the existing scored selection stands:
        # a placeholder is not ideal, but it is better than refusing a still.
        # ``stub_image`` wins on the score card (free, fast, reliable).
        selected = manager._select_auto_provider(self._request())

        assert selected == "stub_image"

    def test_auto_image_warns_when_only_stock_media_is_available(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = self._manager(tmp_path, {"pexels": ProviderConfig()})

        with caplog.at_level(logging.WARNING):
            selected = manager._select_auto_provider(self._request())

        assert selected == "pexels"
        events = [r.getMessage() for r in caplog.records]
        assert any("provider_auto_stock_fallback" in e for e in events)


class TestCostLineProvenance:
    """Design §5.2: a fallback cost line must name the provider that failed."""

    @staticmethod
    def _manager_with_ledger(
        tmp_path: Path,
        *,
        image_chain: list[str],
        providers: dict[str, ProviderConfig],
    ) -> tuple[AssetIngestionManager, Path]:
        from video_editor_state.ledger import ProjectLedger

        db_path = tmp_path / "ledger.db"
        ledger = ProjectLedger(db_path)
        ledger.init_db()
        ctx = JobContext(
            job_id="chain-job",
            work_root=tmp_path / "work",
            output_root=tmp_path / "out",
            ledger=ledger,
        )
        config = AssetProviderConfig(image_chain=image_chain, providers=providers)
        return AssetIngestionManager(ctx, config=config), db_path

    @staticmethod
    def _rows(db_path: Path) -> list[tuple]:
        import sqlite3

        with sqlite3.connect(db_path) as conn:
            return conn.execute(
                "SELECT provider, actual_cost, status, metadata FROM cost_lines"
            ).fetchall()

    @pytest.mark.asyncio
    async def test_fallback_cost_line_names_the_failed_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import json

        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        manager, db_path = self._manager_with_ledger(
            tmp_path,
            image_chain=["agy_image", "poyo_ai"],
            providers={"agy_image": ProviderConfig(), "poyo_ai": ProviderConfig()},
        )

        class Broken:
            provider_name = "agy_image"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("quota exhausted")

        class Paid:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai", "cost": 7.0},
                )

        TestImageChain._install(monkeypatch, manager, {"agy_image": Broken(), "poyo_ai": Paid()})
        request = AssetRequest(
            asset_id="img-cost",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            output_ext="png",
        )

        await manager.resolve([request])

        rows = self._rows(db_path)
        assert len(rows) == 1
        provider, actual, status, metadata = rows[0]
        assert provider == "poyo_ai"
        assert actual == 7.0
        assert status == "success"
        assert json.loads(metadata)["fallback_from"] == "agy_image"
        assert json.loads(metadata)["chain_attempted"] == ["agy_image", "poyo_ai"]

    @pytest.mark.asyncio
    async def test_non_chain_success_writes_one_row_and_failure_writes_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError

        manager, db_path = self._manager_with_ledger(
            tmp_path, image_chain=[], providers={"poyo_ai": ProviderConfig()}
        )

        class Paid:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                output_path.write_bytes(PNG_1X1)
                return AssetResult(
                    asset_id=request.asset_id,
                    asset_type=AssetType.IMAGE,
                    local_path=str(output_path),
                    scene_id=request.scene_id,
                    metadata={"provider": "poyo_ai", "cost": 1.5},
                )

        TestImageChain._install(monkeypatch, manager, {"poyo_ai": Paid()})
        ok = AssetRequest(
            asset_id="ok",
            job_id="chain-job",
            scene_id="s1",
            asset_type=AssetType.IMAGE,
            prompt="x",
            provider="poyo_ai",
            output_ext="png",
        )

        await manager.resolve([ok])

        rows = self._rows(db_path)
        assert len(rows) == 1
        assert rows[0][0] == "poyo_ai"
        assert rows[0][1] == 1.5

        class Broken:
            provider_name = "poyo_ai"

            async def generate(self, request: AssetRequest, output_path: Path):
                raise AGYImageError("down")

        TestImageChain._install(monkeypatch, manager, {"poyo_ai": Broken()})
        bad = AssetRequest(
            asset_id="bad",
            job_id="chain-job",
            scene_id="s2",
            asset_type=AssetType.IMAGE,
            prompt="y",
            provider="poyo_ai",
            output_ext="png",
        )

        with pytest.raises(RuntimeError):
            await manager.resolve([bad])

        assert len(self._rows(db_path)) == 1
