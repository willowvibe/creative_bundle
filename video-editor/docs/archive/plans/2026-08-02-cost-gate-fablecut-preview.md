# Cost Gate + FableCut Preview Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cost estimation gate and FableCut preview loop to all video-editor pipeline templates.

**Architecture:** A new `pricing.py` module loads model-level costs from `pricing.yaml` with 30-day freshness checks. Three new CLI commands (`ve estimate`, `ve preview`, `ve finalize`) are registered via the existing `register_*` pattern. The `ve preview` command reuses the existing FableCut bridge (`export_to_fablecut`, `import_from_fablecut`, `render_fablecut_project`) and the `open_in_fablecut.py` server-startup pattern. New manifest stages (`estimate`, `preview`, `finalize`) integrate with the existing `JobManifest` stage-tracking system.

**Tech Stack:** Python 3.12, Typer, Pydantic, structlog, PyYAML, existing FableCut bridge (Node.js server on localhost:7777)

## Global Constraints

- Follow existing `register_*` pattern for CLI command registration (see `commands/cache_prune.py`)
- Follow existing `@app.command()` decorator pattern for commands defined inline in `main.py`
- All costs in USD (migrate from abstract "credits")
- Pricing data file at repo root: `pricing.yaml`
- 30-day freshness check on pricing data (warning only, never blocks)
- Poyo.ai primary for paid image/video; Pexels/Coverr/Mixkit for free stock
- ElevenLabs voice IDs: professional=`ATYMJR0SnaN1mSrsnZEi`, instant=`32EwRSV41WufKgzh80Ri`
- FableCut expected at `tools/FableCut/` (configurable via `FABLECUT_PATH` env var)
- All new commands use `_context()` helper for JobContext creation (existing pattern in `main.py`)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `pricing.yaml` (create) | Provider model pricing data, `last_updated` timestamp |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py` (create) | `PricingRegistry` class: load YAML, freshness check, per-type cost calculators |
| `apps/cli/src/video_editor_cli/commands/estimate.py` (create) | `ve estimate` command: tally costs, present table, y/n prompt |
| `apps/cli/src/video_editor_cli/commands/preview.py` (create) | `ve preview` command: export to FableCut, start server, edit loop |
| `apps/cli/src/video_editor_cli/commands/finalize.py` (create) | `ve finalize` command: final export after preview |
| `apps/cli/src/video_editor_cli/commands/update_pricing.py` (create) | `ve update-pricing` command: refresh pricing data |
| `apps/cli/src/video_editor_cli/commands/__init__.py` (modify) | Export new command registration functions |
| `apps/cli/src/video_editor_cli/main.py` (modify) | Register new commands; add `--auto-approve`/`--skip-preview` to `ve run` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/__init__.py` (modify) | Export `PricingRegistry` |
| `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py` (modify) | Wire `BudgetGovernor` to use `PricingRegistry` |
| `packages/state/src/video_editor_state/manifest.py` (modify) | No changes needed — `stages` is a free-form `dict[str, dict[str, Any]]` |
| `config.yaml` (modify) | Add `pricing:` section |
| `tests/test_pricing.py` (create) | Unit tests for pricing registry |
| `tests/test_estimate.py` (create) | Integration tests for `ve estimate` |
| `tests/test_preview.py` (create) | Integration tests for `ve preview` |

---

### Task 1: Pricing Registry Module

**Files:**
- Create: `pricing.yaml`
- Create: `packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py`
- Create: `tests/test_pricing.py`
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/__init__.py`

**Interfaces:**
- Produces: `PricingRegistry` class with `load()`, `get_model_cost()`, `is_stale()`, `estimate_tts_cost()`, `estimate_image_cost()`, `estimate_video_cost()`, `estimate_music_cost()`, `estimate_llm_cost()`

- [ ] **Step 1: Write the pricing data file**

Create `pricing.yaml` at repo root:

```yaml
last_updated: "2026-08-02"
providers:
  elevenlabs_tts:
    models:
      eleven_turbo_v2_5: { cost_per_1k_chars: 0.05, currency: USD }
      eleven_flash_v2_5: { cost_per_1k_chars: 0.01, currency: USD }
      eleven_multilingual_v2: { cost_per_1k_chars: 0.05, currency: USD }
  poyo_ai:
    models:
      gpt-image-2: { cost_per_image: 0.04, currency: USD }
      flux-schnell: { cost_per_image: 0.005, currency: USD }
      seedance-2-fast: { cost_per_second: 0.02, currency: USD }
      seedance-2: { cost_per_second: 0.05, currency: USD }
  fal_ai:
    models:
      fal-ai/flux/dev: { cost_per_image: 0.005, currency: USD }
      fal-ai/flux-pro: { cost_per_image: 0.05, currency: USD }
  pexels: { cost_per_asset: 0.0, currency: USD, note: free stock }
  coverr: { cost_per_asset: 0.0, currency: USD, note: free stock }
  mixkit: { cost_per_asset: 0.0, currency: USD, note: free stock }
  poyo_music:
    models:
      minimax-music-2-6: { cost_per_second: 0.01, currency: USD }
      generate-music: { cost_per_second: 0.005, currency: USD }
  elevenlabs_music:
    models:
      eleven_music_v1: { cost_per_second: 0.03, currency: USD }
  openrouter:
    models:
      anthropic/claude-sonnet-4: { cost_per_1M_input: 3.0, cost_per_1M_output: 15.0 }
      google/gemini-2.5-flash: { cost_per_1M_input: 0.15, cost_per_1M_output: 0.60 }
  elevenlabs_sfx:
    models:
      default: { cost_per_second: 0.02, currency: USD }
  sarvam_tts:
    models:
      bulbul-v3: { cost_per_1k_chars: 0.003, currency: USD }
  openai_tts:
    models:
      tts-1: { cost_per_1k_chars: 0.015, currency: USD }
      tts-1-hd: { cost_per_1k_chars: 0.03, currency: USD }
  replicate_image:
    models:
      black-forest-labs/flux-schnell: { cost_per_image: 0.003, currency: USD }
  kokoro_tts: { cost_per_1k_chars: 0.0, currency: USD, note: local }
  kitten_tts: { cost_per_1k_chars: 0.0, currency: USD, note: local }
  moss_tts: { cost_per_1k_chars: 0.0, currency: USD, note: local }
  ace_step: { cost_per_second: 0.0, currency: USD, note: local }
  hyperframes_video: { cost_per_second: 0.0, currency: USD, note: local render }
  matplotlib_data_viz: { cost_per_image: 0.0, currency: USD, note: local }
