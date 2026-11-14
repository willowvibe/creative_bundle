"""Tests that config.yaml registers the new storyteller providers."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_config_yaml_registers_storyteller_providers() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assets = raw.get("assets") or {}
    providers = assets.get("providers") or {}
    defaults = assets.get("defaults") or {}
    assert "poyo_music" in providers, "poyo_music must be configured under assets.providers"
    assert "sarvam_tts" in providers, "sarvam_tts must be configured under assets.providers"
    assert providers["poyo_music"].get("api_key_env") == "POYO_API_KEY"
    assert providers["sarvam_tts"].get("api_key_env") == "SARVAM_API"
    assert defaults.get("music") == "poyo_music"
    assert defaults.get("tts") == "tts_router"


def test_env_example_declares_sarvam_api() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8") if env_example.exists() else ""
    assert "SARVAM_API" in text
