"""Tests for the SQLite project ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from video_editor_state.ledger import (
    LedgerConfig,
    NullLedger,
    ProjectLedger,
)


def test_ledger_init_creates_tables(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    ledger = ProjectLedger(db)
    ledger.init_db()
    conn = sqlite3.connect(db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "projects",
        "jobs",
        "cost_lines",
        "issues",
        "provider_usage",
        "artifacts",
        "project_tags",
    }.issubset(tables)


def test_ledger_context_manager(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    with ProjectLedger(db) as ledger:
        ledger.upsert_project("p1", name="Test")
        assert ledger.get_project("p1") is not None


def test_upsert_and_get_project(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project(
        "p1",
        name="Demo",
        template="short_explainer",
        pipeline_kind="skills",
        status="running",
        source_path="/src/video.mp4",
        budget_limit=10.0,
        tags=["demo", "internal"],
    )
    project = ledger.get_project("p1")
    assert project is not None
    assert project["name"] == "Demo"
    assert project["template"] == "short_explainer"
    assert project["status"] == "running"
    assert project["budget_limit"] == 10.0
    assert project["tags"] == ["demo", "internal"]


def test_set_project_status(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1", status="running")
    ledger.set_project_status("p1", "complete", notes="done")
    project = ledger.get_project("p1")
    assert project is not None
    assert project["status"] == "complete"
    assert project["completed_at"] is not None


def test_record_job_and_set_status(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1")
    ledger.record_job("p1", "ingest", inputs={"source": "a.mp4"})
    ledger.set_job_status("p1", "ingest", "done")
    project = ledger.get_project("p1")
    assert project is not None
    jobs = project["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["stage"] == "ingest"
    assert jobs[0]["status"] == "done"
    assert jobs[0]["completed_at"] is not None


def test_record_cost(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1")
    ledger.record_cost(
        "p1",
        asset_id="scene_1_tts",
        provider="elevenlabs_tts",
        task_type="tts",
        model="eleven_turbo_v2_5",
        estimated_cost=0.05,
        actual_cost=0.04,
        status="success",
    )
    project = ledger.get_project("p1")
    assert project is not None
    assert len(project["cost_lines"]) == 1
    assert project["cost_lines"][0]["provider"] == "elevenlabs_tts"
    assert project["cost_lines"][0]["actual_cost"] == 0.04


def test_record_issue(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1")
    ledger.record_issue(
        "p1",
        severity="high",
        category="render",
        description="Frame drop detected",
        code="BUG_1",
    )
    project = ledger.get_project("p1")
    assert project is not None
    assert len(project["issues"]) == 1
    assert project["issues"][0]["severity"] == "high"


def test_record_artifact(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1")
    ledger.record_artifact("p1", "final_video", path="/out/final.mp4", size_bytes=1024)
    project = ledger.get_project("p1")
    assert project is not None
    assert len(project["artifacts"]) == 1
    assert project["artifacts"][0]["kind"] == "final_video"


def test_provider_usage_rollup(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1")
    for _ in range(3):
        ledger.record_cost(
            "p1",
            provider="elevenlabs_tts",
            task_type="tts",
            estimated_cost=0.05,
            actual_cost=0.04,
        )
    ledger.record_cost(
        "p1",
        provider="fal_ai",
        task_type="image",
        estimated_cost=0.5,
        actual_cost=0.5,
    )
    ledger.rollup_provider_usage("p1")
    project = ledger.get_project("p1")
    assert project is not None
    usage = project["provider_usage"]
    assert len(usage) == 2
    tts = next(u for u in usage if u["provider"] == "elevenlabs_tts")
    assert tts["call_count"] == 3
    assert tts["estimated_total"] == pytest.approx(0.15)
    assert tts["actual_total"] == pytest.approx(0.12)


def test_list_projects_and_filters(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1", status="complete")
    ledger.upsert_project("p2", status="dropped")
    ledger.upsert_project("p3", status="running")
    complete = ledger.list_projects(status="complete")
    assert len(complete) == 1
    assert complete[0]["id"] == "p1"
    all_projects = ledger.list_projects(limit=2)
    assert len(all_projects) == 2


def test_cost_report_aggregation(tmp_path: Path) -> None:
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.upsert_project("p1", template="short_explainer")
    ledger.upsert_project("p2", template="short_explainer")
    for _ in range(2):
        ledger.record_cost(
            "p1",
            provider="fal_ai",
            task_type="image",
            estimated_cost=0.5,
            actual_cost=0.5,
        )
    ledger.record_cost(
        "p2",
        provider="fal_ai",
        task_type="image",
        estimated_cost=0.5,
        actual_cost=0.5,
    )
    report = ledger.cost_report(provider="fal_ai", task_type="image")
    assert len(report) == 1
    assert report[0]["call_count"] == 3
    assert report[0]["actual_total"] == pytest.approx(1.5)
    template_report = ledger.cost_report(template="short_explainer")
    assert len(template_report) == 1


def test_cost_report_counts_projects_without_a_project_row(tmp_path: Path) -> None:
    """A hand-authored build can attach the ledger and never upsert the project.

    The spend is still real (one paid asset per row), so the report must count
    it. An INNER JOIN on ``projects`` silently dropped every row for a job that
    only ever wrote cost lines (#267).
    """
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.record_cost(
        "adhoc",
        provider="poyo_ai",
        task_type="image",
        estimated_cost=0.025,
        actual_cost=0.025,
    )
    report = ledger.cost_report(provider="poyo_ai")
    assert len(report) == 1
    assert report[0]["call_count"] == 1
    assert report[0]["actual_total"] == pytest.approx(0.025)


def test_cost_report_counts_unpriced_calls(tmp_path: Path) -> None:
    """#283: a NULL cost is skipped by SUM, so the report must say how many there are."""
    ledger = ProjectLedger(tmp_path / "ledger.db")
    ledger.init_db()
    ledger.record_cost("p1", provider="poyo_ai", task_type="image", actual_cost=0.03)
    ledger.record_cost("p1", provider="poyo_ai", task_type="image", actual_cost=None)

    [row] = ledger.cost_report(provider="poyo_ai")

    assert row["call_count"] == 2
    assert row["actual_total"] == pytest.approx(0.03)
    assert row["unpriced_count"] == 1


def test_null_ledger_is_no_op() -> None:
    null = NullLedger()
    null.init_db()
    null.upsert_project("p1")
    null.record_cost("p1", provider="x")
    assert null.get_project("p1") is None
    assert null.list_projects() == []
    assert null.cost_report() == []


def test_ledger_config_defaults() -> None:
    cfg = LedgerConfig()
    assert cfg.enabled is True
    assert cfg.path is None
