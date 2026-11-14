"""Typed command registry — "everything is a command".

Borrowed from FilmCraft's agent architecture: every engine capability that can
be driven by an agent is a typed :class:`Command` with a stable string ``id``, a
one-line ``summary``, a ``category``, a pydantic ``params_model`` describing its
inputs, and a ``handler`` that receives the validated params model and returns a
JSON-serializable result.

The same registry backs three front ends:

* the ``ve commands`` / ``ve control`` CLI over the JSON-lines control channel,
* the MCP ``run_command`` / ``list_commands`` tools,
* programmatic use from Python (``await default_registry.execute(...)``).
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from video_editor_state.trust import TrustError, TrustLevel, validate_untrusted_params

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
    "register_command",
]


class CommandError(Exception):
    """Base class for every command-registry failure.

    Callers that need to distinguish the failure mode can catch a subclass;
    callers that only care that *something* went wrong can catch this base.
    """

    code: str = "command_error"


class DuplicateCommandError(CommandError):
    """Raised when registering an ``id`` that is already present."""

    code = "duplicate_command"

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"Command already registered: {command_id}")


class UnknownCommandError(CommandError):
    """Raised when executing or fetching an ``id`` that is not registered."""

    code = "unknown_command"

    def __init__(self, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"Unknown command: {command_id}")


class CommandValidationError(CommandError):
    """Raised when the supplied params fail the command's pydantic model."""

    code = "validation_error"

    def __init__(self, command_id: str, message: str) -> None:
        self.command_id = command_id
        super().__init__(f"Invalid params for {command_id}: {message}")


class CommandExecutionError(CommandError):
    """Raised when a command handler raises."""

    code = "execution_error"

    def __init__(self, command_id: str, message: str) -> None:
        self.command_id = command_id
        super().__init__(f"Command {command_id} failed: {message}")


class CommandPermissionError(CommandError):
    """Raised when the caller's trust level does not allow the command (A10)."""

    code = "permission_denied"

    def __init__(self, command_id: str, message: str) -> None:
        self.command_id = command_id
        super().__init__(f"{command_id} is not available to this caller: {message}")


@dataclass(frozen=True)
class Command:
    """A single typed, dispatachable engine capability."""

    id: str
    summary: str
    category: str
    params_model: type[BaseModel]
    handler: Callable[[Any], Any]
    #: The command writes to the job directory (or elsewhere). Untrusted
    #: front-ends (MCP today, the control server after A10) refuse it (#299).
    mutates: bool = False
    #: The command can spend money. Always refused by untrusted front-ends.
    spends: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable descriptor (including the params schema)."""
        return {
            "id": self.id,
            "summary": self.summary,
            "category": self.category,
            "mutates": self.mutates,
            "spends": self.spends,
            "params_schema": self.params_model.model_json_schema(),
        }


def _to_jsonable(value: Any) -> Any:
    """Coerce a handler result into something ``json.dumps`` can encode."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


class CommandRegistry:
    """An ordered-by-id collection of :class:`Command` definitions."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        """Register ``command``; raises :class:`DuplicateCommandError` on clash."""
        if command.id in self._commands:
            raise DuplicateCommandError(command.id)
        self._commands[command.id] = command
        return command

    def get(self, command_id: str) -> Command:
        """Return the command with ``command_id`` or raise :class:`UnknownCommandError`."""
        try:
            return self._commands[command_id]
        except KeyError:
            raise UnknownCommandError(command_id) from None

    def list_commands(self) -> list[Command]:
        """Return every registered command sorted by id."""
        return sorted(self._commands.values(), key=lambda command: command.id)

    def __contains__(self, command_id: object) -> bool:
        return command_id in self._commands

    def __len__(self) -> int:
        return len(self._commands)

    async def execute(
        self,
        command_id: str,
        params: dict[str, Any] | None = None,
        *,
        trust: TrustLevel = TrustLevel.CLI,
    ) -> Any:
        """Validate ``params`` and run the command, returning a JSON-safe result.

        ``trust`` is the front-end's policy (A10): an untrusted caller cannot
        choose ``config_path``, escape the data roots or use a path in a plan
        filename. MCP is read-only; the control channel may mutate but not
        spend. Handlers may be synchronous or asynchronous; sync ones run in
        ``asyncio.to_thread`` so a blocking command never stalls the caller's
        loop.

        Unknown ids, param-validation failures, permission refusals and handler
        exceptions are re-raised as typed :class:`CommandError` subclasses.
        """
        command = self.get(command_id)
        if trust is TrustLevel.MCP and (command.mutates or command.spends):
            raise CommandPermissionError(
                command_id, "MCP is read-only; use the guarded tool for this capability"
            )
        if trust is TrustLevel.CONTROL and command.spends:
            raise CommandPermissionError(command_id, "the control channel cannot approve spend")
        try:
            validate_untrusted_params(params or {}, trust)
        except TrustError as exc:
            raise CommandPermissionError(command_id, str(exc)) from exc

        try:
            validated = command.params_model.model_validate(params or {})
        except ValidationError as exc:
            raise CommandValidationError(command_id, str(exc)) from exc

        try:
            # Sync handlers do blocking work (ffprobe, QA, HyperFrames); keep
            # them off the event loop so an MCP stdio session stays responsive
            # (#299 / A9).
            if inspect.iscoroutinefunction(command.handler):
                result = command.handler(validated)
            else:
                result = await asyncio.to_thread(command.handler, validated)
            if inspect.isawaitable(result):
                result = await result
        except CommandError:
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced as a typed error
            raise CommandExecutionError(command_id, repr(exc)) from exc

        return _to_jsonable(result)


#: The process-wide registry that the CLI, control server, and MCP server share.
default_registry = CommandRegistry()


def register_command(
    *,
    id: str,
    summary: str,
    category: str,
    params_model: type[BaseModel],
    mutates: bool = False,
    spends: bool = False,
) -> Callable[[Callable[[Any], Any]], Callable[[Any], Any]]:
    """Decorator registering ``handler`` on :data:`default_registry`.

    ``id``/``summary``/``category``/``params_model`` mirror the :class:`Command`
    fields. ``mutates``/``spends`` let an untrusted front-end refuse the command
    before it runs (#299). The wrapped handler is returned unchanged so the
    decorated function stays directly callable (and easy to unit-test).

    >>> class EchoParams(BaseModel):
    ...     text: str
    >>> @register_command(id="demo.echo", summary="Echo", category="demo",
    ...                    params_model=EchoParams)
    ... def _echo(params: EchoParams) -> dict[str, str]:
    ...     return {"text": params.text}
    """

    def _decorator(handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
        default_registry.register(
            Command(
                id=id,
                summary=summary,
                category=category,
                params_model=params_model,
                handler=handler,
                mutates=mutates,
                spends=spends,
            )
        )
        return handler

    return _decorator
