"""One config-file search order for the CLI, asset and LLM loaders (A4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from video_editor_core.constants import REPO_ROOT

CONFIG_ENV = "VIDEO_EDITOR_CONFIG"
CONFIG_NAME = "config.yaml"


def find_config_path(path: str | Path | None = None) -> Path | None:
    """Resolve a project config: explicit → env → cwd ancestors → repo root.

    An explicit path is returned even when it does not exist so the caller can
    report the missing file instead of silently substituting another config.
    """
    if path is not None:
        return Path(path).expanduser()
    env_path = os.getenv(CONFIG_ENV)
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return candidate
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidate = directory / CONFIG_NAME
        if candidate.exists():
            return candidate
    repo_config = REPO_ROOT / CONFIG_NAME
    return repo_config if repo_config.exists() else None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def load_yaml_with_overlay(path: Path | str) -> dict[str, Any]:
    """Read a YAML mapping and merge an adjacent ``config.local.yaml``.

    This is the single file-reading implementation for the project, asset and
    LLM loaders. It deliberately keeps the overlay next to the chosen config
    so a machine-local file never leaks into another checkout.
    """
    target = Path(path)
    with open(target, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config file {target} must contain a YAML mapping")
    overlay = target.with_name("config.local.yaml")
    if overlay.exists():
        with open(overlay, encoding="utf-8") as f:
            extra = yaml.safe_load(f) or {}
        if not isinstance(extra, dict):
            raise ValueError(f"config overlay {overlay} must contain a YAML mapping")
        _deep_merge(raw, extra)
    return raw
