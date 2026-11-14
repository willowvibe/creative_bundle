"""#292: agy preflight, failure classification and the long breaker window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
from video_editor_asset_ingestion.manager import (
    AssetIngestionManager,
    CircuitBreakerOpenError,
    ProviderCircuitBreaker,
)
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.visual.agy_image import AGYImageError
from video_editor_state.job import JobContext

PNG = b"\x89PNG\r\n\x1a\n"


def _manager(tmp_path: Path, *, final: bool = False) -> AssetIngestionManager:
    ctx = JobContext(job_id="agy-job", work_root=tmp_path / "w", output_root=tmp_path / "o")
    ctx.ensure_dirs()
    config = AssetProviderConfig(
        image_chain=["agy_image", "poyo_ai"],
        image_chain_final=["poyo_ai", "openrouter", "fal_ai"],
        providers={
            "agy_image": ProviderConfig(),
            "poyo_ai": ProviderConfig(),
            "openrouter": ProviderConfig(),
            "fal_ai": ProviderConfig(),
        },
    )
    return AssetIngestionManager(ctx, config=config, final=final)


def _request() -> AssetRequest:
    return AssetRequest(
        asset_id="a1",
        job_id="agy-job",
        scene_id="s1",
        asset_type=AssetType.IMAGE,
        prompt="a red circle",
        provider="auto",
        output_ext="png",
    )


def _result(provider: str, request: AssetRequest) -> AssetResult:
    return AssetResult(
        asset_id=request.asset_id,
        asset_type=AssetType.IMAGE,
        local_path="/tmp/a1.png",
        scene_id=request.scene_id,
        metadata={"provider": provider, "cost": 0.0},
    )


@pytest.mark.asyncio
async def test_a_failed_preflight_drops_agy_for_the_whole_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    calls: list[str] = []

    class BrokenAgy:
        provider_name = "agy_image"

        async def preflight(self) -> None:
            calls.append("preflight")
            raise AGYImageError("quota exhausted", kind="quota")

        async def generate(self, request: AssetRequest, output_path: Path) -> Any:
            calls.append("agy-generate")
            raise AssertionError("agy must not run after a failed preflight")

    class Poyo:
        provider_name = "poyo_ai"

        async def generate(self, request: AssetRequest, output_path: Path) -> AssetResult:
            calls.append("poyo")
            output_path.write_bytes(PNG)
            return _result("poyo_ai", request)

    providers = {"agy_image": BrokenAgy(), "poyo_ai": Poyo()}
    monkeypatch.setattr(manager, "_resolve_provider", lambda request, name=None: providers[name])
    monkeypatch.setattr(
        manager,
        "_request_cost",
        lambda request, provider_name: 0.0,
    )

    [result] = await manager.resolve([_request()])

    assert result.metadata["provider"] == "poyo_ai"
    assert calls == ["preflight", "poyo"]
    # The second request must not re-run the failed preflight.
    second = _request()
    second.asset_id = "a2"
    [result] = await manager.resolve([second])
    assert result.metadata["provider"] == "poyo_ai"
    assert calls == ["preflight", "poyo", "poyo"]


def test_agy_gets_a_thirty_minute_recovery_window(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    assert manager._circuit_breaker("agy_image").recovery_seconds == 1800.0
    assert manager._circuit_breaker("poyo_ai").recovery_seconds == 60.0


def test_quota_and_auth_failures_trip_the_breaker_immediately() -> None:
    breaker = ProviderCircuitBreaker("agy_image", failure_threshold=3)

    breaker.record_failure(immediate=True)

    with pytest.raises(CircuitBreakerOpenError):
        breaker.check()


@pytest.mark.asyncio
async def test_a_pinned_agy_request_is_still_dropped_by_a_failed_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller naming agy gets a clear failure, not a ten-minute timeout."""
    manager = _manager(tmp_path)

    class BrokenAgy:
        provider_name = "agy_image"

        async def preflight(self) -> None:
            raise AGYImageError("not authenticated", kind="auth")

    monkeypatch.setattr(manager, "_resolve_provider", lambda request, name=None: BrokenAgy())
    request = _request()
    request.provider = "agy_image"

    with pytest.raises(RuntimeError, match="No provider configured"):
        await manager.resolve([request])
