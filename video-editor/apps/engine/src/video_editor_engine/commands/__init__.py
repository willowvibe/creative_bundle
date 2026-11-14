"""Typed command registry and control channel (FilmCraft-style).

Importing this package registers the built-in commands on
:data:`video_editor_engine.commands.registry.default_registry`.
"""

from __future__ import annotations

from video_editor_engine.commands import builtins as _builtins  # noqa: F401 — registers builtins
from video_editor_engine.commands import (
    pipeline as _pipeline,  # noqa: F401 — registers pipeline commands
)
from video_editor_engine.commands.control import handle_request, serve_control
from video_editor_engine.commands.registry import (
    Command,
    CommandError,
    CommandExecutionError,
    CommandPermissionError,
    CommandRegistry,
    CommandValidationError,
    DuplicateCommandError,
    UnknownCommandError,
    default_registry,
    register_command,
)

__all__ = [
    "Command",
    "CommandError",
    "CommandExecutionError",
    "CommandPermissionError",
    "CommandRegistry",
    "CommandValidationError",
    "DuplicateCommandError",
    "UnknownCommandError",
    "default_registry",
    "handle_request",
    "register_command",
    "serve_control",
]
