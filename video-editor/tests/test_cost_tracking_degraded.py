"""#283: spend-tracking failures are recorded, not only logged."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_state.asset_audit import cost_tracking_degraded_reasons
from video_editor_state.job import JobContext
from video_editor_state.ledger import ProjectLedger


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("cost-job")
    context = JobContext(
        job_id="cost-job",
        work_root=tmp_path / "w",
        output_root=tmp_path / "o",
        ledger=ledger,
    )
    context.ensure_dirs()
    return context


@pytest.fixture
def manager(ctx: JobContext) -> AssetIngestionManager:
    return AssetIngestionManager(ctx, config=AssetProviderConfig())


def _request() -> AssetRequest:
    return AssetRequest(
        asset_id="a1",
        job_id="cost-job",
        scene_id="s1",
        asset_type=AssetType.IMAGE,
        prompt="a red circle",
        provider="poyo_ai",
        output_ext="png",
    )


def _result() -> AssetResult:
    return AssetResult(
        asset_id="a1",
        asset_type=AssetType.IMAGE,
        local_path="/tmp/a1.png",
        scene_id="s1",
        metadata={"provider": "poyo_ai", "cost": 0.01},
    )


def test_a_pricing_failure_marks_the_manager_and_persists_the_reason(
    manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    import video_editor_asset_ingestion.manager as manager_module

    class BrokenRegistry:
        @classmethod
        def load(cls) -> Any:
            raise RuntimeError("pricing.yaml is broken")

    monkeypatch.setattr(manager_module, "PricingRegistry", BrokenRegistry)
    manager._pricing_registry = None

    assert manager._request_cost(_request(), "poyo_ai") is None

    assert manager.cost_tracking_degraded is True
    assert manager.cost_tracking_reasons
    assert cost_tracking_degraded_reasons(manager.ctx.work_dir) == manager.cost_tracking_reasons
    project = manager.ctx.ledger.get_project("cost-job")
    assert project is not None
    assert any(issue["code"] == "COST_TRACKING_DEGRADED" for issue in project["issues"])


def test_a_ledger_write_failure_marks_degraded(
    manager: AssetIngestionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    import video_editor_asset_ingestion.manager as manager_module

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("ledger is read-only")

    monkeypatch.setattr(manager_module, "record_cost_line", boom)

    manager._record_cost_line(_request(), _result(), None)

    assert manager.cost_tracking_degraded is True
    assert "ledger cost line" in manager.cost_tracking_reasons[0]


def test_a_budget_snapshot_failure_marks_degraded(
    manager: AssetIngestionManager,
) -> None:
    class BrokenBudget:
        def save(self, _path: Path) -> None:
            raise OSError("disk full")

    manager.budget = BrokenBudget()  # type: ignore[assignment]

    manager._persist_budget_snapshot()

    assert manager.cost_tracking_degraded is True
    assert "budget snapshot" in manager.cost_tracking_reasons[0]


def test_cost_report_lists_degraded_projects(ctx: JobContext) -> None:
    ctx.ledger.record_issue(
        ctx.job_id,
        severity="medium",
        category="cost",
        code="COST_TRACKING_DEGRADED",
        description="pricing failed",
    )

    assert ctx.ledger.degraded_cost_projects() == ["cost-job"]
