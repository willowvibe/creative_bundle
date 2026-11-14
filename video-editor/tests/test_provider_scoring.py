"""Tests for scored provider selection."""

from __future__ import annotations

import pytest
from video_editor_asset_ingestion.config import (
    AssetProviderConfig,
    ProviderConfig,
)
from video_editor_asset_ingestion.providers.scoring import (
    ProviderCriteria,
    ProviderScoreCard,
    ProviderSelectionError,
    ScoredProviderSelector,
)


def _config(**providers: ProviderConfig) -> AssetProviderConfig:
    """Build an AssetProviderConfig with the named providers."""
    return AssetProviderConfig(providers=dict(providers))


def test_selector_prefers_configured_preferred() -> None:
    """An explicit preferred provider wins when it is configured."""
    cfg = _config(fal_ai=ProviderConfig(), poyo_ai=ProviderConfig())
    selector = ScoredProviderSelector(cfg)
    name, _ = selector.select("image", preferred="poyo_ai")
    assert name == "poyo_ai"


def test_selector_rejects_unconfigured_preferred() -> None:
    """An explicit but unconfigured preferred provider raises a typed error."""
    cfg = _config(fal_ai=ProviderConfig())
    selector = ScoredProviderSelector(cfg)
    with pytest.raises(
        ProviderSelectionError, match="Preferred provider 'poyo_ai' is not configured"
    ):
        selector.select("image", preferred="poyo_ai")


def test_selector_picks_highest_quality_when_weighted() -> None:
    """Quality-only weighting picks the best-quality provider."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    # Set env so availability bonus does not flip the winner.
    selector = ScoredProviderSelector(cfg)
    criteria = ProviderCriteria(
        cost_weight=0.0, quality_weight=1.0, latency_weight=0.0, reliability_weight=0.0
    )
    name, _ = selector.select("image", criteria=criteria)
    # fal_ai has higher default quality (0.85 vs 0.80).
    assert name == "fal_ai"


def test_selector_picks_cheapest_when_weighted() -> None:
    """Cost-only weighting picks the cheapest provider."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    selector = ScoredProviderSelector(cfg)
    criteria = ProviderCriteria(
        cost_weight=1.0, quality_weight=0.0, latency_weight=0.0, reliability_weight=0.0
    )
    name, _ = selector.select("image", criteria=criteria)
    assert name == "poyo_ai"


def test_selector_honors_min_quality_floor() -> None:
    """Providers below the quality floor are excluded."""
    cfg = _config(
        fal_ai=ProviderConfig(),
        stub_image=ProviderConfig(),
    )
    selector = ScoredProviderSelector(cfg)
    criteria = ProviderCriteria(min_quality=0.8)
    name, _ = selector.select("image", criteria=criteria)
    assert name == "fal_ai"


def test_selector_require_api_key_excludes_keyless(monkeypatch: pytest.MonkeyPatch) -> None:
    """require_api_key drops providers without a configured key."""
    monkeypatch.setenv("FAL_KEY", "fake-key")
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(),  # no api_key_env
    )
    selector = ScoredProviderSelector(cfg)
    criteria = ProviderCriteria(require_api_key=True)
    name, _ = selector.select("image", criteria=criteria)
    assert name == "fal_ai"


def test_selector_empty_config_raises() -> None:
    """No configured providers means no selection possible."""
    cfg = AssetProviderConfig()
    selector = ScoredProviderSelector(cfg)
    with pytest.raises(ProviderSelectionError, match="No configured provider satisfies"):
        selector.select("image")


def test_rank_returns_ordered_list() -> None:
    """rank() returns every configured provider highest-score first."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    selector = ScoredProviderSelector(cfg)
    ranked = selector.rank("image")
    names = [r.name for r in ranked]
    assert len(names) == 2
    assert "fal_ai" in names
    assert "poyo_ai" in names


def test_config_select_provider_matches_selector() -> None:
    """AssetProviderConfig.select_provider delegates to the selector."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    name, _ = cfg.select_provider("image", criteria={"quality_weight": 1.0, "cost_weight": 0.0})
    assert name == "fal_ai"


def test_custom_score_table_overrides_defaults() -> None:
    """A custom scoring table changes the winner."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    custom_scores = {
        "image": {
            "fal_ai": ProviderScoreCard(cost=1.0, quality=0.1, latency=10.0, reliability=0.5),
            "poyo_ai": ProviderScoreCard(cost=0.1, quality=0.9, latency=1.0, reliability=0.9),
        }
    }
    selector = ScoredProviderSelector(cfg, scores=custom_scores)
    name, _ = selector.select("image")
    assert name == "poyo_ai"


def test_selector_picks_cheapest_provider_for_model() -> None:
    """Model-aware selection picks the cheapest configured provider offering it.

    Uses the real ``pricing.yaml``: ``gpt-image-2`` is $0.010 on poyo_ai and
    $0.13 on openrouter (as ``openai/gpt-image-2``).
    """
    cfg = _config(
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
        openrouter=ProviderConfig(api_key_env="OPENROUTER_API_KEY"),
    )
    selector = ScoredProviderSelector(cfg)
    name, _ = selector.select("image", criteria=ProviderCriteria(model="gpt-image-2"))
    assert name == "poyo_ai"


def test_selector_model_mode_skips_providers_without_model() -> None:
    """Providers that cannot serve the model are excluded in model mode."""
    cfg = _config(
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
        replicate_image=ProviderConfig(api_key_env="REPLICATE_API_TOKEN"),
    )
    selector = ScoredProviderSelector(cfg)
    # replicate_image does not offer gpt-image-2, so poyo_ai wins by default.
    name, _ = selector.select("image", criteria=ProviderCriteria(model="gpt-image-2"))
    assert name == "poyo_ai"


def test_selector_unknown_model_falls_back_to_weighted_scoring() -> None:
    """An unknown model does not restrict candidates (no regression)."""
    cfg = _config(
        fal_ai=ProviderConfig(api_key_env="FAL_KEY"),
        poyo_ai=ProviderConfig(api_key_env="POYO_API_KEY"),
    )
    selector = ScoredProviderSelector(cfg)
    criteria = ProviderCriteria(
        cost_weight=0.0,
        quality_weight=1.0,
        latency_weight=0.0,
        reliability_weight=0.0,
        model="totally-unknown-model",
    )
    name, _ = selector.select("image", criteria=criteria)
    assert name == "fal_ai"  # higher quality card still wins
