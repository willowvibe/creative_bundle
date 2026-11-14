"""Filesystem locations for social-engine state.

State lives under ``SOCIAL_ENGINE_HOME`` when set, otherwise
``~/.config/social-engine``. The layout is shared by every platform::

    <home>/<platform>/client_secret.json
    <home>/<platform>/tokens/<account>.json
    <home>/<platform>/accounts/<account>.yaml
    <home>/browser/<platform>-<account>/
    <home>/logs/
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _slug(value: str, kind: str) -> str:
    """Validate a platform or account name so it cannot escape the home."""
    if not _NAME_RE.fullmatch(value):
        raise ValueError(
            f"invalid {kind} name {value!r}; use lowercase letters, digits, dots, "
            "underscores and hyphens"
        )
    return value


def home() -> Path:
    """Return the state directory, honouring ``SOCIAL_ENGINE_HOME``."""
    override = os.environ.get("SOCIAL_ENGINE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "social-engine"


def ensure_home() -> Path:
    """Create the state directory (mode 0700) and return it."""
    root = home()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def platform_dir(platform: str) -> Path:
    """Directory holding one platform's secrets, tokens and accounts."""
    return home() / _slug(platform, "platform")


def client_secret_path(platform: str) -> Path:
    """The downloaded OAuth client secret for one platform."""
    return platform_dir(platform) / "client_secret.json"


def token_path(platform: str, account: str) -> Path:
    """OAuth token JSON for one named account."""
    return platform_dir(platform) / "tokens" / f"{_slug(account, 'account')}.json"


def account_path(platform: str, account: str) -> Path:
    """Account YAML for one named account."""
    return platform_dir(platform) / "accounts" / f"{_slug(account, 'account')}.yaml"


def chrome_profile_dir(platform: str, account: str) -> Path:
    """Dedicated Chrome user-data directory used by the browser lane."""
    return home() / "browser" / f"{_slug(platform, 'platform')}-{_slug(account, 'account')}"


def logs_dir() -> Path:
    """Where browser flows save screenshots and other diagnostics."""
    return home() / "logs"
