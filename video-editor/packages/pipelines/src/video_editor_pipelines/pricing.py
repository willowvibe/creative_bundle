"""Pricing helpers shared by the CLI estimate and the orchestrators."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from video_editor_asset_ingestion.pricing import PricingRegistry
from video_editor_asset_ingestion.providers.tts.elevenlabs_tts import (
    DEFAULT_MODEL as ELEVENLABS_DEFAULT_MODEL,
)
from video_editor_asset_ingestion.providers.tts.sarvam_tts import (
    DEFAULT_MODEL as SARVAM_DEFAULT_MODEL,
)
from video_editor_asset_ingestion.providers.visual.poyo_ai import (
    DEFAULT_IMAGE_MODEL as POYO_DEFAULT_IMAGE_MODEL,
)

# These follow the provider modules so the estimate prices what the provider will call.
DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "elevenlabs_tts": ELEVENLABS_DEFAULT_MODEL,
    "sarvam_tts": SARVAM_DEFAULT_MODEL,
    "poyo_ai": POYO_DEFAULT_IMAGE_MODEL,
    "fal_ai": "fal-ai/flux/dev",
    "pexels": "default",
    "coverr": "default",
    "mixkit": "default",
    "poyo_music": "generate-music",
    "elevenlabs_music": "eleven_music_v1",
    "openrouter": "anthropic/claude-sonnet-5.5",
    "openrouter_image": "openai/gpt-image-2",
    "openrouter_video": "google/veo-3.1-fast",
    "openrouter_music": "google/lyria-3-pro-preview",
}


def resolve_model(provider: str, asset_type: str = "", explicit: str | None = None) -> str:
    """Return the model a request will use."""
    if explicit:
        return explicit
    if asset_type:
        per_type = DEFAULT_PROVIDER_MODELS.get(f"{provider}_{asset_type}")
        if per_type is not None:
            return per_type
    return DEFAULT_PROVIDER_MODELS.get(provider, "default")


def auto_image_pricing_provider(image_chain: Sequence[str], registry: PricingRegistry) -> str:
    """The provider an ``"auto"`` still is expected to be billed to."""
    for name in image_chain:
        cost = registry.unit_cost(name, resolve_model(name, "image"), "image")
        if cost is not None and cost > 0:
            return name
    return "poyo_ai"


def format_table(items: list[dict[str, Any]], total_cost: float) -> str:
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


# Historical private spellings used by the CLI.
_resolve_model = resolve_model
_format_table = format_table
