"""Account files: one small YAML file per named account and platform.

An :class:`Account` records the platform's own account id after OAuth (a guard
against signing in to the wrong channel), the CDP port for the browser lane, and
whether the platform's API project has passed its compliance audit.
"""

from __future__ import annotations

import socket
from pathlib import Path

import yaml
from pydantic import BaseModel

from .paths import account_path, home

FIRST_CDP_PORT = 9333
# flow-mcp serves its own CDP endpoint on 9333; a running instance is detected
# by the port probe and skipped, so an account only takes 9333 when nothing owns it.
RESERVED_PORTS: frozenset[int] = frozenset()


class Account(BaseModel):
    """Everything the two lanes need to know about one named account."""

    platform: str
    name: str
    platform_account_id: str | None = None
    cdp_port: int = 0
    api_audited: bool = False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _account_files() -> list[Path]:
    return sorted(home().glob("*/accounts/*.yaml"))


def _used_ports() -> set[int]:
    ports: set[int] = set()
    for path in _account_files():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        port = data.get("cdp_port") if isinstance(data, dict) else None
        if isinstance(port, int):
            ports.add(port)
    return ports


def next_free_port() -> int:
    """Return the first CDP port not in any account and not already listening."""
    used = _used_ports() | set(RESERVED_PORTS)
    candidate = FIRST_CDP_PORT
    while candidate in used or _port_in_use(candidate):
        candidate += 1
    return candidate


def load_account(platform: str, name: str) -> Account:
    """Load an account, creating one (and reserving a CDP port) when absent."""
    path = account_path(platform, name)
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        return Account.model_validate({**data, "platform": platform, "name": name})
    account = Account(platform=platform, name=name, cdp_port=next_free_port())
    save_account(account)
    return account


def save_account(account: Account) -> None:
    """Write the account YAML under the shared state home."""
    path = account_path(account.platform, account.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(account.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
