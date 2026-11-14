"""Trust policy shared by every front-end of the command registry (A10).

The registry is reachable from the CLI (trusted), the JSON-lines control
channel and MCP (both untrusted). Instead of each front-end re-implementing
containment and permission checks, the registry applies this policy:

* an untrusted caller cannot choose ``config_path`` (it selects providers and
  keys), point ``work_root`` / ``output_root`` outside the data roots, or use a
  plan filename with path separators;
* MCP may run read-only commands only — a command that mutates or spends must
  use a guarded tool;
* the control channel may mutate, but spending commands are refused (there is
  no budget-bearing parameter on that surface).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

from video_editor_state.paths import default_output_root, default_work_root

__all__ = [
    "TrustError",
    "TrustLevel",
    "contained_root",
    "plan_filename",
    "validate_untrusted_params",
]


class TrustLevel(StrEnum):
    """Who is calling the registry."""

    CLI = "cli"
    CONTROL = "control"
    MCP = "mcp"

    @property
    def is_trusted(self) -> bool:
        return self is TrustLevel.CLI


class TrustError(ValueError):
    """A parameter an untrusted caller is not allowed to supply."""


def contained_root(
    value: str | Path,
    roots: tuple[Path, ...] | None = None,
    label: str = "root",
) -> Path:
    """Resolve ``value`` and require it to sit inside one of ``roots``."""
    allowed = roots or (
        Path(default_work_root()).resolve(),
        Path(default_output_root()).resolve(),
    )
    path = Path(value).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in allowed):
        raise TrustError(
            f"{label} must be inside the configured data roots {[str(r) for r in allowed]}; got {path}"
        )
    return path


def plan_filename(value: str | Path, label: str = "plan_name") -> str:
    """Require a bare filename for a plan read/write target."""
    name = str(value)
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise TrustError(f"{label} must be a bare filename, got {name!r}")
    return name


def validate_untrusted_params(params: Mapping[str, object], trust: TrustLevel) -> None:
    """Apply the containment/config policy before a command runs."""
    if trust.is_trusted:
        return
    if params.get("config_path"):
        raise TrustError(
            "config_path is not accepted from an untrusted caller; the server "
            "chooses the configuration so providers and keys cannot be redirected"
        )
    for key in ("work_root", "output_root"):
        value = params.get(key)
        if value:
            contained_root(str(value), label=key)
    for key in ("plan_name", "plan_out_name"):
        value = params.get(key)
        if value:
            plan_filename(str(value), label=key)
