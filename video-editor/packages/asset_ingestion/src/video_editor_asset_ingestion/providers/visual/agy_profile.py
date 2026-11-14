"""Restricted home/profile for the local ``agy`` image agent (#292, §5).

``agy`` has no flag for a separate settings directory, but it resolves all of
its state from ``HOME`` (verified against 1.4.8 with ``strace``). Point the
image child at a dedicated home containing:

* auth sym links to the real ``~/.gemini`` credentials (quota still works);
* a ``settings.json`` that denies shell commands;
* a ``PreToolUse`` hook that denies every tool except ``generate_image``,
  ``finish``, and a file write inside the turn's workspace.

The hook is the enforced boundary: ``--sandbox`` only covers terminal
commands, and a plain settings ``write_file(*)`` rule still allowed writes
outside the workspace during the 1.4.8 spike.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_DIR = "~/.config/video-editor/agy-profile"
HOOK_NAME = "video-editor-agy-guard"

#: Tools whose file argument is allowed only inside the turn workspace.
WRITE_TOOLS = {"write_file", "replace_file_content", "multi_replace_file_content", "sed_file"}
#: Tools the image turn genuinely needs.
ALLOWED_TOOLS = {"generate_image", "finish"}
#: agy 1.5 runs generate_image inside an "Image Generator" subagent and talks to it by message
#: (2026-11-11). Messaging touches no files; invoke_subagent is allowed for the image role only.
MESSAGE_TOOLS = {"send_message", "receive_message"}
#: invoke_subagent is allowed for exactly these roles (normalised: lower case, single spaces).
IMAGE_SUBAGENT_ROLES = {"image generator", "image-generator", "imagegenerator"}

AUTH_FILES = (
    "oauth_creds.json",
    "google_accounts.json",
    "installation_id",
    "state.json",
)

GUARD_SCRIPT = '''\
#!/usr/bin/env python3
"""PreToolUse guard installed by `ve agy-setup`."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WRITE_TOOLS = {"write_file", "replace_file_content", "multi_replace_file_content", "sed_file"}
ALLOWED_TOOLS = {"generate_image", "finish"}
#: agy 1.5 runs generate_image inside an "Image Generator" subagent and talks to it by message
#: (2026-11-11). Messaging touches no files; invoke_subagent is allowed for the image role only.
MESSAGE_TOOLS = {"send_message", "receive_message"}
#: invoke_subagent is allowed for exactly these roles (normalised: lower case, single spaces).
IMAGE_SUBAGENT_ROLES = {"image generator", "image-generator", "imagegenerator"}


def _workspace(payload: dict) -> Path | None:
    paths = payload.get("workspacePaths")
    if not isinstance(paths, list) or not paths:
        return None
    try:
        return Path(str(paths[0])).resolve()
    except OSError:
        return None


def _inside(path: str, workspace: Path | None) -> bool:
    if workspace is None:
        return False
    try:
        return Path(path).expanduser().resolve().is_relative_to(workspace)
    except (OSError, RuntimeError):
        return False


