"""Remember paid PoYo tasks so a crash or poll timeout never pays for the same asset twice.

PoYo bills when a task is accepted and offers no way to list past tasks, so a task id lost between submit
and download is a lost paid render (two episode scripts lost tasks this way). Each PoYo provider writes a
small journal next to the output file as soon as a task is accepted, resumes that task on the next run if
the request is identical (same payload hash), and deletes the journal once the file is on disk.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def journal_path(output_path: Path) -> Path:
    """Where the task journal for ``output_path`` lives."""
    return output_path.with_name(f"{output_path.name}.poyo-task.json")


def payload_hash(payload: dict[str, Any]) -> str:
    """A stable hash of the request payload: a changed request never resumes an old task."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[
        :16
    ]


def pending_task(output_path: Path, payload: dict[str, Any]) -> str | None:
    """The task id of an unfinished paid task for this exact request, or ``None``."""
    path = journal_path(output_path)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if entry.get("payload_hash") != payload_hash(payload) or not entry.get("task_id"):
        return None
    logger.info("poyo_task_resumed", task_id=entry["task_id"], output=str(output_path))
    return str(entry["task_id"])


def record_task(
    output_path: Path, payload: dict[str, Any], task_id: str, *, model: str = ""
) -> None:
    """Write the journal right after PoYo accepts (and bills) a task."""
    path = journal_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "task_id": task_id,
        "model": model,
        "payload_hash": payload_hash(payload),
        "accepted_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")


def clear_task(output_path: Path) -> None:
    """Forget the task once its file is saved (or PoYo reported it failed)."""
    journal_path(output_path).unlink(missing_ok=True)
