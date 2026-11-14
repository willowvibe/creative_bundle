"""One source of truth for the storyteller quality-tier presets (TODO 6b)."""

from __future__ import annotations

STORYTELLER_TIER_PRESETS: dict[str, tuple[str, str]] = {
    "storyteller_budget": ("budget", "budget"),
    "storyteller_hd": ("standard", "standard"),
    "storyteller_premium": ("premium", "premium"),
}
