"""Provider model pricing registry with freshness checks.

Loads model-level costs from ``pricing.yaml``. Falls back to built-in
defaults when the file is missing. Emits a warning when pricing data is
older than 30 days.
"""

from __future__ import annotations

import copy
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
PRICING_PATH_ENV = "VIDEO_EDITOR_PRICING"
MODELS_PATH_ENV = "VIDEO_EDITOR_MODELS"


def _package_data(name: str) -> Path:
    return Path(str(files("video_editor_asset_ingestion").joinpath("data", name)))


def default_pricing_path() -> Path:
    override = os.getenv(PRICING_PATH_ENV)
    if override:
        return Path(override)
    repo = REPO_ROOT / "pricing.yaml"
    return repo if repo.exists() else _package_data("pricing.yaml")


def default_models_path() -> Path:
    override = os.getenv(MODELS_PATH_ENV)
    if override:
        return Path(override)
    repo = REPO_ROOT / "models.yaml"
    return repo if repo.exists() else _package_data("models.yaml")


DEFAULT_PRICING_PATH = default_pricing_path()
DEFAULT_MAX_AGE_DAYS = 30

# Cost keys compared per asset type when choosing the cheapest provider for a
# model. Order matters: the first present key wins.
_ASSET_COST_KEYS: dict[str, tuple[str, ...]] = {
    "image": ("cost_per_image", "cost_per_asset"),
    "data_viz": ("cost_per_image", "cost_per_asset"),
    "video": ("cost_per_second", "cost_per_asset"),
    "music": ("cost_per_generation", "cost_per_second"),
    "sfx": ("cost_per_second",),
    "tts": ("cost_per_1k_chars",),
    "alignment": ("cost_per_second",),
    "isolation": ("cost_per_second",),
    "speech_to_speech": ("cost_per_second",),
}

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "last_updated": "2026-10-22",
    "providers": {
        # ---- TTS ----
        "elevenlabs_tts": {
            "models": {
                "eleven_v4": {"cost_per_1k_chars": 0.08, "currency": "USD"},
                "eleven_v3": {"cost_per_1k_chars": 0.08, "currency": "USD"},
                "eleven_v3_conversational": {"cost_per_1k_chars": 0.04, "currency": "USD"},
                "eleven_multilingual_v2": {"cost_per_1k_chars": 0.08, "currency": "USD"},
                "eleven_flash_v2_5": {"cost_per_1k_chars": 0.04, "currency": "USD"},
                "eleven_turbo_v2_5": {"cost_per_1k_chars": 0.04, "currency": "USD"},
                "eleven_v4_turbo": {"cost_per_1k_chars": 0.04, "currency": "USD"},
            }
        },
        "openai_tts": {
            "models": {
                "tts-1": {"cost_per_1k_chars": 0.015, "currency": "USD"},
                "tts-1-hd": {"cost_per_1k_chars": 0.03, "currency": "USD"},
                "gpt-4o-mini-tts": {"cost_per_1k_chars": 0.015, "currency": "USD"},
            }
        },
        "sarvam_tts": {
            "models": {
                "bulbul:v4-flash": {
                    "cost_per_1k_chars": 0.023,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
                "bulbul:v3": {
                    "cost_per_1k_chars": 0.034,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
                "bulbul:v2": {
                    "cost_per_1k_chars": 0.034,
                    "billing_unit": "per_call",
                    "currency": "USD",
                },
            }
        },
        "sarvam_clone": {
            "models": {
                "voice-clone": {
                    "cost_per_1k_chars": 0.023,
                    "billing_unit": "per_call",
                    "currency": "USD",
                }
            }
        },
        "kokoro_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        "kitten_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        "moss_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        # ---- image + video ----
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
                "z-image": {"cost_per_image": 0.010, "currency": "USD"},
                "nano-banana-2-api": {"cost_per_image": 0.040, "currency": "USD"},
                "seedream-5-0-lite-api": {"cost_per_image": 0.025, "currency": "USD"},
                "nano-banana-2-1": {"cost_per_image": 0.020, "currency": "USD"},
                "seedream-5-0-flash": {"cost_per_image": 0.020, "currency": "USD"},
                "flux-3-klein": {"cost_per_image": 0.009, "currency": "USD"},
                "seedance-2-mini": {"cost_per_second": 0.12, "currency": "USD"},
                "nano-banana-2-lite": {"cost_per_image": 0.025, "currency": "USD"},
                "nano-banana-2": {"cost_per_image": 0.040, "currency": "USD"},
                "flux-schnell": {"cost_per_image": 0.0024, "currency": "USD"},
                "seedance-2-fast": {"cost_per_second": 0.14, "currency": "USD"},
                "seedance-2": {"cost_per_second": 0.20, "currency": "USD"},
                "seedance-2-5": {"cost_per_second": 0.315, "currency": "USD"},
                "gemini-omni-1-1-flash": {"cost_per_second": 0.056, "currency": "USD"},
                "veo-3-1-official": {"cost_per_second": 0.018, "currency": "USD"},
                "minimax-h3-max-turbo": {"cost_per_second": 0.040, "currency": "USD"},
                "wan2.2-image-to-video-fast": {
                    "cost_per_generation": 0.06,
                    "currency": "USD",
                    "price_by": {
                        "resolution": {
                            "480p": {"cost_per_generation": 0.03},
                            "720p": {"cost_per_generation": 0.06},
                        }
                    },
                },
            }
        },
        "fal_ai": {
            "models": {
                "fal-ai/flux/dev": {"cost_per_image": 0.025, "currency": "USD"},
                "fal-ai/flux-pro": {"cost_per_image": 0.05, "currency": "USD"},
                "blackforestlabs/flux-3": {"cost_per_image": 0.048, "currency": "USD"},
                "fal-ai/nano-banana-2": {"cost_per_image": 0.08, "currency": "USD"},
                "fal-ai/z-image/turbo": {"cost_per_image": 0.005, "currency": "USD"},
                "bytedance/seedance-2.0/mini/text-to-video": {
                    "cost_per_second": 0.155,
                    "currency": "USD",
                },
            }
        },
        "replicate_image": {
            "models": {
                "black-forest-labs/flux-schnell": {"cost_per_image": 0.003, "currency": "USD"},
                "black-forest-labs/flux-dev": {"cost_per_image": 0.025, "currency": "USD"},
                "black-forest-labs/flux-1.1-pro": {"cost_per_image": 0.04, "currency": "USD"},
            }
        },
        # ---- music + sfx ----
        "poyo_music": {
            "models": {
                "minimax-music-2.6": {
                    "cost_per_generation": 0.10,
                    "cost_per_second": 0.0017,
                    "currency": "USD",
                },
                "generate-music": {
                    "cost_per_generation": 0.10,
                    "cost_per_second": 0.0017,
                    "currency": "USD",
                },
            }
        },
        "elevenlabs_music": {
            "models": {
                "eleven_music_v1": {"cost_per_second": 0.0025, "currency": "USD"},
            }
        },
        "elevenlabs_sfx": {
            "models": {
                "default": {"cost_per_second": 0.002, "currency": "USD"},
            }
        },
        "elevenlabs_isolation": {
            "models": {
                "default": {"cost_per_second": 0.002, "currency": "USD"},
            }
        },
        "elevenlabs_speech_to_speech": {
            "models": {
                "eleven_english_sts_v2": {"cost_per_second": 0.002, "currency": "USD"},
            }
        },
        "elevenlabs_forced_alignment": {
            "models": {
                "default": {"cost_per_second": 0.0, "currency": "USD"},
            }
        },
        "elevenlabs_clone": {
            "models": {
                "default": {"cost_per_sample": 0.0, "currency": "USD"},
            }
        },
        "ace_step": {"cost_per_second": 0.0, "currency": "USD", "note": "local"},
        # ---- stock (free) ----
        "pexels": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "local_media": {"cost_per_asset": 0.0, "currency": "USD", "note": "own file"},
        "pixabay": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "coverr": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "mixkit": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        # ---- local ----
        "hyperframes_video": {"cost_per_second": 0.0, "currency": "USD", "note": "local render"},
        "matplotlib_data_viz": {"cost_per_image": 0.0, "currency": "USD", "note": "local"},
        # ---- LLM ----
        "openrouter": {
            "models": {
                "anthropic/claude-sonnet-5.5": {
                    "cost_per_1M_input": 2.0,
                    "cost_per_1M_output": 10.0,
                },
                "google/gemini-3.8-flash": {
                    "cost_per_1M_input": 0.825,
                    "cost_per_1M_output": 4.125,
                },
                # ---- generation (image / video / music) ----
                "openai/gpt-image-2": {"cost_per_image": 0.13, "currency": "USD"},
                "google/gemini-3-pro-image": {"cost_per_image": 0.155, "currency": "USD"},
                "bytedance-seed/seedream-5-0-pro": {
                    "cost_per_image": 0.045,
                    "currency": "USD",
                },
                "black-forest-labs/flux-3-image": {
                    "cost_per_image": 0.041,
                    "currency": "USD",
                },
                "google/veo-3.1": {"cost_per_second": 0.40, "currency": "USD"},
                "google/veo-3.1-fast": {"cost_per_second": 0.12, "currency": "USD"},
                "google/veo-3.1-lite": {"cost_per_second": 0.08, "currency": "USD"},
                "kwaivgi/kling-v3.0-pro": {"cost_per_second": 0.112, "currency": "USD"},
                "kwaivgi/kling-v3.0-std": {"cost_per_second": 0.084, "currency": "USD"},
                "minimax/hailuo-3": {"cost_per_second": 0.13, "currency": "USD"},
                "alibaba/wan-3.0": {"cost_per_second": 0.10, "currency": "USD"},
                "runway/gen-4.5": {"cost_per_second": 0.12, "currency": "USD"},
                "google/lyria-3-pro-preview": {
                    "cost_per_generation": 0.0,
                    "currency": "USD",
                    "note": "preview; pricing not published",
                },
            }
        },
        "ollama_cloud_pro": {
            "models": {
                "minimax-m3:cloud": {"cost_per_1M_input": 0.60, "cost_per_1M_output": 2.40},
                "glm-5.3:cloud": {"cost_per_1M_input": 1.40, "cost_per_1M_output": 4.40},
                "glm-5.3-flash:cloud": {"cost_per_1M_input": 0.15, "cost_per_1M_output": 0.50},
                "kimi-k2.7-code:cloud": {"cost_per_1M_input": 0.95, "cost_per_1M_output": 4.00},
                "kimi-k3:cloud": {"cost_per_1M_input": 3.00, "cost_per_1M_output": 15.00},
                "deepseek-v4.1-flash:cloud": {
                    "cost_per_1M_input": 0.30,
                    "cost_per_1M_output": 1.20,
                },
                "deepseek-v4-pro:cloud": {"cost_per_1M_input": 1.32, "cost_per_1M_output": 3.96},
                "gemma4:cloud": {"cost_per_1M_input": 0.14, "cost_per_1M_output": 0.40},
                "minimax-m3.1-flash:cloud": {"cost_per_1M_input": 0.15, "cost_per_1M_output": 0.60},
            }
        },
    },
    "model_equivalents": {
        "gpt-image-2": {"poyo_ai": "gpt-image-2", "openrouter": "openai/gpt-image-2"},
        "flux-schnell": {
            "poyo_ai": "flux-schnell",
            "replicate_image": "black-forest-labs/flux-schnell",
        },
        "flux-dev": {
            "fal_ai": "fal-ai/flux/dev",
            "replicate_image": "black-forest-labs/flux-dev",
        },
        "seedance-2-mini": {
            "poyo_ai": "seedance-2-mini",
            "fal_ai": "bytedance/seedance-2.0/mini/text-to-video",
        },
    },
}


