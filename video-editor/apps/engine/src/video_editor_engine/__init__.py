"""Core Python orchestrator for script-to-video pipelines and planning models.

The legacy raw-footage pipeline was removed; raw footage is edited by the
skills-first `SkillsPipeline` (`ve run`) and the render graph in
`video_editor_core`.

This package also exposes the typed command registry (``video_editor_engine
.commands``) that backs the ``ve commands`` / ``ve control`` CLI and the MCP
``run_command`` tool.
"""

from __future__ import annotations

from video_editor_engine.commands import (
    Command,
    CommandError,
    CommandRegistry,
    default_registry,
    register_command,
)

__all__ = [
    "Command",
    "CommandError",
    "CommandRegistry",
    "default_registry",
    "register_command",
]
