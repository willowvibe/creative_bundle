"""Tests for the pricing registry module."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_llm_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)

SAMPLE_PRICING = {
    "last_updated": "2026-10-15",
    "providers": {
        "elevenlabs_tts": {
            "models": {
                "eleven_flash_v2_5": {"cost_per_1k_chars": 0.04, "currency": "USD"},
            }
        },
        "sarvam_tts": {
            "models": {
                "bulbul:v4-flash": {
                    "cost_per_1k_chars": 0.023,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
            }
        },
        "poyo_ai": {
            "models": {
                "gpt-image-2": {"cost_per_image": 0.010, "currency": "USD"},
                "seedance-2-fast": {"cost_per_second": 0.14, "currency": "USD"},
            }
        },
        "pexels": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "poyo_music": {
            "models": {
                "minimax-music-2.6": {
                    "cost_per_generation": 0.10,
                    "cost_per_second": 0.0017,
                    "currency": "USD",
                },
            }
        },
        "openrouter": {
            "models": {
                "anthropic/claude-sonnet-5.5": {
                    "cost_per_1M_input": 2.0,
                    "cost_per_1M_output": 10.0,
                }
            }
        },
    },
}


@pytest.fixture
def sample_pricing_file() -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(SAMPLE_PRICING, f)
    yield Path(f.name)
    Path(f.name).unlink()


class TestPricingRegistry:
    def test_load_from_yaml(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        assert registry.last_updated == "2026-10-15"
        assert "elevenlabs_tts" in registry.providers

    def test_get_model_cost_flat_provider(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = registry.get_model_cost("pexels", "default")
        assert cost == {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"}

    def test_get_model_cost_nested_models(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = registry.get_model_cost("elevenlabs_tts", "eleven_flash_v2_5")
        assert cost == {"cost_per_1k_chars": 0.04, "currency": "USD"}

    def test_get_model_cost_unknown_provider(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = registry.get_model_cost("nonexistent", "model")
        assert cost == {}

    def test_is_stale_fresh(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        registry.last_updated = datetime.now(UTC).strftime("%Y-%m-%d")
        assert not registry.is_stale(max_age_days=30)

    def test_is_stale_old(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        old_date = (datetime.now(UTC) - timedelta(days=45)).strftime("%Y-%m-%d")
        registry.last_updated = old_date
        assert registry.is_stale(max_age_days=30)

    def test_is_stale_missing_date(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        registry.last_updated = ""
        assert registry.is_stale(max_age_days=30)

    def test_default_pricing_fallback(self) -> None:
        """When pricing.yaml is missing, load built-in defaults."""
        registry = PricingRegistry.load(Path("/nonexistent/pricing.yaml"))
        assert "elevenlabs_tts" in registry.providers
        assert "poyo_ai" in registry.providers


class TestCostCalculators:
    def test_estimate_tts_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_tts_cost("elevenlabs_tts", "eleven_flash_v2_5", 500, registry)
        assert cost == 0.02  # 500/1000 * 0.04: ElevenLabs bills by the character

    def test_estimate_tts_cost_does_not_round_to_whole_blocks(
        self, sample_pricing_file: Path
    ) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_tts_cost("elevenlabs_tts", "eleven_flash_v2_5", 1001, registry)
        assert cost == 0.04  # 1001/1000 * 0.04, rounded to 4 places

    def test_per_call_tts_prices_every_request(self, tmp_path: Path) -> None:
        path = tmp_path / "pricing.yaml"
        path.write_text(
            yaml.dump(
                {
                    "providers": {
                        "sarvam_tts": {
                            "models": {
                                "bulbul:v4-flash": {
                                    "cost_per_1k_chars": 0.023,
                                    "billing_unit": "per_call",
                                }
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        registry = PricingRegistry.load(path)

        cost = estimate_tts_cost(
            "sarvam_tts",
            "bulbul:v4-flash",
            300,
            registry,
            char_counts=[100, 100, 100],
        )

        assert cost == pytest.approx(0.069)  # 3 calls * ceil(100/1000) * 0.023

    def test_price_requests_keeps_per_call_tts_billing(self, sample_pricing_file: Path) -> None:
        from video_editor_asset_ingestion.models import AssetRequest, AssetType
        from video_editor_pipelines.explainer_pipeline import price_requests

        registry = PricingRegistry.load(sample_pricing_file)
        requests = [
            AssetRequest(
                asset_id=f"tts-{i}",
                job_id="job",
                scene_id=f"scene-{i}",
                asset_type=AssetType.TTS,
                prompt="hello",
                provider="sarvam_tts",
                params={"model": "bulbul:v4-flash"},
                output_ext="mp3",
            )
            for i in range(3)
        ]

        items = price_requests(requests, registry)
        tts = next(item for item in items if item["task_type"] == "tts")

        assert tts["total_cost"] == pytest.approx(3 * 0.023)

    def test_estimate_image_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_image_cost("poyo_ai", "gpt-image-2", 5, registry)
        assert cost == 0.05  # 5 * 0.010

    def test_estimate_video_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_video_cost("poyo_ai", "seedance-2-fast", 10.0, registry)
        assert cost == 1.40  # 10 * 0.14

    def test_estimate_music_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        # PoYo Music bills per generation, so duration does not scale the cost.
        cost = estimate_music_cost("poyo_music", "minimax-music-2.6", 71.0, registry)
        assert cost == 0.10

    def test_estimate_llm_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_llm_cost("openrouter", "anthropic/claude-sonnet-5.5", 2000, 1000, registry)
        # (2000 * 2.0 + 1000 * 10.0) / 1_000_000 = (4000 + 10000) / 1_000_000 = 0.014
        assert cost == pytest.approx(0.014)

    def test_estimate_free_provider(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_image_cost("pexels", "default", 10, registry)
        assert cost == 0.0

    def test_estimate_unknown_provider_returns_zero(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_tts_cost("nonexistent", "model", 1000, registry)
        assert cost == 0.0


def test_model_equivalents_resolve_and_cheapest(tmp_path: Path) -> None:
    """Canonical model -> per-provider id, with cheapest-provider lookup."""
    path = tmp_path / "pricing.yaml"
    path.write_text(
        yaml.dump(
            {
                "last_updated": "2026-10-16",
                "providers": {
                    "sarvam_tts": {
                        "models": {
                            "bulbul:v4-flash": {
                                "cost_per_1k_chars": 0.023,
                                "billing_unit": "per_call",
                                "currency": "USD",
                            },
                        }
                    },
                    "poyo_ai": {"models": {"gpt-image-2": {"cost_per_image": 0.01}}},
                    "openrouter": {"models": {"openai/gpt-image-2": {"cost_per_image": 0.13}}},
                },
                "model_equivalents": {
                    "gpt-image-2": {
                        "poyo_ai": "gpt-image-2",
                        "openrouter": "openai/gpt-image-2",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    registry = PricingRegistry.load(path)

    assert registry.providers_for_model("gpt-image-2") == {
        "poyo_ai": "gpt-image-2",
        "openrouter": "openai/gpt-image-2",
    }
    assert registry.resolve_model("openrouter", "gpt-image-2") == "openai/gpt-image-2"
    assert registry.resolve_model("unknown_provider", "gpt-image-2") == "gpt-image-2"
    assert registry.unit_cost("poyo_ai", "gpt-image-2", "image") == 0.01
    assert registry.unit_cost("poyo_ai", "missing", "image") is None
    assert registry.cheapest_provider("gpt-image-2", "image") == (
        "poyo_ai",
        "gpt-image-2",
        0.01,
    )
    assert registry.cheapest_provider("no-such-model", "image") is None


TIERED_PRICING = {
    "last_updated": "2026-10-22",
    "providers": {
        "sarvam_tts": {
            "models": {
                "bulbul:v4-flash": {
                    "cost_per_1k_chars": 0.023,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
            }
        },
        "poyo_ai": {
            "models": {
                "gpt-image-2.5-fixed": {
                    "cost_per_image": 0.0085,
                    "currency": "USD",
                    "price_by": {
                        "resolution": {
                            "1K": {"cost_per_image": 0.0085},
                            "2K": {"cost_per_image": 0.03},
                            "4K": {"cost_per_image": 0.04},
                        }
                    },
                },
            }
        },
    },
}


@pytest.fixture
def tiered_registry(tmp_path: Path) -> PricingRegistry:
    path = tmp_path / "pricing.yaml"
    path.write_text(yaml.dump(TIERED_PRICING), encoding="utf-8")
    return PricingRegistry.load(path)


def test_estimate_image_cost_prices_the_requested_resolution(
    tiered_registry: PricingRegistry,
) -> None:
    """A 2K request bills six credits, not the 1K 1.7 (#269).

    PoYo's GPT Image 2.5 is priced per resolution tier; the registry stored one
    scalar, so the estimate and the running ledger booked a 2K render at the 1K
    rate (3.5x under the bill).
    """
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2.5-fixed",
        5,
        tiered_registry,
        params={"model_kwargs": {"resolution": "2K"}},
    )
    assert cost == 0.15  # 5 * 0.03


def test_estimate_image_cost_accepts_a_top_level_resolution(
    tiered_registry: PricingRegistry,
) -> None:
    """A caller may pass the tier key directly, without ``model_kwargs``."""
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2.5-fixed",
        1,
        tiered_registry,
        params={"resolution": "4k"},  # case-insensitive
    )
    assert cost == 0.04


def test_estimate_image_cost_defaults_to_the_base_rate_without_params(
    tiered_registry: PricingRegistry,
) -> None:
    """No tier in the request -> the model's own ``cost_per_image``."""
    assert estimate_image_cost("poyo_ai", "gpt-image-2.5-fixed", 2, tiered_registry) == 0.017


def test_estimate_image_cost_ignores_an_unknown_tier(
    tiered_registry: PricingRegistry,
) -> None:
    """An unpriced tier falls back to the base rate instead of $0.00."""
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2.5-fixed",
        1,
        tiered_registry,
        params={"model_kwargs": {"resolution": "8K"}},
    )
    assert cost == 0.0085


