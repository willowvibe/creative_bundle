"""Tests that config.yaml references only known models for each provider."""

from __future__ import annotations

from pathlib import Path

import yaml

INSTALLED_OLLAMA_MODELS: set[str] = {
    "minimax-m3:cloud",
    "kimi-k2.7-code:cloud",
    "glm-5.3:cloud",
    "glm-5.3-flash:cloud",
    "kimi-k3:cloud",
    "deepseek-v4.1-flash:cloud",
    "deepseek-v4-pro:cloud",
    "gemma4:cloud",
    "minimax-m3.1-flash:cloud",
}

# Static allow-list of OpenRouter model IDs referenced by the project. This is
# intentionally conservative so a typo in config.yaml fails the test.
KNOWN_OPENROUTER_MODELS: set[str] = {
    "anthropic/claude-sonnet-5.5",
    "google/gemini-3.8-flash",
}


def test_config_yaml_references_only_known_models() -> None:
    """Every LLM task in config.yaml must use a model known for its provider."""
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    tasks = (raw.get("llm") or {}).get("tasks", {})
    assert tasks, "no tasks were found in config.yaml"

    for name, task in tasks.items():
        model = task.get("model")
        provider = task.get("provider", "")
        assert model, f"task {name!r} has no model"

        if "ollama" in provider:
            assert model in INSTALLED_OLLAMA_MODELS, (
                f"config.yaml task {name!r} references unknown ollama model: {model}"
            )
        elif provider == "openrouter":
            assert model in KNOWN_OPENROUTER_MODELS, (
                f"config.yaml task {name!r} references unknown openrouter model: {model}"
            )
