"""Tests for runtime budget governance."""

from __future__ import annotations

import pytest
import yaml
from video_editor_asset_ingestion.budget import (
    BudgetExceededError,
    BudgetGovernor,
    BudgetSnapshot,
    estimate_cost,
)
from video_editor_asset_ingestion.pricing import PricingRegistry


def test_estimate_cost_known_provider() -> None:
    """Known provider/task pairs return a non-negative cost."""
    assert estimate_cost("stub_tts", "tts") == 0.0
    assert estimate_cost("elevenlabs_tts", "tts") > 0.0
    assert estimate_cost("poyo_ai", "image") > 0.0


def test_estimate_cost_unknown_provider_returns_zero() -> None:
    """Unknown providers/tasks default to zero-cost estimates."""
    assert estimate_cost("unknown", "image") == 0.0
    assert estimate_cost("fal_ai", "unknown") == 0.0


def test_unlimited_budget_allows_any_request() -> None:
    """A governor with no limit always approves allocations."""
    gov = BudgetGovernor()
    assert gov.can_afford("elevenlabs_tts", "tts") is True
    est = gov.allocate("elevenlabs_tts", "tts")
    assert est.estimated_cost > 0.0
    assert gov.spent == est.estimated_cost
    assert gov.remaining is None


def test_limited_budget_blocks_overrun() -> None:
    """A request that would exceed the limit raises BudgetExceededError."""
    gov = BudgetGovernor(limit=5.0)
    gov.allocate("fal_ai", "image")  # costs 3.0
    assert gov.remaining == 2.0
    with pytest.raises(BudgetExceededError, match="Budget exceeded"):
        gov.allocate("fal_ai", "image")  # another 3.0 would exceed 5.0


def test_limited_budget_allows_exact_fit() -> None:
    """A request that exactly fits the limit is allowed."""
    gov = BudgetGovernor(limit=3.0)
    est = gov.allocate("fal_ai", "image")
    assert gov.spent == 3.0
    assert gov.remaining == 0.0
    assert est.provider == "fal_ai"


def test_record_actual_updates_spent() -> None:
    """Recording an actual cost replaces the estimate in the ledger."""
    gov = BudgetGovernor(limit=50.0)
    est = gov.allocate("elevenlabs_tts", "tts")
    assert gov.spent == est.estimated_cost
    gov.record_actual(est, 12.5)
    assert gov.spent == 12.5
    assert est.actual_cost == 12.5


def test_record_actual_can_be_replaced() -> None:
    """Actual costs can be updated if provider metadata arrives late."""
    gov = BudgetGovernor()
    est = gov.allocate("elevenlabs_tts", "tts")
    gov.record_actual(est, 5.0)
    gov.record_actual(est, 6.0)
    assert gov.spent == 6.0


def test_free_requests_never_exhaust_budget() -> None:
    """Zero-cost providers do not consume a limited budget."""
    gov = BudgetGovernor(limit=1.0)
    for _ in range(10):
        gov.allocate("stub_tts", "tts")
    assert gov.spent == 0.0
    assert gov.remaining == 1.0


def test_snapshot_serializes_state() -> None:
    """snapshot() returns a simple dataclass view of the budget."""
    gov = BudgetGovernor(limit=20.0)
    gov.allocate("poyo_ai", "image")
    snap = gov.snapshot()
    assert isinstance(snap, BudgetSnapshot)
    assert snap.limit == 20.0
    assert snap.estimate_count == 1
    assert snap.spent > 0.0
    assert snap.estimated_remaining == 20.0 - snap.spent


def test_ledger_returns_defensive_copy() -> None:
    """ledger() does not expose internal mutable state."""
    gov = BudgetGovernor()
    gov.allocate("stub_tts", "tts")
    ledger = gov.ledger()
    ledger.clear()
    assert len(gov.ledger()) == 1


def test_custom_pricing_table() -> None:
    """A custom pricing table overrides the defaults."""
    pricing = {"tts": {"openai_tts": 99.0}}
    gov = BudgetGovernor(limit=100.0, pricing=pricing)
    est = gov.allocate("openai_tts", "tts")
    assert est.estimated_cost == 99.0
    assert gov.remaining == 1.0


def test_allocate_prices_the_requests_tier(tmp_path) -> None:
    """A 2K image reserves six credits, not the 1K rate (#269).

    The governor only ever saw provider/task/model, so a tiered provider was
    booked at its base rate and the persisted ``budget.json`` under-reported
    what the run had committed.
    """
    path = tmp_path / "pricing.yaml"
    path.write_text(
        yaml.dump(
            {
                "last_updated": "2026-10-22",
                "providers": {
                    "poyo_ai": {
                        "models": {
                            "gpt-image-2.5-fixed": {
                                "cost_per_image": 0.0085,
                                "currency": "USD",
                                "price_by": {"resolution": {"2K": {"cost_per_image": 0.03}}},
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    gov = BudgetGovernor(limit=1.0, registry=PricingRegistry.load(path))

    est = gov.allocate(
        "poyo_ai",
        "image",
        model="gpt-image-2.5-fixed",
        params={"model_kwargs": {"resolution": "2K"}},
    )

    assert est.estimated_cost == 0.03
    assert gov.spent == 0.03