class PricingRegistry:
    """Load and query provider model pricing data."""

    def __init__(self) -> None:
        self.last_updated: str = ""
        self.providers: dict[str, Any] = {}
        self.model_equivalents: dict[str, dict[str, str]] = {}

    @classmethod
    def load(cls, path: Path | str | None = None) -> PricingRegistry:
        """Load pricing from a YAML file, falling back to built-in defaults."""
        registry = cls()
        target = Path(path) if path else default_pricing_path()

        if target.exists():
            try:
                with open(target, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                registry.last_updated = str(data.get("last_updated", ""))
                registry.providers = data.get("providers", {})
                registry.model_equivalents = data.get("model_equivalents", {}) or {}
                logger.info("pricing_loaded", path=str(target), last_updated=registry.last_updated)
                return registry
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("pricing_load_failed", path=str(target), error=str(exc))

        # Fall back to the canonical models.yaml (single source of truth) before
        # the built-in snapshot.
        models_path = default_models_path()
        if models_path.exists():
            try:
                with open(models_path, encoding="utf-8") as f:
                    models = yaml.safe_load(f) or {}
                registry.last_updated = str(models.get("last_updated", ""))
                registry.providers = models.get("providers", {})
                registry.model_equivalents = models.get("model_equivalents", {}) or {}
                logger.info("pricing_loaded_from_models_yaml", path=str(models_path))
                return registry
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("models_yaml_load_failed", path=str(models_path), error=str(exc))

        # Last resort: built-in defaults.
        logger.info("pricing_using_builtin_defaults")
        registry.last_updated = str(_BUILTIN_DEFAULTS.get("last_updated", ""))
        registry.providers = copy.deepcopy(_BUILTIN_DEFAULTS.get("providers", {}))
        registry.model_equivalents = copy.deepcopy(_BUILTIN_DEFAULTS.get("model_equivalents", {}))
        return registry

    def is_stale(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
        """Return True if pricing data is older than max_age_days."""
        if not self.last_updated:
            return True
        try:
            updated = datetime.strptime(self.last_updated, "%Y-%m-%d").replace(tzinfo=UTC)
            age = datetime.now(UTC) - updated
            return age > timedelta(days=max_age_days)
        except ValueError:
            return True

    def get_model_cost(
        self,
        provider: str,
        model: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the cost dict for a specific provider+model combination.

        For providers with flat pricing (no models key), the model parameter
        is ignored and the provider-level cost dict is returned.

        ``params`` is the asset request's own params. When the model carries a
        ``price_by`` table (a rate that depends on a request attribute such as
        ``resolution``), the matching tier is merged over the base entry, so
        every caller prices the request it will actually make (#269). The tier
        is read from ``params["model_kwargs"]`` first — that is what the
        provider sends — then from the top level.
        """
        from typing import cast

        provider_data = self.providers.get(provider, {})
        if not provider_data:
            return {}

        # Providers with nested models
        if "models" in provider_data:
            models = cast(dict[str, Any], provider_data["models"])
            cost = cast(dict[str, Any], models.get(model, {}))
        else:
            # Flat providers (e.g. pexels, coverr)
            cost = cast(dict[str, Any], provider_data)
        return _apply_price_variant(cost, params)

    def resolve_model(self, provider: str, model: str) -> str:
        """Translate a canonical model id to ``provider``'s id, if known.

        ``models.yaml:model_equivalents`` maps a canonical model (e.g.
        ``gpt-image-2``) to each provider's id (``poyo_ai: gpt-image-2``,
        ``openrouter: openai/gpt-image-2``). Unknown models pass through
        unchanged.
        """
        equivalents = self.model_equivalents.get(model)
        if equivalents and provider in equivalents:
            return equivalents[provider]
        return model

    def providers_for_model(self, model: str) -> dict[str, str]:
        """Return ``{provider: provider_model_id}`` for providers offering ``model``.

        Matches by canonical equivalence first, then by exact model id across
        every provider's model list.
        """
        found: dict[str, str] = {}
        equivalents = self.model_equivalents.get(model)
        if equivalents:
            for provider, provider_model in equivalents.items():
                if provider in self.providers:
                    found[provider] = provider_model
        for provider, data in self.providers.items():
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, dict) and model in models:
                found.setdefault(provider, model)
        return found

    def unit_cost(
        self,
        provider: str,
        model: str,
        asset_type: str,
        params: dict[str, Any] | None = None,
    ) -> float | None:
        """Return the comparable unit cost for a provider/model/asset type.

        The key depends on the asset type (``cost_per_image`` for images,
        ``cost_per_second`` for video, ``cost_per_generation`` then
        ``cost_per_second`` for music, ...). Returns ``None`` when no priced
        entry exists. Costs across different unit kinds are not directly
        comparable — callers compare within a single asset type.

        ``params`` selects a price tier when the model prices by request
        attribute (see ``get_model_cost``).
        """
        keys = _ASSET_COST_KEYS.get(asset_type, ())
        cost = self.get_model_cost(provider, model, params=params)
        for key in keys:
            if key in cost:
                try:
                    return float(cost[key])
                except (TypeError, ValueError):
                    continue
        return None

    def cheapest_provider(self, model: str, asset_type: str) -> tuple[str, str, float] | None:
        """Return ``(provider, provider_model, unit_cost)`` for the cheapest
        provider offering ``model`` for ``asset_type``, or ``None``.

        Ties are broken by provider name for determinism.
        """
        candidates = self.providers_for_model(model)
        best: tuple[str, str, float] | None = None
        for provider, provider_model in sorted(candidates.items()):
            cost = self.unit_cost(provider, provider_model, asset_type)
            if cost is None:
                continue
            if best is None or cost < best[2]:
                best = (provider, provider_model, cost)
        return best

    def check_freshness(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
        """Log a warning if pricing data is stale. Never raises."""
        if self.is_stale(max_age_days):
            age_hint = ""
            if self.last_updated:
                try:
                    updated = datetime.strptime(self.last_updated, "%Y-%m-%d").replace(tzinfo=UTC)
                    days = (datetime.now(UTC) - updated).days
                    age_hint = f" ({days} days old)"
                except ValueError:
                    pass
            logger.warning(
                "pricing_data_stale",
                last_updated=self.last_updated or "unknown",
                max_age_days=max_age_days,
                hint=f"Run `ve touch-pricing` to refresh{age_hint}.",
            )


_MAX_TIER_DEPTH = 4


def _apply_price_variant(
    cost: dict[str, Any],
    params: dict[str, Any] | None,
    _depth: int = 0,
) -> dict[str, Any]:
    """Merge the request's price tier over a model's base cost entry.

    A model may declare a rate per request attribute::

        gpt-image-2.5-fixed:
          cost_per_image: 0.0085        # base / cheapest tier
          price_by:
            resolution:
              "1K": {cost_per_image: 0.0085}
              "2K": {cost_per_image: 0.03}
              "4K": {cost_per_image: 0.04}

    A tier entry may itself carry ``price_by``, for models that price on more
    than one axis — ``gpt-image-2`` bills a quality x resolution matrix::

        price_by:
          quality:
            high:
              cost_per_image: 0.169     # high 1K, used when no resolution is asked
              price_by:
                resolution:
                  "4K": {cost_per_image: 0.321}

    Each level consumes its own axis and recurses into the entry it matched, so
    a quality match with no resolution keeps that quality's rate instead of
    dropping back to the model's cheapest.

    ``params`` supplies the attributes (nested in ``model_kwargs`` first, then
    top level). Values are matched case-insensitively. No params, an absent
    attribute or an unpriced value all leave the entry untouched — an unknown
    tier must never price at $0.00 (#269).
    """
    if not params or _depth >= _MAX_TIER_DEPTH:
        return cost
    variants = cost.get("price_by")
    if not isinstance(variants, dict):
        return cost
    nested = params.get("model_kwargs")
    nested = nested if isinstance(nested, dict) else {}

    merged = cost
    for key, table in variants.items():
        if not isinstance(table, dict):
            continue
        value = nested.get(key, params.get(key))
        if value is None:
            continue
        entry = _lookup_tier(table, value)
        if entry is None:
            continue
        # Drop the table that was just resolved: a deeper ``price_by`` can only
        # come from the matched entry, which also keeps a self-similar config
        # from re-applying the same axis.
        merged = dict(merged)
        merged.pop("price_by", None)
        merged.update(entry)
        merged = _apply_price_variant(merged, params, _depth + 1)
    return merged


def _lookup_tier(table: dict[str, Any], value: Any) -> dict[str, Any] | None:
    """The tier entry for ``value``, matched exactly then case-insensitively."""
    entry = table.get(str(value).strip())
    if isinstance(entry, dict):
        return entry
    wanted = str(value).strip().lower()
    for tier, candidate in table.items():
        if str(tier).strip().lower() == wanted and isinstance(candidate, dict):
            return candidate
    return None


def _get_cost_value(cost_dict: dict[str, Any], key: str) -> float:
    """Safely extract a numeric cost value from a cost dict."""
    return float(cost_dict.get(key, 0.0))


def estimate_tts_cost(
    provider: str,
    model: str,
    char_count: int,
    registry: PricingRegistry,
    *,
    char_counts: Sequence[int] | None = None,
) -> float:
    """Price TTS using the provider's billing unit.

    Most providers bill per 1,000 characters and can be priced from a total.
    Sarvam bills ``ceil(chars / 1_000)`` **per call**, so when the caller knows
    the individual request sizes it must pass ``char_counts``; otherwise a
    multi-call job is under-priced (#263). A single-call caller can keep using
    ``char_count`` alone.
    """
    cost_dict = registry.get_model_cost(provider, model)
    rate = _get_cost_value(cost_dict, "cost_per_1k_chars")
    if rate == 0.0:
        return 0.0
    billing_unit = str(cost_dict.get("billing_unit", "per_1000_chars"))
    if billing_unit == "per_call":
        counts = char_counts if char_counts is not None else [char_count]
        blocks: float = sum(math.ceil(chars / 1000) for chars in counts)
    else:
        # Billed by the character (ElevenLabs credits): a 177-character clip
        # costs 177/1000 of the rate, not a whole block (chai-stall-pilot-1
        # priced ten short scene clips at 10 x $0.08).
        blocks = char_count / 1000
    return round(blocks * rate, 4)


def estimate_image_cost(
    provider: str,
    model: str,
    count: int,
    registry: PricingRegistry,
    params: dict[str, Any] | None = None,
) -> float:
    """cost_per_image x count, at the request's price tier when it has one."""
    cost_dict = registry.get_model_cost(provider, model, params=params)
    rate = _get_cost_value(cost_dict, "cost_per_image")
    if rate == 0.0:
        # Check for flat cost_per_asset (free stock providers)
        rate = _get_cost_value(cost_dict, "cost_per_asset")
    return round(count * rate, 4)


def estimate_video_cost(
    provider: str,
    model: str,
    duration_s: float,
    registry: PricingRegistry,
    params: dict[str, Any] | None = None,
    *,
    count: int = 1,
) -> float:
    """cost_per_second x duration_seconds, at the request's price tier.

    A model billed per request (``cost_per_generation``, e.g. PoYo's Wan 2.2 Fast,
    whose clips have a fixed length) costs ``count`` generations regardless of
    the duration asked for.
    """
    cost_dict = registry.get_model_cost(provider, model, params=params)
    if "cost_per_generation" in cost_dict:
        return round(count * _get_cost_value(cost_dict, "cost_per_generation"), 4)
    rate = _get_cost_value(cost_dict, "cost_per_second")
    if rate == 0.0:
        rate = _get_cost_value(cost_dict, "cost_per_asset")
    return round(duration_s * rate, 4)


def estimate_music_cost(
    provider: str,
    model: str,
    duration_s: float,
    registry: PricingRegistry,
    params: dict[str, Any] | None = None,
) -> float:
    """Estimate the cost of one music track.

    Providers that bill per generation (e.g. PoYo Music, Suno) expose a
    ``cost_per_generation`` rate; when present it wins over the per-second
    approximation regardless of the requested duration, because one
    generation yields one usable track. Providers that bill per second
    (e.g. ElevenLabs Music) fall back to ``cost_per_second x duration``.
    A ``params`` tier overrides whichever rate applies.
    """
    cost_dict = registry.get_model_cost(provider, model, params=params)
    if "cost_per_generation" in cost_dict:
        return round(_get_cost_value(cost_dict, "cost_per_generation"), 4)
    rate = _get_cost_value(cost_dict, "cost_per_second")
    return round(duration_s * rate, 4)


def estimate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    registry: PricingRegistry,
) -> float:
    """(input x cost_per_1M_input + output x cost_per_1M_output) / 1_000_000"""
    cost_dict = registry.get_model_cost(provider, model)
    input_rate = _get_cost_value(cost_dict, "cost_per_1M_input")
    output_rate = _get_cost_value(cost_dict, "cost_per_1M_output")
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