```

- [ ] **Step 2: Write the failing test for PricingRegistry**

Create `tests/test_pricing.py`:

```python
"""Tests for the pricing registry module."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_tts_cost,
    estimate_image_cost,
    estimate_video_cost,
    estimate_music_cost,
    estimate_llm_cost,
)


SAMPLE_PRICING = {
    "last_updated": "2026-08-02",
    "providers": {
        "elevenlabs_tts": {
            "models": {
                "eleven_flash_v2_5": {"cost_per_1k_chars": 0.01, "currency": "USD"},
            }
        },
        "poyo_ai": {
            "models": {
                "gpt-image-2": {"cost_per_image": 0.04, "currency": "USD"},
                "seedance-2-fast": {"cost_per_second": 0.02, "currency": "USD"},
            }
        },
        "pexels": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "poyo_music": {
            "models": {
                "minimax-music-2-6": {"cost_per_second": 0.01, "currency": "USD"},
            }
        },
        "openrouter": {
            "models": {
                "anthropic/claude-sonnet-4": {
                    "cost_per_1M_input": 3.0,
                    "cost_per_1M_output": 15.0,
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
        assert registry.last_updated == "2026-08-02"
        assert "elevenlabs_tts" in registry.providers

    def test_get_model_cost_flat_provider(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = registry.get_model_cost("pexels", "default")
        assert cost == {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"}

    def test_get_model_cost_nested_models(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = registry.get_model_cost("elevenlabs_tts", "eleven_flash_v2_5")
        assert cost == {"cost_per_1k_chars": 0.01, "currency": "USD"}

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
        assert cost == 0.01  # ceil(500/1000) * 0.01 = 1 * 0.01

    def test_estimate_tts_cost_rounds_up(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_tts_cost("elevenlabs_tts", "eleven_flash_v2_5", 1001, registry)
        assert cost == 0.02  # ceil(1001/1000) * 0.01 = 2 * 0.01

    def test_estimate_image_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_image_cost("poyo_ai", "gpt-image-2", 5, registry)
        assert cost == 0.20  # 5 * 0.04

    def test_estimate_video_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_video_cost("poyo_ai", "seedance-2-fast", 10.0, registry)
        assert cost == 0.20  # 10 * 0.02

    def test_estimate_music_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_music_cost("poyo_music", "minimax-music-2-6", 71.0, registry)
        assert cost == 0.71  # 71 * 0.01

    def test_estimate_llm_cost(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_llm_cost(
            "openrouter", "anthropic/claude-sonnet-4", 2000, 1000, registry
        )
        # (2000 * 3.0 + 1000 * 15.0) / 1_000_000 = (6000 + 15000) / 1_000_000 = 0.021
        assert cost == pytest.approx(0.021)

    def test_estimate_free_provider(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_image_cost("pexels", "default", 10, registry)
        assert cost == 0.0

    def test_estimate_unknown_provider_returns_zero(self, sample_pricing_file: Path) -> None:
        registry = PricingRegistry.load(sample_pricing_file)
        cost = estimate_tts_cost("nonexistent", "model", 1000, registry)
        assert cost == 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_pricing.py -v
```

Expected: all tests FAIL with `ModuleNotFoundError: No module named 'video_editor_asset_ingestion.pricing'`

- [ ] **Step 4: Implement PricingRegistry**

Create `packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py`:

```python
"""Provider model pricing registry with freshness checks.

Loads model-level costs from ``pricing.yaml``. Falls back to built-in
defaults when the file is missing. Emits a warning when pricing data is
older than 30 days.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_PRICING_PATH = REPO_ROOT / "pricing.yaml"
DEFAULT_MAX_AGE_DAYS = 30

_BUILTIN_DEFAULTS: dict[str, Any] = {
    "last_updated": "2026-08-02",
    "providers": {
        "elevenlabs_tts": {
            "models": {
                "eleven_turbo_v2_5": {"cost_per_1k_chars": 0.05, "currency": "USD"},
                "eleven_flash_v2_5": {"cost_per_1k_chars": 0.01, "currency": "USD"},
                "eleven_multilingual_v2": {"cost_per_1k_chars": 0.05, "currency": "USD"},
            }
        },
        "poyo_ai": {
            "models": {
                "gpt-image-2": {"cost_per_image": 0.04, "currency": "USD"},
                "flux-schnell": {"cost_per_image": 0.005, "currency": "USD"},
                "seedance-2-fast": {"cost_per_second": 0.02, "currency": "USD"},
                "seedance-2": {"cost_per_second": 0.05, "currency": "USD"},
            }
        },
        "fal_ai": {
            "models": {
                "fal-ai/flux/dev": {"cost_per_image": 0.005, "currency": "USD"},
                "fal-ai/flux-pro": {"cost_per_image": 0.05, "currency": "USD"},
            }
        },
        "pexels": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "coverr": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "mixkit": {"cost_per_asset": 0.0, "currency": "USD", "note": "free stock"},
        "poyo_music": {
            "models": {
                "minimax-music-2-6": {"cost_per_second": 0.01, "currency": "USD"},
                "generate-music": {"cost_per_second": 0.005, "currency": "USD"},
            }
        },
        "elevenlabs_music": {
            "models": {
                "eleven_music_v1": {"cost_per_second": 0.03, "currency": "USD"},
            }
        },
        "openrouter": {
            "models": {
                "anthropic/claude-sonnet-4": {
                    "cost_per_1M_input": 3.0,
                    "cost_per_1M_output": 15.0,
                },
                "google/gemini-2.5-flash": {
                    "cost_per_1M_input": 0.15,
                    "cost_per_1M_output": 0.60,
                },
            }
        },
        "elevenlabs_sfx": {
            "models": {
                "default": {"cost_per_second": 0.02, "currency": "USD"},
            }
        },
        "sarvam_tts": {
            "models": {
                "bulbul-v3": {"cost_per_1k_chars": 0.003, "currency": "USD"},
            }
        },
        "openai_tts": {
            "models": {
                "tts-1": {"cost_per_1k_chars": 0.015, "currency": "USD"},
                "tts-1-hd": {"cost_per_1k_chars": 0.03, "currency": "USD"},
            }
        },
        "replicate_image": {
            "models": {
                "black-forest-labs/flux-schnell": {"cost_per_image": 0.003, "currency": "USD"},
            }
        },
        "kokoro_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        "kitten_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        "moss_tts": {"cost_per_1k_chars": 0.0, "currency": "USD", "note": "local"},
        "ace_step": {"cost_per_second": 0.0, "currency": "USD", "note": "local"},
        "hyperframes_video": {"cost_per_second": 0.0, "currency": "USD", "note": "local render"},
        "matplotlib_data_viz": {"cost_per_image": 0.0, "currency": "USD", "note": "local"},
    },
}


class PricingRegistry:
    """Load and query provider model pricing data."""

    def __init__(self) -> None:
        self.last_updated: str = ""
        self.providers: dict[str, Any] = {}

    @classmethod
    def load(cls, path: Path | str | None = None) -> PricingRegistry:
        """Load pricing from a YAML file, falling back to built-in defaults."""
        registry = cls()
        target = Path(path) if path else DEFAULT_PRICING_PATH

        if target.exists():
            try:
                with open(target, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                registry.last_updated = str(data.get("last_updated", ""))
                registry.providers = data.get("providers", {})
                logger.info("pricing_loaded", path=str(target), last_updated=registry.last_updated)
                return registry
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("pricing_load_failed", path=str(target), error=str(exc))

        # Fall back to built-in defaults
        logger.info("pricing_using_builtin_defaults")
        registry.last_updated = str(_BUILTIN_DEFAULTS.get("last_updated", ""))
        registry.providers = _BUILTIN_DEFAULTS.get("providers", {})
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

    def get_model_cost(self, provider: str, model: str) -> dict[str, Any]:
        """Return the cost dict for a specific provider+model combination.

        For providers with flat pricing (no models key), the model parameter
        is ignored and the provider-level cost dict is returned.
        """
        provider_data = self.providers.get(provider, {})
        if not provider_data:
            return {}

        # Providers with nested models
        if "models" in provider_data:
            models = provider_data["models"]
            return models.get(model, {})

        # Flat providers (e.g. pexels, coverr)
        return provider_data

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
                hint=f"Run `ve update-pricing` to refresh{age_hint}.",
            )