def test_unit_cost_resolves_a_price_variant(tiered_registry: PricingRegistry) -> None:
    """``unit_cost`` sees the tier too, so cheapest-provider stays honest."""
    assert (
        tiered_registry.unit_cost(
            "poyo_ai",
            "gpt-image-2.5-fixed",
            "image",
            params={"model_kwargs": {"resolution": "2K"}},
        )
        == 0.03
    )


def test_get_model_cost_keeps_currency_when_a_tier_applies(
    tiered_registry: PricingRegistry,
) -> None:
    """A variant overrides the rate, not the rest of the model's entry."""
    cost = tiered_registry.get_model_cost(
        "poyo_ai",
        "gpt-image-2.5-fixed",
        params={"model_kwargs": {"resolution": "2K"}},
    )
    assert cost["cost_per_image"] == 0.03
    assert cost["currency"] == "USD"


MATRIX_PRICING = {
    "last_updated": "2026-10-22",
    "providers": {
        "sarvam_tts": {
            "models": {
                "bulbul:v4-flash": {
                    "cost_per_1k_chars": 0.023,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
            }
        },
        "poyo_ai": {
            "models": {
                "gpt-image-2": {
                    "cost_per_image": 0.010,
                    "currency": "USD",
                    "price_by": {
                        "quality": {
                            "low": {
                                "cost_per_image": 0.010,
                                "price_by": {
                                    "resolution": {
                                        "1K": {"cost_per_image": 0.010},
                                        "2K": {"cost_per_image": 0.020},
                                        "4K": {"cost_per_image": 0.040},
                                    }
                                },
                            },
                            "medium": {
                                "cost_per_image": 0.042,
                                "price_by": {
                                    "resolution": {
                                        "1K": {"cost_per_image": 0.042},
                                        "2K": {"cost_per_image": 0.045},
                                        "4K": {"cost_per_image": 0.081},
                                    }
                                },
                            },
                            "high": {
                                "cost_per_image": 0.169,
                                "price_by": {
                                    "resolution": {
                                        "1K": {"cost_per_image": 0.169},
                                        "2K": {"cost_per_image": 0.178},
                                        "4K": {"cost_per_image": 0.321},
                                    }
                                },
                            },
                        }
                    },
                },
            }
        },
    },
}


@pytest.fixture
def matrix_registry(tmp_path: Path) -> PricingRegistry:
    path = tmp_path / "pricing.yaml"
    path.write_text(yaml.dump(MATRIX_PRICING), encoding="utf-8")
    return PricingRegistry.load(path)


def matrix_params(quality: str, resolution: str | None = None) -> dict[str, object]:
    kwargs: dict[str, object] = {"quality": quality}
    if resolution:
        kwargs["resolution"] = resolution
    return {"model_kwargs": kwargs}


def test_estimate_image_cost_prices_quality_and_resolution(
    matrix_registry: PricingRegistry,
) -> None:
    """A two-axis model bills the quality *and* the resolution tier (#270).

    ``gpt-image-2`` prices on a 3x3 quality x resolution matrix (high 4K is 64
    credits, $0.321) but the registry held only the 1K-low scalar, so a hero
    frame was estimated at 3% of its bill.
    """
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2",
        2,
        matrix_registry,
        params=matrix_params("high", "4K"),
    )
    assert cost == 0.642  # 2 * 0.321


