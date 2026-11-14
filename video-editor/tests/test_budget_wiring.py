"""Phase D tests: budget governor and scored provider wiring."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner
from video_editor_asset_ingestion.budget import BudgetGovernor
from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetType
from video_editor_core.models.scene_plan import Scene, ScenePlan
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_scene_plan(tmp_path: Path) -> Path:
    """Write a minimal scene plan with one narrated scene."""
    plan = ScenePlan(
        job_id="budget-job",
        script="Test script",
        scenes=[
            Scene(
                id="scene_1",
                type="narration",
                duration_seconds=3.0,
                narration="Hello world",
                visual_description="A friendly greeting card",
            )
        ],
    )
    plan_path = tmp_path / "work" / "budget-job" / "plan" / "scene_plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return plan_path


def _make_stub_only_config(tmp_path: Path) -> Path:
    """Write a config.yaml that only exposes stub/local providers."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
assets:
  providers:
    stub_tts: {}
    stub_image: {}
  defaults:
    tts: stub_tts
    image: stub_image
""",
        encoding="utf-8",
    )
    return config_path


def _make_video(path: Path, duration: float = 3.0) -> None:
    """Create a minimal valid MP4 at ``path``."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clear_asset_manager_class_state() -> None:
    """Isolate class-level semaphores and circuit breakers between tests."""
    AssetIngestionManager._semaphores.clear()
    AssetIngestionManager._circuit_breakers.clear()