def _get_cost_value(cost_dict: dict[str, Any], key: str) -> float:
    """Safely extract a numeric cost value from a cost dict."""
    return float(cost_dict.get(key, 0.0))


def estimate_tts_cost(
    provider: str, model: str, char_count: int, registry: PricingRegistry
) -> float:
    """cost_per_1k_chars × ceil(chars/1000)"""
    cost_dict = registry.get_model_cost(provider, model)
    rate = _get_cost_value(cost_dict, "cost_per_1k_chars")
    if rate == 0.0:
        return 0.0
    blocks = math.ceil(char_count / 1000)
    return round(blocks * rate, 4)


def estimate_image_cost(
    provider: str, model: str, count: int, registry: PricingRegistry
) -> float:
    """cost_per_image × count"""
    cost_dict = registry.get_model_cost(provider, model)
    rate = _get_cost_value(cost_dict, "cost_per_image")
    if rate == 0.0:
        # Check for flat cost_per_asset (free stock providers)
        rate = _get_cost_value(cost_dict, "cost_per_asset")
    return round(count * rate, 4)


def estimate_video_cost(
    provider: str, model: str, duration_s: float, registry: PricingRegistry
) -> float:
    """cost_per_second × duration_seconds"""
    cost_dict = registry.get_model_cost(provider, model)
    rate = _get_cost_value(cost_dict, "cost_per_second")
    if rate == 0.0:
        rate = _get_cost_value(cost_dict, "cost_per_asset")
    return round(duration_s * rate, 4)


def estimate_music_cost(
    provider: str, model: str, duration_s: float, registry: PricingRegistry
) -> float:
    """cost_per_second × duration_seconds"""
    cost_dict = registry.get_model_cost(provider, model)
    rate = _get_cost_value(cost_dict, "cost_per_second")
    return round(duration_s * rate, 4)


def estimate_llm_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    registry: PricingRegistry,
) -> float:
    """(input × cost_per_1M_input + output × cost_per_1M_output) / 1_000_000"""
    cost_dict = registry.get_model_cost(provider, model)
    input_rate = _get_cost_value(cost_dict, "cost_per_1M_input")
    output_rate = _get_cost_value(cost_dict, "cost_per_1M_output")
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_pricing.py -v
```

Expected: all 14 tests PASS

- [ ] **Step 6: Export PricingRegistry from package __init__**

Edit `packages/asset_ingestion/src/video_editor_asset_ingestion/__init__.py`:

```python
"""Generative asset ingestion pipeline for cloud media providers."""

from __future__ import annotations

from video_editor_asset_ingestion.config import AssetProviderConfig
from video_editor_asset_ingestion.manager import AssetIngestionManager
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_llm_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)

__all__ = [
    "AssetRequest",
    "AssetResult",
    "AssetType",
    "AssetIngestionManager",
    "AssetProviderConfig",
    "PricingRegistry",
    "estimate_tts_cost",
    "estimate_image_cost",
    "estimate_video_cost",
    "estimate_music_cost",
    "estimate_llm_cost",
]
```

- [ ] **Step 7: Commit**

```bash
git add pricing.yaml \
  packages/asset_ingestion/src/video_editor_asset_ingestion/pricing.py \
  packages/asset_ingestion/src/video_editor_asset_ingestion/__init__.py \
  tests/test_pricing.py
git commit -m "feat: add provider model pricing registry with freshness checks

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `ve estimate` Command

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/estimate.py`
- Create: `tests/test_estimate.py`
- Modify: `apps/cli/src/video_editor_cli/commands/__init__.py`
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: `PricingRegistry`, `estimate_tts_cost`, `estimate_image_cost`, `estimate_video_cost`, `estimate_music_cost` from Task 1
- Consumes: `ScenePlan`, `requests_from_scene` from existing engine
- Produces: `register_estimate(app)` function; `ve estimate` CLI command

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_estimate.py`:

