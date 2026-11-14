"""CLI integration tests for the project ledger."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner
from video_editor_cli.main import app
from video_editor_state.ledger import ProjectLedger


def _write_config(tmp_path: Path, ledger_path: Path) -> Path:
    cfg = {
        "ledger": {
            "enabled": True,
            "path": str(ledger_path),
        },
        "assets": {
            "providers": {},
            "defaults": {
                "tts": "stub_tts",
                "image": "stub_image",
                "video": "stub_hyperframes_video",
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return config_path


def _make_plan(tmp_path: Path, job_id: str) -> Path:
    job_dir = tmp_path / job_id
    plan_dir = job_dir / "plan"
    plan_dir.mkdir(parents=True)
    plan = {
        "job_id": job_id,
        "script": "Ledger CLI test script.",
        "scenes": [
            {
                "id": "scene_1",
                "type": "narration",
                "duration_seconds": 3.0,
                "narration": "Ledger test narration.",
                "visual_description": "A simple test image.",
                "asset_query": "",
                "props": {},
            }
        ],
    }
    plan_path = plan_dir / "scene_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path


def test_project_init_creates_db(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    runner = CliRunner()
    result = runner.invoke(app, ["project-init", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert ledger_path.exists()
    assert "Ledger ready" in result.output


def test_project_list_and_status(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    ledger = ProjectLedger(ledger_path)
    ledger.init_db()
    ledger.upsert_project("proj_a", status="complete", template="short_explainer")
    ledger.upsert_project("proj_b", status="dropped")

    runner = CliRunner()
    list_result = runner.invoke(
        app, ["project-list", "--config", str(config_path), "--status", "complete"]
    )
    assert list_result.exit_code == 0, list_result.output
    assert "proj_a" in list_result.output
    assert "proj_b" not in list_result.output

    status_result = runner.invoke(app, ["project-status", "proj_a", "--config", str(config_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "short_explainer" in status_result.output
    assert "complete" in status_result.output


def test_assets_command_records_cost_lines(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    _make_plan(tmp_path, "ledger_assets")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "assets",
            "ledger_assets",
            "--work-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path),
            "--config",
            str(config_path),
            "--tts-provider",
            "stub_tts",
            "--image-provider",
            "stub_image",
        ],
    )
    assert result.exit_code == 0, result.output

    ledger = ProjectLedger(ledger_path)
    project = ledger.get_project("ledger_assets")
    assert project is not None
    assert project["status"] == "planned"  # assets does not mark pipeline status
    assert len(project["cost_lines"]) >= 2
    providers = {c["provider"] for c in project["cost_lines"]}
    assert providers <= {"stub_tts", "stub_image"}


def test_cost_report_cli(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    ledger = ProjectLedger(ledger_path)
    ledger.init_db()
    ledger.upsert_project("p1", template="short_explainer")
    ledger.upsert_project("p2", template="short_explainer")
    for _ in range(2):
        ledger.record_cost(
            "p1",
            provider="stub_image",
            task_type="image",
            estimated_cost=0.1,
            actual_cost=0.1,
        )
    ledger.record_cost(
        "p2",
        provider="stub_image",
        task_type="image",
        estimated_cost=0.1,
        actual_cost=0.1,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["cost-report", "--config", str(config_path), "--provider", "stub_image"],
    )
    assert result.exit_code == 0, result.output
    assert "stub_image" in result.output
    assert "3" in result.output


def test_cost_report_cli_warns_about_unpriced_calls(tmp_path: Path) -> None:
    """#283: totals with a NULL-cost row undercount; the CLI must say so."""
    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    ledger = ProjectLedger(ledger_path)
    ledger.init_db()
    ledger.record_cost("p1", provider="poyo_ai", task_type="image", actual_cost=0.03)
    ledger.record_cost("p1", provider="poyo_ai", task_type="image", actual_cost=None)

    result = CliRunner().invoke(app, ["cost-report", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "unpriced" in result.output
    assert "1 call(s) have no recorded price" in result.output


def test_cost_report_cli_warns_about_degraded_cost_tracking(tmp_path: Path) -> None:
    """#283: a project that reported a tracking failure must be named."""
    from video_editor_state.ledger import ProjectLedger

    ledger_path = tmp_path / "ledger.db"
    config_path = _write_config(tmp_path, ledger_path)
    ledger = ProjectLedger(ledger_path)
    ledger.init_db()
    ledger.upsert_project("p1")
    ledger.record_issue(
        "p1",
        severity="medium",
        category="cost",
        code="COST_TRACKING_DEGRADED",
        description="pricing failed",
    )

    result = CliRunner().invoke(app, ["cost-report", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "degraded cost tracking" in result.output
    assert "p1" in result.output
