"""Default job roots for the CLI.

The resolution rule (and the env vars it reads) lives in
:mod:`video_editor_state.paths` so the CLI, the engine, the MCP server and
``JobContext`` itself all agree. Re-exported here because the CLI resolves its
``typer`` option defaults from this module.
"""

from __future__ import annotations

from video_editor_state.paths import (
    CONTAINER_OUTPUT_ROOT,
    CONTAINER_WORK_ROOT,
    LOCAL_OUTPUT_ROOT,
    LOCAL_WORK_ROOT,
    OUTPUT_ROOT_ENV,
    WORK_ROOT_ENV,
    default_output_root,
    default_work_root,
)

__all__ = [
    "CONTAINER_OUTPUT_ROOT",
    "CONTAINER_WORK_ROOT",
    "LOCAL_OUTPUT_ROOT",
    "LOCAL_WORK_ROOT",
    "OUTPUT_ROOT_ENV",
    "WORK_ROOT_ENV",
    "default_output_root",
    "default_work_root",
]