```python
"""Integration tests for the ve estimate command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.main import app as _app_module


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def temp_work_dir(tmp_path: Path) -> Path:
    """Create a minimal work directory with a scene plan."""
    work = tmp_path / "test-job"
    plan_dir = work / "plan"
    plan_dir.mkdir(parents=True)

    plan = {
        "job_id": "test-job",
        "script": "# Test\n\n## Scene 1\n- Narration: Hello world\n- Visual: A test image\n",
        "scenes": [
            {
                "id": "scene_001",
                "type": "narration",
                "duration_seconds": 5.0,
                "narration": "Hello world",
                "visual_description": "A test image",
                "asset_query": "test",
                "props": {},
                "assets": [],
            }
        ],
        "aspect_ratio": "16:9",
        "music_mood": "cinematic",
        "music_path": "",
    }
    (plan_dir / "scene_plan.json").write_text(json.dumps(plan))
    return work


class TestEstimateCommand:
    def test_estimate_requires_job_id(self, runner: CliRunner) -> None:
        """ve estimate without a job-id should fail."""
        # We test the command function directly since we can't easily
        # test the registered Typer app in isolation.
        from video_editor_cli.commands.estimate import estimate_command

        # Missing scene plan should raise
        with pytest.raises(SystemExit):
            estimate_command(
                job_id="nonexistent",
                work_root="/tmp/nonexistent",
                output_root="/tmp/nonexistent",
                config_path=None,
                auto_approve=False,
                tts_provider="elevenlabs_tts",
                image_provider="poyo_ai",
                video_provider="poyo_ai",
                music_provider="poyo_music",
                voice="instant",
            )

    def test_estimate_with_auto_approve(self, temp_work_dir: Path) -> None:
        """ve estimate --yes should write cost_estimate.json without prompting."""
        from video_editor_cli.commands.estimate import estimate_command

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        estimate_path = temp_work_dir / "plan" / "cost_estimate.json"
        assert estimate_path.exists()

        data = json.loads(estimate_path.read_text())
        assert data["job_id"] == "test-job"
        assert data["voice_id"] == "32EwRSV41WufKgzh80Ri"
        assert data["voice_name"] == "instant"
        assert "line_items" in data
        assert "total_cost" in data
        assert data["currency"] == "USD"

    def test_estimate_writes_manifest_stage(self, temp_work_dir: Path) -> None:
        """ve estimate should mark the estimate stage in the manifest."""
        from video_editor_cli.commands.estimate import estimate_command

        # Create a manifest first
        manifest_path = temp_work_dir / "manifest.json"
        manifest = {
            "job_id": "test-job",
            "tenant_id": "default",
            "created_at": "2026-08-02T00:00:00Z",
            "work_root": str(temp_work_dir),
            "output_root": str(temp_work_dir.parent),
            "stages": {},
            "operations": [],
        }
        manifest_path.write_text(json.dumps(manifest))

        estimate_command(
            job_id="test-job",
            work_root=str(temp_work_dir.parent),
            output_root=str(temp_work_dir.parent),
            config_path=None,
            auto_approve=True,
            tts_provider="elevenlabs_tts",
            image_provider="poyo_ai",
            video_provider="poyo_ai",
            music_provider="poyo_music",
            voice="instant",
        )

        updated = json.loads(manifest_path.read_text())
        assert "estimate" in updated["stages"]
        assert "completed_at" in updated["stages"]["estimate"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_estimate.py -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement the estimate command**

Create `apps/cli/src/video_editor_cli/commands/estimate.py`:

```python
"""ve estimate: tally asset costs and prompt for approval before generation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import typer
from video_editor_asset_ingestion.models import AssetType
from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    estimate_image_cost,
    estimate_music_cost,
    estimate_tts_cost,
    estimate_video_cost,
)
from video_editor_asset_ingestion.scene_assets import requests_from_scene
from video_editor_engine.models.scene_plan import ScenePlan
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

# Harish's ElevenLabs voice IDs
VOICE_OPTIONS: dict[str, dict[str, str]] = {
    "professional": {
        "id": "ATYMJR0SnaN1mSrsnZEi",
        "label": "professional (ATYMJR0SnaN1mSrsnZEi)",
    },
    "instant": {
        "id": "32EwRSV41WufKgzh80Ri",
        "label": "instant (32EwRSV41WufKgzh80Ri)",
    },
}

# Default provider→model mapping for cost estimation
DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "elevenlabs_tts": "eleven_flash_v2_5",
    "poyo_ai": "gpt-image-2",
    "fal_ai": "fal-ai/flux/dev",
    "pexels": "default",
    "coverr": "default",
    "mixkit": "default",
    "poyo_music": "minimax-music-2-6",
    "elevenlabs_music": "eleven_music_v1",
    "openrouter": "anthropic/claude-sonnet-4",
}


def _resolve_model(provider: str) -> str:
    """Return the default model for a provider."""
    return DEFAULT_PROVIDER_MODELS.get(provider, "default")


def _count_chars(scenes: list[Any]) -> int:
    """Count total narration characters across all scenes."""
    return sum(len(getattr(s, "narration", "") or "") for s in scenes)


def _tally_costs(
    plan: ScenePlan,
    registry: PricingRegistry,
    tts_provider: str,
    image_provider: str,
    video_provider: str,
    music_provider: str,
) -> list[dict[str, Any]]:
    """Build a list of cost line items from a scene plan."""
    items: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], int] = {}

    for scene in plan.scenes:
        for req in requests_from_scene(
            scene,
            plan.job_id,
            tts_provider=tts_provider,
            image_provider=image_provider,
            video_provider=video_provider,
        ):
            provider = req.provider or "unknown"
            asset_type = req.asset_type.value if hasattr(req.asset_type, "value") else str(req.asset_type)
            model = _resolve_model(provider)
            key = (provider, model, asset_type)
            seen[key] = seen.get(key, 0) + 1

    for (provider, model, asset_type), count in seen.items():
        if asset_type == "tts":
            total_chars = _count_chars(plan.scenes)
            unit_cost = estimate_tts_cost(provider, model, total_chars, registry)
            total_cost = unit_cost
        elif asset_type == "image":
            unit_cost = estimate_image_cost(provider, model, 1, registry)
            total_cost = estimate_image_cost(provider, model, count, registry)
        elif asset_type == "video":
            total_duration = sum(s.duration_seconds for s in plan.scenes)
            unit_cost = estimate_video_cost(provider, model, 1.0, registry)
            total_cost = estimate_video_cost(provider, model, total_duration, registry)
        elif asset_type == "music":
            total_duration = sum(s.duration_seconds for s in plan.scenes)
            unit_cost = estimate_music_cost(provider, model, 1.0, registry)
            total_cost = estimate_music_cost(provider, model, total_duration, registry)
        else:
            unit_cost = 0.0
            total_cost = 0.0

        items.append({
            "provider": provider,
            "model": model,
            "task_type": asset_type,
            "count": count,
            "unit_cost": round(unit_cost, 6),
            "total_cost": round(total_cost, 4),
            "currency": "USD",
        })

    return items


def _format_table(items: list[dict[str, Any]], total_cost: float) -> str:
    """Format cost line items as a text table."""
    lines = [
        f"{'Provider':<20} {'Model':<22} {'Count':<7} {'Est. Cost':<10}",
        "-" * 63,
    ]
    for item in items:
        lines.append(
            f"{item['provider']:<20} {item['model']:<22} "
            f"{item['count']:<7} ${item['total_cost']:<9.4f}"
        )
    lines.append("-" * 63)
    lines.append(f"{'TOTAL':<20} {'':<22} {sum(i['count'] for i in items):<7} ${total_cost:<9.4f}")
    return "\n".join(lines)


def estimate_command(
    job_id: str,
    work_root: str = "/work",
    output_root: str = "/output",
    config_path: Path | None = None,
    auto_approve: bool = False,
    tts_provider: str = "elevenlabs_tts",
    image_provider: str = "poyo_ai",
    video_provider: str = "poyo_ai",
    music_provider: str = "poyo_music",
    voice: str = "instant",
) -> None:
    """Tally estimated costs for a scene plan and prompt for approval."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
    )

    plan_path = ctx.work_dir / "plan" / "scene_plan.json"
    if not plan_path.exists():
        typer.echo(f"Scene plan not found: {plan_path}", err=True)
        raise typer.Exit(1)

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    if not plan.scenes:
        typer.echo("No scenes to estimate. Run ve plan-script first.", err=True)
        raise typer.Exit(1)

    # Load pricing and check freshness
    registry = PricingRegistry.load()
    registry.check_freshness()

    # Resolve voice
    voice_info = VOICE_OPTIONS.get(voice, VOICE_OPTIONS["instant"])

    # Tally costs
    items = _tally_costs(
        plan, registry,
        tts_provider=tts_provider,
        image_provider=image_provider,
        video_provider=video_provider,
        music_provider=music_provider,
    )
    total_cost = round(sum(item["total_cost"] for item in items), 4)
    total_duration = sum(s.duration_seconds for s in plan.scenes)

    # Display
    typer.echo(f"\nEstimated costs for {job_id} ({len(plan.scenes)} scenes, ~{total_duration:.0f}s):")
    typer.echo(_format_table(items, total_cost))
    typer.echo(f"\nVoice: {voice_info['label']}")

    if not auto_approve:
        typer.echo(f"\nTotal: ${total_cost:.4f}")
        answer = typer.prompt("Proceed?", default="n", show_default=False)
        if answer.lower() not in ("y", "yes"):
            typer.echo("Aborted. Edit the scene plan or re-run with different providers.")
            raise typer.Exit(0)

    # Write cost estimate
    estimate_data = {
        "job_id": job_id,
        "approved_at": datetime.now(UTC).isoformat(),
        "voice_id": voice_info["id"],
        "voice_name": voice,
        "line_items": items,
        "total_cost": total_cost,
        "currency": "USD",
    }
    estimate_path = ctx.work_dir / "plan" / "cost_estimate.json"
    estimate_path.parent.mkdir(parents=True, exist_ok=True)
    estimate_path.write_text(json.dumps(estimate_data, indent=2), encoding="utf-8")

    # Mark manifest stage
    manifest = ctx.manifest()
    manifest.stage_done("estimate", {"cost_estimate": str(estimate_path), "total_cost": total_cost})
    ctx.store.save()

    typer.echo(f"Cost estimate approved and saved to {estimate_path}")


