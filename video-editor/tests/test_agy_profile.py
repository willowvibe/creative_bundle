"""#292: the restricted agy HOME and its PreToolUse guard."""

from __future__ import annotations

import json
import os
from pathlib import Path

from video_editor_asset_ingestion.providers.visual.agy_profile import (
    ensure_profile,
    guard_decision,
)


def _source_home(tmp_path: Path) -> Path:
    home = tmp_path / "real-home"
    gemini = home / ".gemini"
    gemini.mkdir(parents=True)
    (gemini / "oauth_creds.json").write_text("{}")
    (gemini / "google_accounts.json").write_text("{}")
    return home


def test_setup_writes_the_restricted_home(tmp_path: Path) -> None:
    profile = ensure_profile(tmp_path / "profile", source_home=_source_home(tmp_path))

    assert (profile / ".gemini" / "oauth_creds.json").exists()
    settings = json.loads((profile / ".gemini" / "antigravity-cli" / "settings.json").read_text())
    assert settings["allowNonWorkspaceAccess"] is False
    assert "command(*)" in settings["permissions"]["deny"]

    hooks = json.loads((profile / ".gemini" / "config" / "hooks.json").read_text())
    hook = hooks["video-editor-agy-guard"]["PreToolUse"][0]
    assert hook["matcher"] == "*"
    assert hook["hooks"][0]["command"] == str(profile / "agy_guard.py")
    assert os.access(profile / "agy_guard.py", os.X_OK)


def test_the_guard_allows_the_image_tool_and_workspace_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert guard_decision(
        {"workspacePaths": [str(workspace)], "toolCall": {"name": "generate_image"}}
    ) == {"decision": "allow"}
    assert guard_decision(
        {
            "workspacePaths": [str(workspace)],
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(workspace / "image.png")},
            },
        }
    ) == {"decision": "allow"}


def test_the_guard_denies_commands_and_writes_outside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    command = guard_decision(
        {
            "workspacePaths": [str(workspace)],
            "toolCall": {"name": "run_command", "args": {"CommandLine": "echo hi"}},
        }
    )
    assert command["decision"] == "deny"

    outside = guard_decision(
        {
            "workspacePaths": [str(workspace)],
            "toolCall": {
                "name": "write_file",
                "args": {"Path": str(tmp_path / "outside.png")},
            },
        }
    )
    assert outside["decision"] == "deny"
    assert "workspace" in outside["reason"]


def test_the_guard_allows_agy_15s_image_subagent_and_nothing_else(tmp_path: Path) -> None:
    """agy 1.5 delegates generate_image to an "Image Generator" subagent (2026-11-11)."""

    def decide(name: str, args: dict[str, str] | None = None) -> str:
        payload = {
            "workspacePaths": [str(tmp_path)],
            "toolCall": {"name": name, "args": args or {}},
        }
        return str(guard_decision(payload)["decision"])

    assert decide("invoke_subagent", {"Role": '"Image Generator"', "Prompt": "a cat"}) == "allow"
    assert decide("invoke_subagent", {"Role": "Researcher"}) == "deny"
    assert decide("invoke_subagent", {"Role": "image generator and shell helper"}) == "deny"
    for name in ("send_message", "receive_message"):
        assert decide(name) == "allow"
    assert decide("manage_subagents", {"Action": "create"}) == "deny"
    assert decide("run_command", {"CommandLine": "curl x"}) == "deny"


def test_the_installed_guard_script_matches_the_python_copy(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from video_editor_asset_ingestion.providers.visual.agy_profile import GUARD_SCRIPT

    script = tmp_path / "guard.py"
    script.write_text(GUARD_SCRIPT)
    for call in (
        {"name": "invoke_subagent", "args": {"Role": "Image Generator"}},
        {"name": "invoke_subagent", "args": {"Role": "Coder"}},
        {"name": "invoke_subagent", "args": {"Role": "image generator plus"}},
        {"name": "receive_message", "args": {}},
    ):
        payload = {"workspacePaths": [str(tmp_path)], "toolCall": call}
        out = subprocess.run(
            [sys.executable, str(script)], input=json.dumps(payload), capture_output=True, text=True
        )
        assert json.loads(out.stdout)["decision"] == guard_decision(payload)["decision"]
