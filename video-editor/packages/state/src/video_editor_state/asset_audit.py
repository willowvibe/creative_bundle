"""Job-level evidence about the assets a build actually used (#281, #283).

The asset cache is the durable record the pipeline writes before it renders.
QA reads it here instead of trusting the plan or a provider's success return,
so a placeholder that slipped through cannot pass unnoticed.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable
from pathlib import Path

DEFAULT_CACHE_DIR = ".cache/assets"
META_SUFFIX = ".meta.json"
COST_TRACKING_DEGRADED_FILE = "cost_tracking_degraded.json"


def stub_asset_ids(
    work_dir: Path | str,
    *,
    used_paths: Iterable[str | Path] | None = None,
) -> list[str]:
    """Return the ids of cached assets whose provider starts with ``stub_``.

    Pass ``used_paths`` — the ``local_path`` of every asset this build
    resolved — to audit only the files the build actually used. The cache is
    content-keyed and survives a run, so a reused job id otherwise reports a
    stub from an earlier draft that the current build never touched.

    Missing or malformed cache files are ignored: this is an audit aid, not a
    reason to fail a build that already produced media.
    """
    cache = Path(work_dir) / DEFAULT_CACHE_DIR
    if not cache.is_dir():
        return []
    used: set[str] | None = None
    if used_paths is not None:
        used = {Path(path).stem for path in used_paths}
    found: set[str] = set()
    for meta_path in sorted(cache.glob(f"*{META_SUFFIX}")):
        if used is not None and meta_path.name.removesuffix(META_SUFFIX) not in used:
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            continue
        provider = metadata.get("provider")
        if isinstance(provider, str) and provider.startswith("stub_"):
            asset_id = data.get("asset_id")
            found.add(str(asset_id) if asset_id else meta_path.name)
    return sorted(found)


def used_asset_paths(results: Iterable[object]) -> list[str]:
    """The local path of every resolved asset in ``results``.

    A real :class:`AssetResult` carries ``local_path``; a lighter stand-in may
    only expose ``resolved_path``. Read both so the audit filters the cache by
    what this build consumed without the caller having to normalize its result
    shapes.
    """
    paths: list[str] = []
    for result in results:
        path = getattr(result, "local_path", None)
        if not path:
            resolved = getattr(result, "resolved_path", None)
            path = resolved() if callable(resolved) else resolved
        if path:
            paths.append(str(path))
    return paths


def cost_tracking_degraded_reasons(work_dir: Path | str) -> list[str]:
    """Return the reasons the last asset run could not track its spend."""
    path = Path(work_dir) / COST_TRACKING_DEGRADED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    reasons = data.get("reasons")
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons]


def write_cost_tracking_degraded(work_dir: Path | str, reasons: list[str]) -> None:
    """Persist the degradation reasons atomically for the QA/report stages."""
    path = Path(work_dir) / COST_TRACKING_DEGRADED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(
            json.dumps({"reasons": reasons}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