def register_estimate(app: typer.Typer) -> None:
    """Register the estimate command on the Typer app."""

    @app.command(name="estimate")
    def estimate(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str = typer.Option("/work", "--work-root", help="Work directory root"),
        output_root: str = typer.Option("/output", "--output-root", help="Output directory root"),
        config_path: Path | None = typer.Option(None, "--config", "-c", help="Path to project config.yaml"),
        auto_approve: bool = typer.Option(False, "--yes", "-y", help="Skip the approval prompt"),
        tts_provider: str = typer.Option("elevenlabs_tts", "--tts-provider", help="TTS provider for estimation"),
        image_provider: str = typer.Option("poyo_ai", "--image-provider", help="Image provider for estimation"),
        video_provider: str = typer.Option("poyo_ai", "--video-provider", help="Video provider for estimation"),
        music_provider: str = typer.Option("poyo_music", "--music-provider", help="Music provider for estimation"),
        voice: str = typer.Option("instant", "--voice", help="ElevenLabs voice: instant or professional"),
    ) -> None:
        """Tally estimated costs for a scene plan and prompt for approval."""
        estimate_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            config_path=config_path,
            auto_approve=auto_approve,
            tts_provider=tts_provider,
            image_provider=image_provider,
            video_provider=video_provider,
            music_provider=music_provider,
            voice=voice,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_estimate.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Register the command**

Edit `apps/cli/src/video_editor_cli/commands/__init__.py`:

```python
"""CLI command modules."""

from video_editor_cli.commands.estimate import register_estimate

__all__ = ["register_estimate"]
```

Edit `apps/cli/src/video_editor_cli/main.py` — add import near line 62:

```python
from video_editor_cli.commands.estimate import register_estimate
```

Add registration call near line 2757 (after existing register calls):

```python
register_estimate(app)
```

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/estimate.py \
  apps/cli/src/video_editor_cli/commands/__init__.py \
  apps/cli/src/video_editor_cli/main.py \
  tests/test_estimate.py
git commit -m "feat: add ve estimate command with cost tally and approval gate

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `ve finalize` Command

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/finalize.py`
- Modify: `apps/cli/src/video_editor_cli/commands/__init__.py`
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: `ExportSkill`, `PostRenderReviewSkill`, `VerifySkill` from existing skills
- Produces: `register_finalize(app)` function; `ve finalize` CLI command

- [ ] **Step 1: Implement the finalize command**

Create `apps/cli/src/video_editor_cli/commands/finalize.py`:

```python
"""ve finalize: produce the final deliverable after preview approval."""

from __future__ import annotations

from pathlib import Path

import structlog
import typer
from export.export import ExportSkill
from review.review import PostRenderReviewSkill
from verify.verify import VerifySkill
from video_editor_state.job import JobContext
from video_editor_state.ledger_hooks import record_pipeline_complete

logger = structlog.get_logger(__name__)


def finalize_command(
    job_id: str,
    work_root: str = "/work",
    output_root: str = "/output",
    output_name: str = "final.mp4",
    config_path: Path | None = None,
    run_review: bool = False,
    run_verify: bool = False,
) -> Path:
    """Export the final MP4, optionally running review and verify gates."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
    )
    ctx.ensure_dirs()
    manifest = ctx.manifest()

    # Determine input: use edited plan video if preview was done,
    # otherwise use the original rendered video.
    final_path = ctx.output_dir / output_name

    # If preview stage completed, the rendered video should already exist
    # at the output path from the preview's render_fablecut_project call.
    # Otherwise, look for the standard render output.
    if not final_path.exists():
        # Fall back to the standard render output
        rendered = ctx.output_dir / "final.mp4"
        if rendered.exists():
            final_path = rendered

    if not final_path.exists():
        typer.echo(
            "No rendered video found. Run ve render or ve preview first.",
            err=True,
        )
        raise typer.Exit(1)

    # Export (copy to final location + generate report)
    export = ExportSkill(ctx)
    final = export.run(output_name=output_name)
    typer.echo(f"Final export: {final}")

    # Optional post-render review
    if run_review and not manifest.is_stage_complete("review"):
        review_skill = PostRenderReviewSkill(ctx)
        review_report = review_skill.run(final)
        typer.echo(
            f"Post-render review: {review_report.overall} "
            f"({len(review_report.checks)} checks)"
        )
        export.append_post_render_review()

    # Optional verification gate
    if run_verify:
        import asyncio

        verify_skill = VerifySkill(ctx)
        try:
            report = asyncio.run(verify_skill.run())
            typer.echo(f"Verification: {report.overall} ({len(report.checks)} checks)")
        except Exception as exc:
            typer.echo(f"Verification failed: {exc}", err=True)
            raise typer.Exit(1) from exc

    # Mark finalize stage
    manifest.stage_done("finalize", {"final": str(final)})
    ctx.store.save()

    # Record pipeline completion in ledger
    record_pipeline_complete(ctx, final_video_path=str(final), status="complete")

    return final


def register_finalize(app: typer.Typer) -> None:
    """Register the finalize command on the Typer app."""

    @app.command(name="finalize")
    def finalize(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str = typer.Option("/work", "--work-root", help="Work directory root"),
        output_root: str = typer.Option("/output", "--output-root", help="Output directory root"),
        output_name: str = typer.Option("final.mp4", "--output-name", help="Output video filename"),
        config_path: Path | None = typer.Option(None, "--config", "-c", help="Path to project config.yaml"),
        review: bool = typer.Option(False, "--review", help="Run post-render review gate"),
        verify: bool = typer.Option(False, "--verify", help="Run requirements verification gate"),
    ) -> None:
        """Export the final MP4 after preview approval."""
        finalize_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            output_name=output_name,
            config_path=config_path,
            run_review=review,
            run_verify=verify,
        )
```

- [ ] **Step 2: Register the command**

Edit `apps/cli/src/video_editor_cli/commands/__init__.py`:

```python
"""CLI command modules."""

from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.commands.finalize import register_finalize

__all__ = ["register_estimate", "register_finalize"]
```

Edit `apps/cli/src/video_editor_cli/main.py` — add import:

```python
from video_editor_cli.commands.finalize import register_finalize
```

Add registration call:

```python
register_finalize(app)
```

- [ ] **Step 3: Verify the command registers**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/ve finalize --help
```

Expected: shows help text for the finalize command

- [ ] **Step 4: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/finalize.py \
  apps/cli/src/video_editor_cli/commands/__init__.py \
  apps/cli/src/video_editor_cli/main.py
git commit -m "feat: add ve finalize command for post-preview export

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `ve preview` Command (FableCut Edit Loop)

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/preview.py`
- Create: `tests/test_preview.py`
- Modify: `apps/cli/src/video_editor_cli/commands/__init__.py`
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: `export_to_fablecut`, `import_from_fablecut`, `render_fablecut_project` from `video_editor_fablecut_bridge`
- Consumes: `ScenePlan` from `video_editor_engine.models.scene_plan`
- Produces: `register_preview(app)` function; `ve preview` CLI command

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_preview.py`:

```python
"""Integration tests for the ve preview command (FableCut edit loop)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video_editor_cli.commands.preview import (
    _find_fablecut_dir,
    _is_fablecut_running,
    _export_project,
)


