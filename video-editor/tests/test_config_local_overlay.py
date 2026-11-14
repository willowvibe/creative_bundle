"""H5: config.local.yaml overlays the tracked config for all loaders."""

from __future__ import annotations

from pathlib import Path

from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_config import ProjectConfig
from video_editor_llm.config import LLMConfig


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n  default_provider: ollama\nassets:\n  strict: true\n  image_chain: []\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.yaml").write_text(
        "llm:\n  default_provider: openrouter\nassets:\n  strict: false\n",
        encoding="utf-8",
    )
    return path


def test_project_config_merges_the_local_overlay(tmp_path: Path) -> None:
    config = ProjectConfig.load(_write(tmp_path))

    assert config.llm.default_provider == "openrouter"
    assert config.assets.strict is False


def test_asset_and_llm_loaders_merge_the_same_overlay(tmp_path: Path) -> None:
    path = _write(tmp_path)

    assert AssetProviderConfig.load(path).strict is False
    assert LLMConfig.load(path).default_provider == "openrouter"
