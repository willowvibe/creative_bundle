"""Artifact store with idempotent checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import structlog

from video_editor_state.manifest import JobManifest, OperationLog

logger = structlog.get_logger(__name__)


def atomic_write_text(path: Path | str, text: str) -> Path:
    """Atomically write ``text`` to ``path``.

    Writes to a uniquely-named temp file next to ``path``, flushes +
    fsyncs, then ``os.replace``s it into place. A crash or power loss
    mid-write leaves either the old file or the new one — never a
    half-written artifact.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return target


def atomic_write_json(path: Path | str, data: Any) -> Path:
    """Atomically write JSON ``data`` to ``path``."""
    return atomic_write_text(path, json.dumps(data, indent=2))


class ArtifactStore:
    """Filesystem-backed artifact store."""

    def __init__(self, work_dir: Path | str, output_dir: Path | str | None = None):
        self.work_dir = Path(work_dir)
        # When the caller knows the job's output root, use it directly. The
        # fallback below guesses from the work path, which is wrong for any
        # layout whose last segment is not literally ``work`` (#260).
        self.output_dir = Path(output_dir) if output_dir is not None else None
        # Bug #89: the constructor must not create directories. Callers
        # are responsible for ``ensure_dirs()`` so a worker draining many
        # jobs doesn't repeatedly mkdir on every context build.
        self.manifest_path = self.work_dir / "manifest.json"
        self.manifest = self._load_or_create()

    def _load_or_create(self) -> JobManifest:
        if self.manifest_path.exists():
            return JobManifest.load(self.manifest_path)
        return JobManifest(job_id=self.work_dir.name)

    def artifact(self, *parts: str) -> Path:
        path = self.work_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def output(self, *parts: str) -> Path:
        """Resolve a path under the job output directory.

        When the store was built with an explicit ``output_dir``, paths resolve
        there. Otherwise the output directory is derived from the work directory
        by swapping the rightmost ``work`` segment for ``output`` and preserving
        every segment after it (e.g. ``/data/work/job-1`` → ``/data/output/job-1``),
        falling back to ``<work_dir>/output`` when there is no such segment.
        """
        if self.output_dir is not None:
            path = self.output_dir.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        parts_list = list(self.work_dir.parts)
        try:
            work_idx = len(parts_list) - 1 - parts_list[::-1].index("work")
        except ValueError:
            work_idx = -1
        if work_idx >= 0:
            parts_list[work_idx] = "output"
            output_dir = Path(*parts_list)
        else:
            # Fallback: append ``output`` as a sibling directory when the
            # work directory does not follow the conventional naming.
            output_dir = self.work_dir / "output"
        path = output_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def record(self, operation: str, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        log = OperationLog(
            operation=operation,
            inputs=inputs,
            outputs=outputs,
            tenant_id=self.manifest.tenant_id,
        )
        self.manifest.operations.append(log)
        self.save()
        logger.info("operation_recorded", operation=operation, job_id=self.manifest.job_id)

    def save(self) -> None:
        self.manifest.save(self.manifest_path)

    def exists(self, *parts: str) -> bool:
        return self.artifact(*parts).exists()

    def read_json(self, *parts: str) -> Any:
        path = self.artifact(*parts)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def write_json(self, *parts: str, data: Any) -> Path:
        """Write JSON data atomically to the artifact path."""
        path = self.artifact(*parts)
        return atomic_write_json(path, data)

    def hash_file(self, path: Path | str) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def should_run(self, operation: str, output_path: Path | str, force: bool = False) -> bool:
        if force:
            return True
        if not Path(output_path).exists():
            return True
        logger.info("skipping_idempotent_operation", operation=operation, output=str(output_path))
        return False
