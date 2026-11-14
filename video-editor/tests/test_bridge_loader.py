"""The editor bridges are optional: the CLI starts without them and a bridge command says what to install."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


# Setting a sys.modules entry to None makes any import of that module raise ModuleNotFoundError,
# which is what an install without the `bridges` extra looks like.
_HIDE_BRIDGES = """
import sys
sys.modules["video_editor_fablecut_bridge"] = None
sys.modules["video_editor_comfystudio_bridge"] = None
"""


def test_the_cli_starts_and_lists_commands_without_the_bridges():
    result = _run(
        _HIDE_BRIDGES
        + """
from typer.testing import CliRunner
from video_editor_cli.main import app
out = CliRunner().invoke(app, ["--help"])
print(out.exit_code)
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("0")


def test_a_bridge_command_without_the_bridge_prints_an_install_hint():
    result = _run(
        _HIDE_BRIDGES
        + """
import pytest, typer
from video_editor_cli.bridge_loader import FABLECUT, bridge
try:
    bridge(f"{FABLECUT}.exporter", "export_to_fablecut")
except typer.Exit as exc:
    print("exit", exc.exit_code)
"""
    )
    assert "exit 1" in result.stdout, result.stderr
    assert "video-editor-fablecut-bridge is not installed" in result.stderr
    assert "uv sync --all-packages" in result.stderr


def test_an_installed_bridge_loads_its_function():
    from video_editor_cli.bridge_loader import FABLECUT, bridge
    from video_editor_fablecut_bridge.exporter import export_to_fablecut

    assert bridge(f"{FABLECUT}.exporter", "export_to_fablecut") is export_to_fablecut
