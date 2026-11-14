"""ve update-pricing: refresh the provider pricing registry."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
import typer
import yaml
from video_editor_asset_ingestion.pricing import DEFAULT_PRICING_PATH, PricingRegistry

logger = structlog.get_logger(__name__)


def update_pricing_command() -> None:
    """Update the last_updated timestamp on pricing.yaml."""
    registry = PricingRegistry.load()

    if not DEFAULT_PRICING_PATH.exists():
        typer.echo(
            f"pricing.yaml not found at {DEFAULT_PRICING_PATH}. A default file will be created."
        )

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
    """Register the touch-pricing command on the Typer app."""

    @app.command(name="touch-pricing")
    def touch_pricing() -> None:
        """Refresh the provider pricing registry timestamp."""
        update_pricing_command()
