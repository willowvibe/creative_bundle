"""A4: all three loaders find the same config from a subdirectory."""

from __future__ import annotations

from pathlib import Path

from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_config import ProjectConfig
from video_editor_llm.config import LLMConfig


def _config(root: Path, provider: str) -> Path:
    path = root / "config.yaml"
    path.write_text(
        f"llm:\n  default_provider: {provider}\nassets:\n  strict: false\n",
        encoding="utf-8",
    )
    return path


def test_loaders_walk_up_from_a_subdirectory(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, "ollama")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("VIDEO_EDITOR_CONFIG", raising=False)

    assert ProjectConfig.load().llm.default_provider == "ollama"
    assert AssetProviderConfig.load().strict is False
    assert LLMConfig.load().default_provider == "ollama"


def test_video_editor_config_still_wins(tmp_path: Path, monkeypatch) -> None:
    _config(tmp_path, "ollama")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("llm:\n  default_provider: openrouter\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIDEO_EDITOR_CONFIG", str(explicit))

    assert ProjectConfig.load().llm.default_provider == "openrouter"
    assert LLMConfig.load().default_provider == "openrouter"
