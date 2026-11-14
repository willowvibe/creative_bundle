"""Repository-root constant shared by apps and packages."""

from __future__ import annotations

from pathlib import Path

#: ``packages/core/src/video_editor_core/constants.py`` -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