class TestPreviewHelpers:
    def test_find_fablecut_dir_env_var(self, tmp_path: Path) -> None:
        """FABLECUT_PATH env var should take precedence."""
        fablecut_dir = tmp_path / "my-fablecut"
        fablecut_dir.mkdir()
        with patch.dict("os.environ", {"FABLECUT_PATH": str(fablecut_dir)}):
            result = _find_fablecut_dir()
            assert result == fablecut_dir

    def test_find_fablecut_dir_default(self, tmp_path: Path) -> None:
        """Default path is REPO_ROOT/tools/FableCut."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("video_editor_cli.commands.preview.REPO_ROOT", tmp_path):
                fablecut_dir = tmp_path / "tools" / "FableCut"
                fablecut_dir.mkdir(parents=True)
                result = _find_fablecut_dir()
                assert result == fablecut_dir

    def test_find_fablecut_dir_not_found(self, tmp_path: Path) -> None:
        """Should raise SystemExit when FableCut is not found."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("video_editor_cli.commands.preview.REPO_ROOT", tmp_path):
                with pytest.raises(SystemExit):
                    _find_fablecut_dir()

    def test_is_fablecut_running_true(self) -> None:
        """Should detect running server via HTTP check."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("httpx.get", return_value=mock_response):
            assert _is_fablecut_running() is True

    def test_is_fablecut_running_false(self) -> None:
        """Should return False when server is not reachable."""
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            assert _is_fablecut_running() is False

    def test_export_project_creates_files(self, tmp_path: Path) -> None:
        """Export should create project.json and media/ directory."""
        from video_editor_engine.models.scene_plan import Scene, ScenePlan

        plan = ScenePlan(
            job_id="test",
            script="test",
            aspect_ratio="16:9",
            music_mood="cinematic",
            scenes=[
                Scene(
                    id="scene_001",
                    type="narration",
                    duration_seconds=5.0,
                    narration="Hello",
                    visual_description="A test image",
                )
            ],
        )
        project_dir = tmp_path / "fablecut"
        result = _export_project(plan, project_dir)
        assert (result / "project.json").exists()
        assert (result / "media").is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_preview.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement the preview command**

Create `apps/cli/src/video_editor_cli/commands/preview.py`:

```python
"""ve preview: export to FableCut, open browser, interactive edit loop."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import structlog
import typer
from video_editor_engine.models.scene_plan import ScenePlan
from video_editor_fablecut_bridge.exporter import export_to_fablecut
from video_editor_fablecut_bridge.importer import import_from_fablecut
from video_editor_fablecut_bridge.renderer import render_fablecut_project
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_state.job import JobContext

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[5]
FABLECUT_URL = "http://localhost:7777"
FABLECUT_DEFAULT_DIR = REPO_ROOT / "tools" / "FableCut"


