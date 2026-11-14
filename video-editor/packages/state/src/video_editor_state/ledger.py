"""SQLite-backed project ledger.

Persists every project run, its per-stage status, actual/estimated provider
costs, issues/audits, and final deliverable metadata. The ledger is optional
and file-backed: when disabled or when no path is configured, calls fall back
to :class:`NullLedger` so the engine never fails because of missing DB setup.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class LedgerError(RuntimeError):
    """Raised when a ledger operation cannot be completed."""


class LedgerConfig(BaseModel):
    """Runtime configuration for the project ledger."""

    enabled: bool = True
    path: Path | None = Field(default=None)


class _LedgerBackend(Protocol):
    """Common interface implemented by ProjectLedger and NullLedger."""

    def init_db(self) -> None: ...
    def close(self) -> None: ...
    def upsert_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        template: str | None = None,
        pipeline_kind: str | None = None,
        status: str = "planned",
        source_path: str | None = None,
        output_video_path: str | None = None,
        final_duration_s: float | None = None,
        budget_limit: float | None = None,
        total_estimated_cost: float | None = None,
        total_actual_cost: float | None = None,
        currency: str = "credits",
        notes: str | None = None,
        tags: list[str] | None = None,
    ) -> None: ...
    def set_project_status(
        self,
        project_id: str,
        status: str,
        *,
        notes: str | None = None,
    ) -> None: ...
    def record_job(
        self,
        project_id: str,
        stage: str,
        *,
        job_id: str | None = None,
        status: str = "running",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> None: ...
    def set_job_status(
        self,
        project_id: str,
        stage: str,
        status: str,
    ) -> None: ...
    def record_cost(
        self,
        project_id: str,
        *,
        asset_id: str | None = None,
        provider: str | None = None,
        task_type: str | None = None,
        model: str | None = None,
        estimated_cost: float | None = None,
        actual_cost: float | None = None,
        currency: str = "credits",
        latency_ms: int | None = None,
        generated_at: str | None = None,
        status: str = "success",
        job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    def record_issue(
        self,
        project_id: str,
        *,
        severity: str,
        category: str,
        description: str,
        code: str | None = None,
        job_id: str | None = None,
        detected_at: str | None = None,
    ) -> None: ...
    def record_artifact(
        self,
        project_id: str,
        kind: str,
        path: str | None = None,
        *,
        job_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        created_at: str | None = None,
    ) -> None: ...
    def rollup_provider_usage(self, project_id: str) -> None: ...
    def get_project(self, project_id: str) -> dict[str, Any] | None: ...
    def list_projects(
        self,
        *,
        status: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def cost_report(
        self,
        *,
        provider: str | None = None,
        task_type: str | None = None,
        template: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def degraded_cost_projects(
        self,
        *,
        since: str | None = None,
    ) -> list[str]: ...


class NullLedger:
    """No-op ledger used when the ledger is disabled.

    Keeps the same interface as :class:`ProjectLedger` so callers never have
    to branch on whether a real ledger is attached.
    """

    def init_db(self) -> None:
        return None

    def close(self) -> None:
        return None

    def upsert_project(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def set_project_status(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def record_job(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def set_job_status(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def record_cost(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def record_issue(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def record_artifact(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def rollup_provider_usage(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN002,ANN003
        return None

    def get_project(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:  # noqa: ANN002,ANN003
        return None

    def list_projects(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN002,ANN003
        return []

    def cost_report(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN002,ANN003
        return []

    def degraded_cost_projects(self, *args: Any, **kwargs: Any) -> list[str]:  # noqa: ANN002,ANN003
        return []

    def __enter__(self) -> NullLedger:
        return self

    def __exit__(self, *exc: Any) -> None:  # noqa: ANN001
        return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ProjectLedger:
    """SQLite-backed project ledger.

    Tables:
        projects         — one row per finished, partial, dropped or failed project
        jobs             — one row per pipeline stage execution
        cost_lines       — one row per provider invocation
        issues           — audit findings, QA/review failures, budget overruns
        provider_usage   — rolled-up usage per provider per project
        artifacts        — final and intermediate deliverables
        project_tags     — user/template tags for grouping
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self.path,
                timeout=30.0,
                check_same_thread=False,
            )
            # WAL mode lets readers and writers coexist safely for short writes.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def init_db(self) -> None:
        """Create tables and indexes idempotently."""
        conn = self._connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT,
                template TEXT,
                pipeline_kind TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                source_path TEXT,
                output_video_path TEXT,
                final_duration_s REAL,
                budget_limit REAL,
                total_estimated_cost REAL,
                total_actual_cost REAL,
                currency TEXT NOT NULL DEFAULT 'credits',
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT,
                completed_at TEXT,
                inputs TEXT,
                outputs TEXT
            );

            CREATE TABLE IF NOT EXISTS cost_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                job_id TEXT,
                asset_id TEXT,
                provider TEXT,
                task_type TEXT,
                model TEXT,
                estimated_cost REAL,
                actual_cost REAL,
                currency TEXT NOT NULL DEFAULT 'credits',
                latency_ms INTEGER,
                generated_at TEXT,
                status TEXT NOT NULL DEFAULT 'success',
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                job_id TEXT,
                severity TEXT NOT NULL,
                category TEXT NOT NULL,
                code TEXT,
                description TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution TEXT
            );

            CREATE TABLE IF NOT EXISTS provider_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                task_type TEXT,
                call_count INTEGER NOT NULL DEFAULT 0,
                estimated_total REAL NOT NULL DEFAULT 0.0,
                actual_total REAL NOT NULL DEFAULT 0.0,
                first_used_at TEXT,
                last_used_at TEXT,
                UNIQUE(project_id, provider, task_type)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                job_id TEXT,
                kind TEXT NOT NULL,
                path TEXT,
                mime_type TEXT,
                size_bytes INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS project_tags (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (project_id, tag)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
            CREATE INDEX IF NOT EXISTS idx_cost_project ON cost_lines(project_id);
            CREATE INDEX IF NOT EXISTS idx_cost_provider ON cost_lines(provider, task_type);
            CREATE INDEX IF NOT EXISTS idx_issues_project ON issues(project_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind);
            """
        )
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - the ledger is a side channel; a write failure must not fail the job
                logger.warning("ledger_close_failed", path=str(self.path), exc_info=True)
            finally:
                self._conn = None

    def __enter__(self) -> ProjectLedger:
        self.init_db()
        return self

    def __exit__(self, *exc: Any) -> None:  # noqa: ANN001
        self.close()

    def upsert_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        template: str | None = None,
        pipeline_kind: str | None = None,
        status: str = "planned",
        source_path: str | None = None,
        output_video_path: str | None = None,
        final_duration_s: float | None = None,
        budget_limit: float | None = None,
        total_estimated_cost: float | None = None,
        total_actual_cost: float | None = None,
        currency: str = "credits",
        notes: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        now = _now_iso()
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO projects (
                id, name, template, pipeline_kind, status, created_at, started_at,
                source_path, output_video_path, final_duration_s, budget_limit,
                total_estimated_cost, total_actual_cost, currency, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                template=excluded.template,
                pipeline_kind=excluded.pipeline_kind,
                status=excluded.status,
                started_at=COALESCE(projects.started_at, excluded.started_at),
                source_path=excluded.source_path,
                output_video_path=excluded.output_video_path,
                final_duration_s=excluded.final_duration_s,
                budget_limit=excluded.budget_limit,
                total_estimated_cost=excluded.total_estimated_cost,
                total_actual_cost=excluded.total_actual_cost,
                currency=excluded.currency,
                notes=excluded.notes
            """,
            (
                project_id,
                name,
                template,
                pipeline_kind,
                status,
                now,
                now if status == "running" else None,
                source_path,
                output_video_path,
                final_duration_s,
                budget_limit,
                total_estimated_cost,
                total_actual_cost,
                currency,
                notes,
            ),
        )
        if tags:
            conn.executemany(
                "INSERT OR IGNORE INTO project_tags (project_id, tag) VALUES (?, ?)",
                [(project_id, tag) for tag in tags],
            )
        conn.commit()

    def set_project_status(
        self,
        project_id: str,
        status: str,
        *,
        notes: str | None = None,
    ) -> None:
        now = _now_iso()
        conn = self._connection()
        updates = {"status": status, "completed_at": now}
        if notes is not None:
            updates["notes"] = notes
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [project_id]
        conn.execute(f"UPDATE projects SET {set_clause} WHERE id=?", values)  # noqa: S608
        conn.commit()

    def record_job(
        self,
        project_id: str,
        stage: str,
        *,
        job_id: str | None = None,
        status: str = "running",
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        resolved_job_id = job_id or f"{project_id}:{stage}"
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO jobs (
                id, project_id, stage, status, started_at, inputs, outputs
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                inputs=COALESCE(excluded.inputs, jobs.inputs),
                outputs=COALESCE(excluded.outputs, jobs.outputs)
            """,
            (
                resolved_job_id,
                project_id,
                stage,
                status,
                now,
                json.dumps(inputs) if inputs else None,
                json.dumps(outputs) if outputs else None,
            ),
        )
        conn.commit()

    def set_job_status(
        self,
        project_id: str,
        stage: str,
        status: str,
    ) -> None:
        now = _now_iso()
        job_id = f"{project_id}:{stage}"
        conn = self._connection()
        conn.execute(
            """
            UPDATE jobs
            SET status=?, completed_at=?
            WHERE id=? AND project_id=?
            """,
            (status, now, job_id, project_id),
        )
        conn.commit()

    def record_cost(
        self,
        project_id: str,
        *,
        asset_id: str | None = None,
        provider: str | None = None,
        task_type: str | None = None,
        model: str | None = None,
        estimated_cost: float | None = None,
        actual_cost: float | None = None,
        currency: str = "credits",
        latency_ms: int | None = None,
        generated_at: str | None = None,
        status: str = "success",
        job_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _now_iso()
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO cost_lines (
                project_id, job_id, asset_id, provider, task_type, model,
                estimated_cost, actual_cost, currency, latency_ms, generated_at,
                status, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                job_id,
                asset_id,
                provider,
                task_type,
                model,
                estimated_cost,
                actual_cost,
                currency,
                latency_ms,
                generated_at or now,
                status,
                json.dumps(metadata) if metadata else None,
            ),
        )
        conn.commit()

    def record_issue(
        self,
        project_id: str,
        *,
        severity: str,
        category: str,
        description: str,
        code: str | None = None,
        job_id: str | None = None,
        detected_at: str | None = None,
    ) -> None:
        now = _now_iso()
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO issues (
                project_id, job_id, severity, category, code, description,
                detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                job_id,
                severity,
                category,
                code,
                description,
                detected_at or now,
            ),
        )
        conn.commit()

    def record_artifact(
        self,
        project_id: str,
        kind: str,
        path: str | None = None,
        *,
        job_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        created_at: str | None = None,
    ) -> None:
        now = _now_iso()
        conn = self._connection()
        conn.execute(
            """
            INSERT INTO artifacts (
                project_id, job_id, kind, path, mime_type, size_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                job_id,
                kind,
                path,
                mime_type,
                size_bytes,
                created_at or now,
            ),
        )
        conn.commit()

    def rollup_provider_usage(self, project_id: str) -> None:
        """Recompute provider_usage rollup rows for a project."""
        conn = self._connection()
        conn.execute(
            "DELETE FROM provider_usage WHERE project_id=?",
            (project_id,),
        )
        conn.execute(
            """
            INSERT INTO provider_usage (
                project_id, provider, task_type, call_count,
                estimated_total, actual_total, first_used_at, last_used_at
            )
            SELECT
                project_id,
                provider,
                task_type,
                COUNT(*),
                COALESCE(SUM(estimated_cost), 0.0),
                COALESCE(SUM(actual_cost), 0.0),
                MIN(generated_at),
                MAX(generated_at)
            FROM cost_lines
            WHERE project_id=? AND provider IS NOT NULL
            GROUP BY project_id, provider, task_type
            """,
            (project_id,),
        )
        conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        # sqlite3.Row iteration yields integer indices, so we must use .keys()
        # to get the column names. ruff's SIM118 does not apply here.
        return {k: row[k] for k in row.keys()}  # noqa: SIM118

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        conn = self._connection()
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            return None
        project = self._row_to_dict(row)
        project["jobs"] = [
            self._row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY started_at",
                (project_id,),
            )
        ]
        project["cost_lines"] = [
            self._row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM cost_lines WHERE project_id=? ORDER BY generated_at",
                (project_id,),
            )
        ]
        project["issues"] = [
            self._row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM issues WHERE project_id=? ORDER BY detected_at",
                (project_id,),
            )
        ]
        project["artifacts"] = [
            self._row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at",
                (project_id,),
            )
        ]
        project["provider_usage"] = [
            self._row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM provider_usage WHERE project_id=?",
                (project_id,),
            )
        ]
        project["tags"] = [
            r["tag"]
            for r in conn.execute(
                "SELECT tag FROM project_tags WHERE project_id=? ORDER BY tag",
                (project_id,),
            )
        ]
        return project

    def list_projects(
        self,
        *,
        status: str | None = None,
        since: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conn = self._connection()
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if since:
            where.append("created_at >= ?")
            params.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"SELECT * FROM projects {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?"  # noqa: S608
        params.extend([limit, offset])
        return [self._row_to_dict(r) for r in conn.execute(query, params)]

    def cost_report(
        self,
        *,
        provider: str | None = None,
        task_type: str | None = None,
        template: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return aggregate spend grouped by provider and task type.

        Optionally filter by provider, task_type, project template, or date.
        ``unpriced_count`` is the number of calls in the group with no cost on
        record; their spend is missing from ``actual_total``.
        """
        conn = self._connection()
        where: list[str] = []
        params: list[Any] = []
        if provider:
            where.append("c.provider = ?")
            params.append(provider)
        if task_type:
            where.append("c.task_type = ?")
            params.append(task_type)
        if template:
            where.append("p.template = ?")
            params.append(template)
        if since:
            where.append("c.generated_at >= ?")
            params.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = (
            "SELECT "
            "COALESCE(c.provider, 'unknown') AS provider, "
            "COALESCE(c.task_type, 'unknown') AS task_type, "
            "COUNT(*) AS call_count, "
            "COALESCE(SUM(c.estimated_cost), 0.0) AS estimated_total, "
            "COALESCE(SUM(c.actual_cost), 0.0) AS actual_total, "
            # A row the manager could not price is NULL, which SUM skips: count
            # them so a report never presents an undercount as the total (#283).
            "SUM(CASE WHEN c.actual_cost IS NULL THEN 1 ELSE 0 END) AS unpriced_count "
            "FROM cost_lines c "
            # LEFT, not INNER: a hand-authored build can write cost lines
            # without ever upserting its project row, and its spend is real.
            # An INNER JOIN hid every such project from the report (#267).
            "LEFT JOIN projects p ON p.id = c.project_id "
            f"{clause} "
            "GROUP BY c.provider, c.task_type "
            "ORDER BY actual_total DESC, estimated_total DESC"
        )
        return [self._row_to_dict(r) for r in conn.execute(query, params)]

    def degraded_cost_projects(self, *, since: str | None = None) -> list[str]:
        """Projects that reported a spend-tracking failure (#283)."""
        conn = self._connection()
        where = ["code = 'COST_TRACKING_DEGRADED'"]
        params: list[Any] = []
        if since:
            where.append("detected_at >= ?")
            params.append(since)
        query = (
            "SELECT DISTINCT project_id FROM issues "
            f"WHERE {' AND '.join(where)} ORDER BY project_id"
        )
        return [str(row[0]) for row in conn.execute(query, params)]
