"""A4: one resolved Settings object carries the project config."""

from __future__ import annotations

from pathlib import Path

from video_editor_config import Settings


def test_settings_loads_once_and_keeps_the_source_path(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("assets:\n  strict: false\n", encoding="utf-8")

    settings = Settings.load(path)

    assert settings.path == path
    assert settings.config.assets.strict is False
    assert Settings.load(path) is settings