def test_estimate_image_cost_keeps_the_quality_tier_without_a_resolution(
    matrix_registry: PricingRegistry,
) -> None:
    """Quality alone resolves to that quality's base rate, not the global one."""
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2",
        1,
        matrix_registry,
        params=matrix_params("high"),
    )
    assert cost == 0.169


def test_estimate_image_cost_falls_back_when_a_nested_tier_is_unknown(
    matrix_registry: PricingRegistry,
) -> None:
    """An unpriced resolution keeps the quality rate instead of dropping to 1K."""
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2",
        1,
        matrix_registry,
        params=matrix_params("medium", "8K"),
    )
    assert cost == 0.042


def test_nested_tiers_match_case_insensitively(matrix_registry: PricingRegistry) -> None:
    """Both axes accept the provider's own casing."""
    cost = estimate_image_cost(
        "poyo_ai",
        "gpt-image-2",
        1,
        matrix_registry,
        params=matrix_params("HIGH", "4k"),
    )
    assert cost == 0.321


def test_get_model_cost_keeps_currency_through_nested_tiers(
    matrix_registry: PricingRegistry,
) -> None:
    """Recursing through two tiers still leaves the model's other keys intact."""
    cost = matrix_registry.get_model_cost(
        "poyo_ai",
        "gpt-image-2",
        params=matrix_params("medium", "4K"),
    )
    assert cost["cost_per_image"] == 0.081
    assert cost["currency"] == "USD"


def test_estimate_image_cost_base_rate_covers_the_whole_matrix(
    matrix_registry: PricingRegistry,
) -> None:
    """No tier at all -> the model's scalar, as before."""
    assert estimate_image_cost("poyo_ai", "gpt-image-2", 3, matrix_registry) == 0.03


def test_recommended_poyo_image_models_are_priced() -> None:
    """Every image model the routing doc recommends must carry a price.

    ``docs/POYO_ROUTING.md`` recommends ``nano-banana-2-lite`` for budget work
    and lists ``nano-banana-2`` beside it, but neither existed in
    ``models.yaml``, so ``ve estimate`` priced them at $0.00 and the cost gate
    under-reported the spend (#262).
    """
    registry = PricingRegistry.load()
    for model in ("gpt-image-2", "nano-banana-2-lite", "nano-banana-2"):
        cost = registry.get_model_cost("poyo_ai", model)
        assert cost.get("cost_per_image", 0) > 0, f"{model} has no poyo_ai price"
    assert estimate_image_cost("poyo_ai", "nano-banana-2-lite", 10, registry) == 0.25
