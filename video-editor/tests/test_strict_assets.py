"""#281: ``assets.strict`` refuses to replace a missing or failed provider with a stub."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import AssetProviderConfig, ProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_state.job import JobContext


def _manager(tmp_path: Path, config: AssetProviderConfig) -> AssetIngestionManager:
    ctx = JobContext(job_id="strict-job", work_root=tmp_path / "w", output_root=tmp_path / "o")
    ctx.ensure_dirs()
    return AssetIngestionManager(ctx, config=config)


def _request(provider: str = "auto", asset_type: AssetType = AssetType.IMAGE) -> AssetRequest:
    return AssetRequest(
        asset_id="a1",
        job_id="strict-job",
        scene_id="s1",
        asset_type=asset_type,
        prompt="a red circle",
        provider=provider,
        output_ext="png",
    )


def test_strict_is_the_default() -> None:
    assert AssetProviderConfig().strict is True


@pytest.mark.asyncio
async def test_auto_with_no_provider_fails_instead_of_a_stub(tmp_path: Path) -> None:
    manager = _manager(tmp_path, AssetProviderConfig())

    batch = await manager.resolve_partial([_request()])

    assert batch.results == []
    [(asset_id, error)] = batch.failures
    assert asset_id == "a1"
    assert "StubFallbackRefusedError" in error and "assets.strict" in error


@pytest.mark.asyncio
async def test_lenient_mode_still_serves_the_placeholder(tmp_path: Path) -> None:
    manager = _manager(tmp_path, AssetProviderConfig(strict=False))

    results = await manager.resolve([_request()])

    assert results[0].metadata["provider"] == "stub_image"


@pytest.mark.asyncio
async def test_an_explicitly_named_stub_is_still_served(tmp_path: Path) -> None:
    manager = _manager(tmp_path, AssetProviderConfig())

    results = await manager.resolve([_request(provider="stub_image")])

    assert results[0].metadata["provider"] == "stub_image"


def test_cached_stub_assets_are_visible_to_qa(tmp_path: Path) -> None:
    from video_editor_state.asset_audit import stub_asset_ids

    work = tmp_path / "w"
    cache = work / ".cache" / "assets"
    cache.mkdir(parents=True)
    (cache / "a.meta.json").write_text(
        json.dumps({"asset_id": "still_rat", "metadata": {"provider": "stub_image"}}),
        encoding="utf-8",
    )
    (cache / "b.meta.json").write_text(
        json.dumps({"asset_id": "voice", "metadata": {"provider": "poyo_ai"}}),
        encoding="utf-8",
    )

    assert stub_asset_ids(work) == ["still_rat"]
    assert stub_asset_ids(tmp_path / "no-cache") == []


def test_used_paths_hide_a_stub_from_an_earlier_draft(tmp_path: Path) -> None:
    """A reused job id must report only the files this build consumed.

    The cache is content-keyed, so a new draft writes a second entry for the
    same asset id and the old stub stays on disk. Without ``used_paths`` QA
    reports a stub the current build never touched.
    """
    from video_editor_state.asset_audit import stub_asset_ids

    work = tmp_path / "w"
    cache = work / ".cache" / "assets"
    cache.mkdir(parents=True)
    old_stub = cache / "old1.png"
    old_stub.write_bytes(b"")
    (cache / "old1.meta.json").write_text(
        json.dumps({"asset_id": "scene_1_image", "metadata": {"provider": "stub_image"}}),
        encoding="utf-8",
    )
    new_still = cache / "new1.png"
    new_still.write_bytes(b"")
    (cache / "new1.meta.json").write_text(
        json.dumps({"asset_id": "scene_1_image", "metadata": {"provider": "poyo_ai"}}),
        encoding="utf-8",
    )

    # The whole cache still holds the old stub …
    assert stub_asset_ids(work) == ["scene_1_image"]
    # … but the build that used the paid file is clean.
    assert stub_asset_ids(work, used_paths=[new_still]) == []
    # And a build that did use the stub still reports it.
    assert stub_asset_ids(work, used_paths=[old_stub]) == ["scene_1_image"]


def test_used_asset_paths_reads_either_result_shape(tmp_path: Path) -> None:
    """The audit takes real ``AssetResult``s and lighter stand-ins alike."""
    from types import SimpleNamespace

    from video_editor_state.asset_audit import used_asset_paths

    real = SimpleNamespace(local_path=tmp_path / "real.png")
    stand_in = SimpleNamespace(resolved_path=lambda: tmp_path / "stand-in.wav")
    empty = SimpleNamespace()
    assert used_asset_paths([real, stand_in, empty]) == [
        str(tmp_path / "real.png"),
        str(tmp_path / "stand-in.wav"),
    ]


@pytest.mark.asyncio
async def test_a_failed_terminal_fal_attempt_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider

    async def down(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("FAL is down")

    monkeypatch.setattr(FalAIProvider, "generate", down)
    manager = _manager(tmp_path, AssetProviderConfig(providers={"fal_ai": ProviderConfig()}))

    with pytest.raises(Exception, match="FAL is down"):
        await manager.resolve([_request(provider="fal_ai")])
    assert not list((tmp_path / "w").rglob("*.png"))