def decide(payload: dict) -> dict:
    call = payload.get("toolCall")
    call = call if isinstance(call, dict) else {}
    name = str(call.get("name") or "")
    args = call.get("args")
    args = args if isinstance(args, dict) else {}
    workspace = _workspace(payload)

    if name in ALLOWED_TOOLS or name in MESSAGE_TOOLS:
        return {"decision": "allow"}
    if name == "invoke_subagent":
        role = " ".join(str(args.get("Role") or args.get("role") or "").strip().strip('"').lower().split())
        if role in IMAGE_SUBAGENT_ROLES:
            return {"decision": "allow"}
        return {
            "decision": "deny",
            "reason": "video-editor agy profile: only an image-generator subagent is permitted",
        }
    if name in WRITE_TOOLS:
        for key in ("Path", "path", "TargetFile", "target_file", "File", "file"):
            value = args.get(key)
            if isinstance(value, str) and _inside(value, workspace):
                return {"decision": "allow"}
        return {
            "decision": "deny",
            "reason": "video-editor agy profile: writes are confined to the turn workspace",
        }
    return {
        "decision": "deny",
        "reason": f"video-editor agy profile: tool {name!r} is not permitted",
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        decision = decide(payload if isinstance(payload, dict) else {})
    except Exception as exc:  # noqa: BLE001 - fail closed: deny the tool call
        decision = {"decision": "deny", "reason": f"video-editor agy guard failed: {exc}"}
    sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def profile_path(profile_dir: str | Path | None = None) -> Path:
    """Resolve the profile home, expanding ``~``."""
    return Path(profile_dir or DEFAULT_PROFILE_DIR).expanduser().resolve()


def profile_env(profile_dir: str | Path) -> dict[str, str]:
    """Environment for an ``agy`` child: isolated HOME, same PATH/network."""
    env = dict(os.environ)
    env["HOME"] = str(profile_path(profile_dir))
    # Keep the agent out of the real XDG state as well.
    for key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        env.pop(key, None)
    return env


def _link_auth(profile: Path, source_home: Path) -> list[str]:
    """Symlink real credentials into the profile; return missing file names."""
    missing: list[str] = []
    for name in AUTH_FILES:
        source = source_home / ".gemini" / name
        target = profile / ".gemini" / name
        if source.exists():
            if not target.exists():
                target.symlink_to(source)
        else:
            missing.append(name)
    return missing


def ensure_profile(
    profile_dir: str | Path | None = None,
    *,
    source_home: str | Path | None = None,
) -> Path:
    """Create or refresh the restricted ``agy`` home and return it."""
    profile = profile_path(profile_dir)
    gemini = profile / ".gemini"
    cli = gemini / "antigravity-cli"
    config = gemini / "config"
    for directory in (profile, gemini, cli, config):
        directory.mkdir(parents=True, exist_ok=True)

    guard = profile / "agy_guard.py"
    guard.write_text(GUARD_SCRIPT, encoding="utf-8")
    guard.chmod(guard.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    hooks = {
        HOOK_NAME: {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(guard),
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    }
    (config / "hooks.json").write_text(json.dumps(hooks, indent=2), encoding="utf-8")

    settings = {
        "allowNonWorkspaceAccess": False,
        "permissions": {
            "allow": ["generate_image", "write_file(*)", "finish"],
            # The hook is authoritative; these keep a missing hook from
            # silently restoring the user's global command(*) policy.
            "deny": ["command(*)", "run_command(*)"],
        },
    }
    (cli / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")

    source = Path(source_home).expanduser() if source_home else Path.home()
    _link_auth(profile, source)
    return profile


def auth_ready(profile_dir: str | Path | None = None) -> bool:
    """True when the profile has an OAuth credential link."""
    return (profile_path(profile_dir) / ".gemini" / "oauth_creds.json").exists()


def guard_decision(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Python copy of the installed guard, for tests and diagnostics."""
    call = payload.get("toolCall")
    call_dict = call if isinstance(call, dict) else {}
    name = str(call_dict.get("name") or "")
    args = call_dict.get("args")
    args_dict = args if isinstance(args, dict) else {}
    paths = payload.get("workspacePaths")
    workspace: Path | None = None
    if isinstance(paths, list) and paths:
        try:
            workspace = Path(str(paths[0])).resolve()
        except OSError:
            workspace = None
    if name in ALLOWED_TOOLS or name in MESSAGE_TOOLS:
        return {"decision": "allow"}
    if name == "invoke_subagent":
        role = " ".join(
            str(args_dict.get("Role") or args_dict.get("role") or "")
            .strip()
            .strip('"')
            .lower()
            .split()
        )
        if role in IMAGE_SUBAGENT_ROLES:
            return {"decision": "allow"}
        return {
            "decision": "deny",
            "reason": "video-editor agy profile: only an image-generator subagent is permitted",
        }
    if name in WRITE_TOOLS:
        for key in ("Path", "path", "TargetFile", "target_file", "File", "file"):
            value = args_dict.get(key)
            if isinstance(value, str) and workspace is not None:
                try:
                    if Path(value).expanduser().resolve().is_relative_to(workspace):
                        return {"decision": "allow"}
                except (OSError, RuntimeError):
                    pass
        return {
            "decision": "deny",
            "reason": "video-editor agy profile: writes are confined to the turn workspace",
        }
    return {
        "decision": "deny",
        "reason": f"video-editor agy profile: tool {name!r} is not permitted",
    }
