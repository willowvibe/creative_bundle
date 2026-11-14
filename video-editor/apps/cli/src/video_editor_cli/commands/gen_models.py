"""``ve gen-models``: generate ``pricing.yaml`` from the canonical ``models.yaml``.

``models.yaml`` at the repo root is the single source of truth for provider
model costs. ``pricing.yaml`` is a generated artifact consumed by
``PricingRegistry`` — do not hand-edit it; edit ``models.yaml`` and re-run.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import typer
import yaml

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[5]
MODELS_PATH = REPO_ROOT / "models.yaml"
PRICING_PATH = REPO_ROOT / "pricing.yaml"

_HEADER = (
    "# GENERATED FILE - do not edit by hand.\n"
    "# Source: models.yaml  (regenerate with: ve gen-models)\n"
)


def generate_pricing(models: dict[str, object]) -> str:
    """Render the ``pricing.yaml`` text for a parsed ``models.yaml`` mapping."""
    data: dict[str, object] = {
        "last_updated": models.get("last_updated", ""),
        "providers": models.get("providers", {}),
    }
    equivalences = models.get("model_equivalents")
    if equivalences:
        data["model_equivalents"] = equivalences
    return _HEADER + yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def gen_models_command() -> None:
    """Regenerate ``pricing.yaml`` from ``models.yaml``."""
    if not MODELS_PATH.exists():
        typer.echo(f"models.yaml not found at {MODELS_PATH}", err=True)
        raise typer.Exit(1)

    models = yaml.safe_load(MODELS_PATH.read_text(encoding="utf-8")) or {}
    PRICING_PATH.write_text(generate_pricing(models), encoding="utf-8")
    typer.echo(f"Wrote {PRICING_PATH} from {MODELS_PATH}")
    typer.echo(f"Providers: {len(models.get('providers', {}))}")


def register_gen_models(app: typer.Typer) -> None:
    """Register the ``gen-models`` command on the Typer app."""

    @app.command(name="gen-models")
    def gen_models() -> None:
        """Regenerate pricing.yaml from models.yaml (single source of truth)."""
        gen_models_command()