def _find_fablecut_dir() -> Path:
    """Resolve the FableCut installation directory."""
    env_path = os.getenv("FABLECUT_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_dir():
            return candidate

    if FABLECUT_DEFAULT_DIR.is_dir():
        return FABLECUT_DEFAULT_DIR

    typer.echo(
        "FableCut not found. Clone it to tools/FableCut/ or set FABLECUT_PATH.",
        err=True,
    )
    raise typer.Exit(1)


def _is_fablecut_running() -> bool:
    """Check if FableCut server is already running on localhost:7777."""
    try:
        response = httpx.get(FABLECUT_URL, timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


def _start_fablecut_server(fablecut_dir: Path) -> subprocess.Popen[bytes] | None:
    """Start the FableCut Node.js server as a background process.

    Returns the Popen object, or None if already running.
    """
    if _is_fablecut_running():
        logger.info("fablecut_already_running")
        return None

    server_js = fablecut_dir / "server.js"
    if not server_js.exists():
        typer.echo(f"server.js not found in {fablecut_dir}", err=True)
        raise typer.Exit(1)

    logger.info("fablecut_starting", dir=str(fablecut_dir))
    proc = subprocess.Popen(
        ["node", "server.js"],
        cwd=str(fablecut_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Write PID file
    pid_file = fablecut_dir / ".fablecut_server.pid"
    pid_file.write_text(str(proc.pid))

    # Wait for server to be ready
    for _ in range(30):
        time.sleep(0.5)
        if _is_fablecut_running():
            logger.info("fablecut_ready")
            return proc

    typer.echo("FableCut server did not start within 15 seconds.", err=True)
    raise typer.Exit(1)


def _stop_fablecut_server(proc: subprocess.Popen[bytes] | None, fablecut_dir: Path) -> None:
    """Stop the FableCut server and clean up the PID file."""
    pid_file = fablecut_dir / ".fablecut_server.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()


def _export_project(plan: ScenePlan, project_dir: Path) -> Path:
    """Export a ScenePlan to a FableCut project directory."""
    return export_to_fablecut(
        scene_plan=plan,
        project_root=project_dir,
        fps=30,
        width=1920,
        height=1080,
    )


def _sync_to_editor(source_dir: Path, fablecut_dir: Path) -> None:
    """Copy the exported project into the FableCut editor directory."""
    import shutil

    src_json = source_dir / "project.json"
    src_media = source_dir / "media"

    dst_json = fablecut_dir / "project.json"
    dst_media = fablecut_dir / "media"

    # Back up editor defaults if not already done
    backup_json = fablecut_dir / "project.json.ve_backup"
    backup_media = fablecut_dir / "media.ve_backup"
    if dst_json.exists() and not backup_json.exists():
        shutil.copy2(dst_json, backup_json)
    if dst_media.exists() and not backup_media.exists():
        shutil.copytree(dst_media, backup_media)

    # Sync project files
    shutil.copy2(src_json, dst_json)
    if dst_media.exists():
        shutil.rmtree(dst_media)
    if src_media.exists():
        shutil.copytree(src_media, dst_media)

    # Write state file for finalize
    state = {"source_project_dir": str(source_dir)}
    (fablecut_dir / ".fablecut_state.json").write_text(json.dumps(state))


def _run_edit_loop(ctx: JobContext, project_dir: Path, fablecut_dir: Path) -> int:
    """Run the interactive edit loop. Returns the number of edit iterations."""
    edit_count = 0

    typer.echo("\n" + "=" * 60)
    typer.echo("FableCut editor is open at http://localhost:7777")
    typer.echo("Edit the timeline, then type a command.")
    typer.echo("=" * 60)
    typer.echo("\nCommands:")
    typer.echo("  done, finalize  — finish editing and proceed to final export")
    typer.echo("  status          — show current state")
    typer.echo("  help            — show this help")
    typer.echo()

    while True:
        try:
            cmd = typer.prompt("fablecut", default="status", show_default=False).strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nExiting preview loop.")
            break

        if cmd in ("done", "finalize"):
            typer.echo("Importing edited timeline and rendering final video...")

            # Import edited project
            edited_json = fablecut_dir / "project.json"
            if edited_json.exists():
                plan_path = ctx.work_dir / "plan" / "scene_plan_with_assets.json"
                if plan_path.exists():
                    original_plan = ScenePlan.model_validate_json(
                        plan_path.read_text(encoding="utf-8")
                    )
                    try:
                        updated_plan = import_from_fablecut(edited_json, original_plan)
                        edited_plan_path = ctx.work_dir / "plan" / "scene_plan_edited.json"
                        edited_plan_path.write_text(
                            updated_plan.model_dump_json(indent=2), encoding="utf-8"
                        )
                        typer.echo(f"Edited plan saved to {edited_plan_path}")
                    except Exception as exc:
                        logger.warning("fablecut_import_failed", error=str(exc))
                        typer.echo(f"Warning: could not import edits: {exc}")

            # Render final video from FableCut project
            output_path = ctx.output_dir / "final.mp4"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                render_fablecut_project(
                    project_path=edited_json,
                    output_path=output_path,
                    media_dir=fablecut_dir / "media",
                    render_options=RenderOptions(),
                )
                typer.echo(f"Final render: {output_path}")
            except Exception as exc:
                logger.warning("fablecut_render_failed", error=str(exc))
                typer.echo(f"Warning: FableCut render failed: {exc}")
                typer.echo("Falling back to Remotion render of the edited plan.")
                # Fallback: render via Remotion
                from video_editor_cli.remotion_renderer import render_scene_plan

                edited_plan_path = ctx.work_dir / "plan" / "scene_plan_edited.json"
                if edited_plan_path.exists():
                    render_scene_plan(ctx, edited_plan_path, output_path)

            # Mark preview stage complete
            manifest = ctx.manifest()
            manifest.stage_done("preview", {
                "fablecut_project": str(project_dir),
                "edited_plan": str(ctx.work_dir / "plan" / "scene_plan_edited.json"),
                "final_video": str(output_path),
                "edit_count": edit_count,
            })
            ctx.store.save()

            typer.echo("Preview complete. Run `ve finalize` to export the final deliverable.")
            break

        elif cmd == "status":
            typer.echo(f"Job: {ctx.job_id}")
            typer.echo(f"Work dir: {ctx.work_dir}")
            typer.echo(f"FableCut: {FABLECUT_URL}")
            typer.echo(f"Edit iterations: {edit_count}")

        elif cmd == "help":
            typer.echo("Commands: done, finalize, status, help")

        else:
            typer.echo(f"Unknown command: {cmd!r}. Type 'help' for available commands.")

    return edit_count


def preview_command(
    job_id: str,
    work_root: str = "/work",
    output_root: str = "/output",
    config_path: Path | None = None,
    project_dir: Path | None = None,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
) -> None:
    """Export to FableCut, open browser, and run the interactive edit loop."""
    ctx = JobContext(
        job_id=job_id,
        work_root=work_root,
        output_root=output_root,
    )
    ctx.ensure_dirs()

    # Read the enriched scene plan
    plan_path = ctx.work_dir / "plan" / "scene_plan_with_assets.json"
    if not plan_path.exists():
        typer.echo(f"Scene plan not found: {plan_path}", err=True)
        typer.echo("Run ve assets first.", err=True)
        raise typer.Exit(1)

    plan = ScenePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    # Resolve project directory
    if project_dir is None:
        project_dir = ctx.work_dir / "fablecut"

    # Find FableCut installation
    fablecut_dir = _find_fablecut_dir()

    # Export to FableCut
    typer.echo(f"Exporting to FableCut: {project_dir}")
    _export_project(plan, project_dir)

    # Sync to editor
    _sync_to_editor(project_dir, fablecut_dir)

    # Start FableCut server
    proc = _start_fablecut_server(fablecut_dir)

    # Open browser
    try:
        webbrowser.open(FABLECUT_URL)
    except Exception:
        typer.echo(f"Could not open browser. Navigate to {FABLECUT_URL}")

    # Run edit loop
    try:
        _run_edit_loop(ctx, project_dir, fablecut_dir)
    finally:
        # Cleanup
        _stop_fablecut_server(proc, fablecut_dir)


def register_preview(app: typer.Typer) -> None:
    """Register the preview command on the Typer app."""

    @app.command(name="preview")
    def preview(
        job_id: str = typer.Argument(..., help="Job identifier"),
        work_root: str = typer.Option("/work", "--work-root", help="Work directory root"),
        output_root: str = typer.Option("/output", "--output-root", help="Output directory root"),
        config_path: Path | None = typer.Option(None, "--config", "-c", help="Path to project config.yaml"),
        project_dir: Path | None = typer.Option(None, "--project-dir", "-d", help="FableCut project directory"),
        fps: int = typer.Option(30, "--fps", help="Timeline FPS"),
        width: int = typer.Option(1920, "--width", help="Timeline width"),
        height: int = typer.Option(1080, "--height", help="Timeline height"),
    ) -> None:
        """Export to FableCut web editor for manual timeline editing."""
        preview_command(
            job_id=job_id,
            work_root=work_root,
            output_root=output_root,
            config_path=config_path,
            project_dir=project_dir,
            fps=fps,
            width=width,
            height=height,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/test_preview.py -v
```

Expected: 6 tests PASS

- [ ] **Step 5: Register the command**

Edit `apps/cli/src/video_editor_cli/commands/__init__.py`:

```python
"""CLI command modules."""

from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.commands.finalize import register_finalize
from video_editor_cli.commands.preview import register_preview

__all__ = ["register_estimate", "register_finalize", "register_preview"]
```

Edit `apps/cli/src/video_editor_cli/main.py` — add import:

```python
from video_editor_cli.commands.preview import register_preview
```

Add registration call:

```python
register_preview(app)
```

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/preview.py \
  apps/cli/src/video_editor_cli/commands/__init__.py \
  apps/cli/src/video_editor_cli/main.py \
  tests/test_preview.py
git commit -m "feat: add ve preview command with FableCut edit loop

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: `ve update-pricing` Command

**Files:**
- Create: `apps/cli/src/video_editor_cli/commands/update_pricing.py`
- Modify: `apps/cli/src/video_editor_cli/commands/__init__.py`
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: `PricingRegistry` from Task 1
- Produces: `register_update_pricing(app)` function; `ve update-pricing` CLI command

- [ ] **Step 1: Implement the update-pricing command**

Create `apps/cli/src/video_editor_cli/commands/update_pricing.py`:

```python
"""ve update-pricing: refresh the provider pricing registry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import structlog
import typer
import yaml
from video_editor_asset_ingestion.pricing import DEFAULT_PRICING_PATH, PricingRegistry

logger = structlog.get_logger(__name__)


def update_pricing_command() -> None:
    """Update the last_updated timestamp on pricing.yaml.

    In the future, this could fetch live pricing from provider APIs.
    For now, it bumps the freshness timestamp so the staleness warning
    is suppressed for another 30 days.
    """
    registry = PricingRegistry.load()

    if not DEFAULT_PRICING_PATH.exists():
        typer.echo(
            f"pricing.yaml not found at {DEFAULT_PRICING_PATH}. "
            "A default file will be created."
        )

    # Write the current pricing data with an updated timestamp
    data = {
        "last_updated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "providers": registry.providers,
    }

    DEFAULT_PRICING_PATH.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    typer.echo(f"Pricing data updated: {DEFAULT_PRICING_PATH}")
    typer.echo(f"Last updated: {data['last_updated']}")
    typer.echo(f"Providers: {len(data['providers'])}")


def register_update_pricing(app: typer.Typer) -> None:
    """Register the update-pricing command on the Typer app."""

    @app.command(name="update-pricing")
    def update_pricing() -> None:
        """Refresh the provider pricing registry timestamp.

        Run this when you've verified that the pricing data in pricing.yaml
        is still accurate. In the future, this command will fetch live
        pricing from provider APIs.
        """
        update_pricing_command()
```

- [ ] **Step 2: Register the command**

Edit `apps/cli/src/video_editor_cli/commands/__init__.py`:

```python
"""CLI command modules."""

from video_editor_cli.commands.estimate import register_estimate
from video_editor_cli.commands.finalize import register_finalize
from video_editor_cli.commands.preview import register_preview
from video_editor_cli.commands.update_pricing import register_update_pricing

__all__ = [
    "register_estimate",
    "register_finalize",
    "register_preview",
    "register_update_pricing",
]
```

Edit `apps/cli/src/video_editor_cli/main.py` — add import:

```python
from video_editor_cli.commands.update_pricing import register_update_pricing
```

Add registration call:

```python
register_update_pricing(app)
```

- [ ] **Step 3: Verify the command works**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/ve update-pricing
```

Expected: "Pricing data updated: .../pricing.yaml" with today's date

- [ ] **Step 4: Commit**

```bash
git add apps/cli/src/video_editor_cli/commands/update_pricing.py \
  apps/cli/src/video_editor_cli/commands/__init__.py \
  apps/cli/src/video_editor_cli/main.py
git commit -m "feat: add ve update-pricing command for pricing freshness

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: `ve run` Integration (--auto-approve, --skip-preview)

**Files:**
- Modify: `apps/cli/src/video_editor_cli/main.py`

**Interfaces:**
- Consumes: `register_estimate`, `register_preview`, `register_finalize` from Tasks 2-5
- Produces: `--auto-approve`/`-y` and `--skip-preview` flags on `ve run`

- [ ] **Step 1: Add flags to ve run**

Edit `apps/cli/src/video_editor_cli/main.py` — add new option singletons near the existing option definitions (around line 340):

```python
_AUTO_APPROVE_OPT = typer.Option(
    False, "--auto-approve", "-y", help="Skip the cost estimate approval prompt"
)
_SKIP_PREVIEW_OPT = typer.Option(
    False, "--skip-preview", help="Skip the FableCut preview stage"
)
```

- [ ] **Step 2: Add parameters to the run function signature**

Edit the `run` function signature (around line 1165) to add the new parameters after `budget`:

```python
    auto_approve: bool = _AUTO_APPROVE_OPT,
    skip_preview: bool = _SKIP_PREVIEW_OPT,
```

- [ ] **Step 3: Add estimate gate logic to the run function**

After the template selection and before asset generation, add the estimate gate. Insert after line 1222 (after `typer.echo(f"Template: ...")`):

```python
    # --- Cost estimation gate ---
    if selected.pipeline_kind in ("script_to_video", "storyteller", "music_video"):
        from video_editor_cli.commands.estimate import estimate_command

        plan_path = ctx.work_dir / "plan" / "scene_plan.json"
        if plan_path.exists() and not ctx.manifest().is_stage_complete("estimate"):
            typer.echo("")
            estimate_command(
                job_id=job_id,
                work_root=work_root,
                output_root=output_root,
                config_path=config_path,
                auto_approve=auto_approve,
            )
```

- [ ] **Step 4: Add preview hint at the end of run**

After the pipeline completes (before the final `typer.echo`), add a hint about the preview stage. Insert before the return statement of each pipeline branch:

For the storyteller branch (before line 1301 `return`):
```python
        if not skip_preview:
            typer.echo("\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export.")
```

For the music_video branch (before line 1344 `return`):
```python
        if not skip_preview:
            typer.echo("\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export.")
```

For the skills/file branch (before line 1424 `return`):
```python
        if not skip_preview:
            typer.echo("\nRun `ve preview` to edit the timeline in FableCut, then `ve finalize` to export.")
```

- [ ] **Step 5: Verify the flags appear in help**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/ve run --help
```

Expected: `--auto-approve`/`-y` and `--skip-preview` appear in the options list

- [ ] **Step 6: Commit**

```bash
git add apps/cli/src/video_editor_cli/main.py
git commit -m "feat: add --auto-approve and --skip-preview flags to ve run

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: Wire BudgetGovernor to PricingRegistry

**Files:**
- Modify: `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py`

**Interfaces:**
- Consumes: `PricingRegistry` from Task 1
- Produces: `BudgetGovernor` now uses USD from pricing registry

- [ ] **Step 1: Update BudgetGovernor to use PricingRegistry**

Edit `packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py` — add import at top:

```python
from video_editor_asset_ingestion.pricing import PricingRegistry, estimate_image_cost, estimate_tts_cost, estimate_video_cost, estimate_music_cost
```

Update the `estimate_cost` function to delegate to the pricing registry:

```python
def estimate_cost(
    provider: str,
    task_type: str,
    pricing: dict[str, dict[str, float]] | None = None,
    registry: PricingRegistry | None = None,
    model: str = "default",
    char_count: int = 0,
    duration_s: float = 0.0,
    count: int = 1,
) -> float:
    """Return the estimated USD cost for one provider/task request.

    Prefers the new PricingRegistry (model-level USD costs). Falls back
    to the legacy DEFAULT_PRICING table (abstract credits) when no
    registry is available.
    """
    if registry is not None:
        if task_type == "tts":
            return estimate_tts_cost(provider, model, char_count, registry)
        elif task_type in ("image", "data_viz"):
            return estimate_image_cost(provider, model, count, registry)
        elif task_type == "video":
            return estimate_video_cost(provider, model, duration_s, registry)
        elif task_type == "music":
            return estimate_music_cost(provider, model, duration_s, registry)
        # For other types, fall through to legacy pricing

    # Legacy credit-based pricing (deprecated, kept for backward compat)
    table = pricing if pricing is not None else DEFAULT_PRICING
    return table.get(task_type, {}).get(provider, 0.0)
```

- [ ] **Step 2: Run existing budget tests**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/ -k "budget" -v
```

Expected: existing budget tests still PASS

- [ ] **Step 3: Commit**

```bash
git add packages/asset_ingestion/src/video_editor_asset_ingestion/budget.py
git commit -m "feat: wire BudgetGovernor to PricingRegistry for USD costs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Add pricing section to config.yaml

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Add pricing section**

Edit `config.yaml` — add after the `paths:` section:

```yaml
pricing:
  # Path to the provider pricing registry file.
  path: pricing.yaml
  # Maximum age in days before pricing data is considered stale.
  # Stale data triggers a warning but does not block the pipeline.
  max_age_days: 30
```

- [ ] **Step 2: Commit**

```bash
git add config.yaml
git commit -m "feat: add pricing section to config.yaml

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: End-to-End Verification

- [ ] **Step 1: Run the full test suite**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass, no regressions

- [ ] **Step 2: Verify all new commands are registered**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/ve --help
```

Expected: `estimate`, `preview`, `finalize`, `update-pricing` appear in command list

- [ ] **Step 3: Test estimate with the saju-lead-gen job**

```bash
cd /mnt/data2/git_repos/video-editor && .venv/bin/ve estimate saju-lead-gen --work-root projects --output-root outputs -y
```

Expected: cost table displayed, `cost_estimate.json` written

- [ ] **Step 4: Commit any final changes**

```bash
git add -A && git diff --cached --stat
git commit -m "chore: final integration verification

Co-Authored-By: Claude <noreply@anthropic.com>"
```
