"""The Docker example in docs/PACKAGING.md copies every uv workspace member's manifest."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_docker_example_copies_every_workspace_member():
    members = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"][
        "members"
    ]
    doc = (ROOT / "docs" / "PACKAGING.md").read_text()
    missing = [m for m in members if f"COPY {m}/pyproject.toml {m}/" not in doc]
    assert not missing, f"add these to the Dockerfile in docs/PACKAGING.md: {missing}"
