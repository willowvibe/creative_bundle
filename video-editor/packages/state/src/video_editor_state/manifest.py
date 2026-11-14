"""Job manifest and operation log models."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from video_editor_state.paths import default_output_root, default_work_root


class OperationLog(BaseModel):
    """A single idempotent operation record."""

    operation: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tenant_id: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class JobManifest(BaseModel):
    """Top-level manifest for a job."""

    job_id: str
    tenant_id: str = "default"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    work_root: str = Field(default_factory=default_work_root)
    output_root: str = Field(default_factory=default_output_root)
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    operations: list[OperationLog] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str) -> JobManifest:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def save(self, path: Path | str | None = None) -> None:
        """Atomically persist the manifest to disk.

        Writes to ``<path>.tmp``, fsyncs the file, then ``os.replace``s it
        into place. A SIGKILL or power loss mid-write leaves either the old
        manifest or the new one — never a half-written JSON.
        """
        target = Path(path) if path else Path(self.work_root) / self.job_id / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    def stage_done(self, stage: str, outputs: dict[str, Any] | None = None) -> None:
        self.stages[stage] = {
            "completed_at": datetime.now(UTC).isoformat(),
            "outputs": outputs or {},
        }

    def stage_outputs(self, stage: str) -> dict[str, Any]:
        return cast(dict[str, Any], self.stages.get(stage, {}).get("outputs", {}))

    def is_stage_complete(self, stage: str) -> bool:
        return "completed_at" in self.stages.get(stage, {})

    def log_operation(
        self,
        operation: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        tenant_id: str = "",
    ) -> None:
        """Append an idempotent operation record to the manifest log."""
        self.operations.append(
            OperationLog(
                operation=operation,
                inputs=inputs or {},
                outputs=outputs or {},
                tenant_id=tenant_id,
            )
        )