class TestBudgetGovernor:
    """Unit tests for the budget governor serialization added in Phase D."""

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        governor = BudgetGovernor(limit=50.0)
        estimate = governor.allocate("stub_tts", "tts")
        governor.record_actual(estimate, 0.0)

        path = tmp_path / "budget.json"
        governor.save(path)
        restored = BudgetGovernor.load(path)

        assert restored.limit == 50.0
        assert restored.spent == 0.0
        assert len(restored.ledger()) == 1
        assert restored.ledger()[0].provider == "stub_tts"

    def test_rejects_over_budget_request(self) -> None:
        from video_editor_asset_ingestion.budget import BudgetExceededError

        governor = BudgetGovernor(limit=1.0)
        # elevenlabs_tts default estimate is 18.0 credits.
        with pytest.raises(BudgetExceededError):
            governor.allocate("elevenlabs_tts", "tts")

    def test_cost_lines_carry_the_job_id_and_the_registry_price(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#273: without a provider-reported cost the row must still be priced.

        The short path passed no governor, so every paid asset produced a
        ``cost_lines`` row with NULL cost and ``ve cost-report`` attributed
        none of the build's spend. A governor with a registry supplies the
        USD estimate, and a cache hit records zero rather than NULL.
        """
        from video_editor_asset_ingestion import manager as manager_module
        from video_editor_asset_ingestion.models import AssetResult

        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            manager_module,
            "record_cost_line",
            lambda ctx, **kwargs: captured.update(kwargs),
        )
        ctx = JobContext(job_id="priced", work_root=tmp_path / "work", output_root=tmp_path / "out")
        manager = AssetIngestionManager(ctx, config=AssetProviderConfig())
        request = AssetRequest(
            asset_id="still_rat",
            job_id="priced",
            scene_id="hook",
            asset_type=AssetType.IMAGE,
            prompt="a rat",
            provider="poyo_ai",
            params={"model": "gpt-image-2.5-fixed", "resolution": "2K"},
            output_ext="png",
        )
        result = AssetResult(
            asset_id="still_rat",
            asset_type=AssetType.IMAGE,
            local_path=str(tmp_path / "rat.png"),
        )

        manager._record_cost_line(request, result, None)

        assert captured["job_id"] == "priced"
        assert captured["estimated_cost"] == pytest.approx(0.03)
        assert captured["actual_cost"] == pytest.approx(0.03)

        manager._record_cost_line(request, result, None, status="cached")
        assert captured["estimated_cost"] == 0.0
        assert captured["actual_cost"] == 0.0

    def test_a_failed_attempt_releases_its_reservation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#264: a provider failure must not leave its price in ``spent``.

        The reservation is taken before the call; only ``record_actual`` used
        to settle it, so a failure kept the money in the running total and in
        the persisted budget. The manager now releases it.
        """
        from video_editor_asset_ingestion.pricing import PricingRegistry

        ctx = JobContext(job_id="failed", work_root=tmp_path / "work", output_root=tmp_path / "out")
        governor = BudgetGovernor(registry=PricingRegistry.load())
        manager = AssetIngestionManager(ctx, config=AssetProviderConfig(), budget=governor)
        request = AssetRequest(
            asset_id="still_rat",
            job_id="failed",
            scene_id="hook",
            asset_type=AssetType.IMAGE,
            prompt="a rat",
            provider="poyo_ai",
            params={"model": "gpt-image-2.5-fixed", "resolution": "2K"},
            output_ext="png",
        )

        async def _fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(manager, "_generate_one", _fail)
        with pytest.raises(RuntimeError, match="provider exploded"):
            asyncio.run(manager.resolve([request]))

        assert governor.spent == 0.0


class TestAutoProviderFallback:
    """Ensure 'auto' provider selection degrades safely without config."""

    def test_auto_fallback_to_stub_without_config(self, tmp_path: Path) -> None:
        ctx = JobContext(
            job_id="auto-fallback",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        manager = AssetIngestionManager(ctx, config=AssetProviderConfig(strict=False))
        request = AssetRequest(
            asset_id="a1",
            job_id="auto-fallback",
            scene_id="scene_1",
            asset_type=AssetType.TTS,
            prompt="Hello",
            provider="auto",
            output_ext="mp3",
        )
        provider = manager._resolve_provider(request)
        assert provider.provider_name == "stub_tts"

    def test_auto_uses_scored_selection_with_config(self, tmp_path: Path) -> None:
        from video_editor_asset_ingestion.config import ProviderConfig

        ctx = JobContext(
            job_id="auto-scored",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        config = AssetProviderConfig(
            providers={
                "openai_tts": ProviderConfig(),
                "stub_tts": ProviderConfig(),
            },
            defaults={},
        )
        manager = AssetIngestionManager(ctx, config=config)
        request = AssetRequest(
            asset_id="a1",
            job_id="auto-scored",
            scene_id="scene_1",
            asset_type=AssetType.TTS,
            prompt="Hello",
            provider="auto",
            output_ext="mp3",
        )
        provider = manager._resolve_provider(request)
        # Without API keys, scoring picks stub_tts (lower cost).
        assert provider.provider_name == "stub_tts"


class TestAssetsBudgetCLI:
    """End-to-end tests for ``ve assets --budget``."""

    def test_assets_command_with_budget_persists_ledger(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        _make_scene_plan(tmp_path)
        config_path = _make_stub_only_config(tmp_path)
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "assets",
                "budget-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--budget",
                "10.0",
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 0, result.output

        budget_path = tmp_path / "work" / "budget-job" / "budget" / "budget.json"
        assert budget_path.exists()
        data = json.loads(budget_path.read_text(encoding="utf-8"))
        assert data["limit"] == 10.0
        assert data["estimate_count"] >= 1

    def test_assets_command_auto_provider_fallback(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        _make_scene_plan(tmp_path)
        config_path = _make_stub_only_config(tmp_path)
        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "assets",
                "budget-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--budget",
                "10.0",
                "--config",
                str(config_path),
            ],
        )
        assert result.exit_code == 0, result.output


class TestExportBudgetSection:
    """Ensure the export report surfaces the budget ledger."""

    def test_export_report_includes_budget_section(
        self,
        cli_runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        work_dir = tmp_path / "work" / "budget-export-job"
        output_dir = tmp_path / "output" / "budget-export-job"
        work_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        # Seed a budget ledger.
        governor = BudgetGovernor(limit=25.0)
        estimate = governor.allocate("stub_tts", "tts", metadata={"asset_id": "scene_1_tts"})
        governor.record_actual(estimate, 0.0)
        governor.save(work_dir / "budget" / "budget.json")

        # Export needs a final video candidate.
        _make_video(work_dir / "music" / "with_music.mp4")

        from video_editor_cli.main import app

        result = cli_runner.invoke(
            app,
            [
                "export",
                "budget-export-job",
                "--work-root",
                str(tmp_path / "work"),
                "--output-root",
                str(tmp_path / "output"),
                "--skip-review",
            ],
        )
        assert result.exit_code == 0, result.output

        report_path = output_dir / "report.md"
        assert report_path.exists()
        report = report_path.read_text(encoding="utf-8")
        assert "## Budget / Spend" in report
        assert "25.00" in report or "unlimited" in report
        assert "stub_tts" in report


class TestWorkerRunPayload:
    """Verify the queued run payload carries the budget through to the pipeline."""

    def test_run_payload_serializes_budget(self) -> None:
        from video_editor_cli.main import _RunPayload

        payload = _RunPayload(
            source="/work/test.mp4",
            budget=42.0,
        )
        assert payload.budget == 42.0
        dumped = payload.model_dump(mode="json")
        assert dumped["budget"] == 42.0

    def test_worker_handle_run_passes_budget_to_skills_pipeline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Smoke-test that ``_handle_run`` forwards budget to ``SkillsPipeline``."""
        from video_editor_cli.main import _handle_run
        from video_editor_state.job import JobContext

        work_root = tmp_path / "work"
        output_root = tmp_path / "output"
        ctx = JobContext(
            job_id="worker-budget",
            work_root=work_root,
            output_root=output_root,
        )

        captured: dict[str, Any] = {}

        async def _fake_run_async(
            self: Any,
            source_path: Path,
            **kwargs: Any,
        ) -> dict[str, Any]:
            captured["budget"] = self.budget
            return {"final": str(output_root / "worker-budget" / "final.mp4")}

        monkeypatch.setattr(
            "video_editor_pipelines.pipeline.SkillsPipeline.run_async",
            _fake_run_async,
        )

        source_video = ctx.work_dir / "source.mp4"
        _make_video(source_video)

        asyncio = __import__("asyncio")
        asyncio.run(
            _handle_run(
                ctx,
                {
                    "source": str(source_video),
                    "budget": 7.0,
                    "work_root": str(work_root),
                    "output_root": str(output_root),
                },
            )
        )

        assert captured["budget"] is not None
        assert captured["budget"].limit == 7.0
