"""Driftguard: inline model tables must match the canonical ``models.yaml``.

``models.yaml`` is the single source of truth for provider costs (generated into
``pricing.yaml``) plus the LLM task routing, estimate defaults, and template
quality tiers. These tables also live inline in code for zero-IO import; this
test fails if they drift from ``models.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS: dict[str, Any] = yaml.safe_load((REPO_ROOT / "models.yaml").read_text(encoding="utf-8"))


def test_llm_task_routing_in_sync() -> None:
    from video_editor_llm.router import ModelRouter

    # Back-compat map (the router fallback) equals the Ollama free-mode routing.
    assert MODELS["llm"]["tasks"] == ModelRouter.TASK_MODELS
    assert MODELS["llm"]["providers"]["ollama_cloud_pro"]["tasks"] == ModelRouter.TASK_MODELS


def test_openrouter_routing_matches_config_yaml() -> None:
    """models.yaml must agree with the active config.yaml OpenRouter routing."""
    config = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
    tasks = config["llm"]["tasks"]
    config_openrouter = {
        name: task["model"] for name, task in tasks.items() if task.get("provider") == "openrouter"
    }
    assert MODELS["llm"]["default_provider"] == config["llm"]["default_provider"]
    assert MODELS["llm"]["providers"]["openrouter"]["tasks"] == config_openrouter


def test_estimate_defaults_in_sync() -> None:
    from video_editor_pipelines.pricing import DEFAULT_PROVIDER_MODELS

    assert MODELS["estimate"]["defaults"] == DEFAULT_PROVIDER_MODELS


def test_template_tiers_in_sync() -> None:
    from video_editor_engine.planner.storyteller_planner import TEMPLATE_TIER_PRESETS

    assert {name: list(pair) for name, pair in TEMPLATE_TIER_PRESETS.items()} == MODELS[
        "templates"
    ]["tiers"]
