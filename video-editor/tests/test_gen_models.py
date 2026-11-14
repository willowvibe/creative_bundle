"""Tests for the models.yaml -> pricing.yaml generation (single source of truth)."""

from __future__ import annotations

import yaml
from video_editor_cli.commands.gen_models import (
    MODELS_PATH,
    PRICING_PATH,
    generate_pricing,
)


def test_models_yaml_exists_and_is_canonical() -> None:
    assert MODELS_PATH.exists(), "models.yaml is the canonical cost registry"
    models = yaml.safe_load(MODELS_PATH.read_text(encoding="utf-8"))
    assert "providers" in models and models["providers"]


def test_pricing_yaml_is_generated_and_in_sync() -> None:
    """pricing.yaml must equal what `ve gen-models` would produce (no drift)."""
    models = yaml.safe_load(MODELS_PATH.read_text(encoding="utf-8"))
    generated = generate_pricing(models)
    committed = PRICING_PATH.read_text(encoding="utf-8")
    assert generated == committed, "run `ve gen-models` to regenerate pricing.yaml"


def test_generated_pricing_loads_into_registry() -> None:
    from video_editor_asset_ingestion.pricing import PricingRegistry

    registry = PricingRegistry.load()
    assert registry.last_updated == "2026-10-22"
    assert registry.get_model_cost("poyo_ai", "seedance-2-fast")["cost_per_second"] == 0.14
    assert (
        registry.get_model_cost("openrouter", "anthropic/claude-sonnet-5.5")["cost_per_1M_input"]
        == 2.0
    )
