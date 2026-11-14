"""Default work/output roots, resolved the same way by every entry point.

Container deployments mount ``/work`` and ``/output``; a developer machine has
neither, and writing to them raises ``PermissionError``. The resolution order
is:

1. ``VE_WORK_ROOT`` / ``VE_OUTPUT_ROOT`` when set,
2. the container mount, if it exists,
3. ``./projects`` / ``./outputs`` in the current directory.

It lives in ``video_editor_state`` rather than the CLI because ``JobContext``
and ``JobManifest`` are constructed by the engine, the MCP server and ad-hoc
scripts too, and a library caller that omits the roots must not silently land
on a path that only exists inside a container.
"""

from __future__ import annotations

import os
from pathlib import Path

WORK_ROOT_ENV = "VE_WORK_ROOT"
OUTPUT_ROOT_ENV = "VE_OUTPUT_ROOT"
DATA_DIR_ENV = "VIDEO_EDITOR_DATA_DIR"

CONTAINER_WORK_ROOT = "/work"
CONTAINER_OUTPUT_ROOT = "/output"

LOCAL_WORK_ROOT = "projects"
LOCAL_OUTPUT_ROOT = "outputs"


def _mounted(path: str) -> bool:
    """Return True when ``path`` exists (the container mount is present)."""
    return Path(path).is_dir()


def _default(env_var: str, container_path: str, local_path: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    data_dir = os.environ.get(DATA_DIR_ENV)
    if data_dir:
        return str(Path(data_dir).expanduser() / local_path)
    return container_path if _mounted(container_path) else local_path


def default_work_root() -> str:
    """Return the work root to use when the caller did not pass one."""
    return _default(WORK_ROOT_ENV, CONTAINER_WORK_ROOT, LOCAL_WORK_ROOT)


def default_output_root() -> str:
    """Return the output root to use when the caller did not pass one."""
    return _default(OUTPUT_ROOT_ENV, CONTAINER_OUTPUT_ROOT, LOCAL_OUTPUT_ROOT)


QUEUE_DB_ENV = "VE_QUEUE_DB"


def default_cache_root() -> str:
    """Machine-wide cache: ``VE_CACHE_DIR``, else ``<data dir>/cache``, else ``.cache``."""
    override = os.environ.get("VE_CACHE_DIR")
    if override:
        return override
    data_dir = os.environ.get(DATA_DIR_ENV)
    if data_dir:
        return str(Path(data_dir).expanduser() / "cache")
    return ".cache"


def default_queue_db() -> str:
    """SQLite queue path: ``VE_QUEUE_DB`` or ``<output root>/queue.db``."""
    override = os.environ.get(QUEUE_DB_ENV)
    if override:
        return override
    return str(Path(default_output_root()) / "queue.db")
