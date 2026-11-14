"""Preset loader for graphics skill."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from graphics.models import Brand, Preset, merge_preset_with_brand

logger = structlog.get_logger(__name__)


class PresetError(Exception):
    """Raised when a preset cannot be loaded or is invalid."""


PRESET_NAMES = {
    "short_explainer",
    "tiktok_raw",
    "long_form",
    "saju_long_form",
    "vlogger_youtube",
    "vlogger_shorts",
    "cooking_show",
    "event_recap",
    "podcast_studio",
    "tech_tutorial",
}


def load_brand(presets_dir: Path | str) -> Brand:
    presets_dir = Path(presets_dir)
    brand_path = presets_dir / "brand.json"
    if not brand_path.exists():
        raise PresetError(f"Brand file not found: {brand_path}")
    with open(brand_path, encoding="utf-8") as f:
        data = json.load(f)
    return Brand(**data)


def load_brand_for_job(
    presets_dir: Path | str,
    tenant_id: str | None = None,
    job_id: str | None = None,
) -> Brand:
    """Resolve the brand profile for a job.

    Resolution order (first hit wins):
      1. ``<presets_dir>/brands/<job_id>.json``        — project-specific
      2. ``<presets_dir>/brands/<tenant_id>.json``     — tenant-specific
      3. ``<presets_dir>/brand.json``                  — global default

    ``job_id`` and ``tenant_id`` are sanitized to a safe filename component
    before lookup. A missing override file is silently skipped (the next
    candidate is tried) — only the final fallback raises ``PresetError``.
    """
    presets_dir = Path(presets_dir)
    brands_dir = presets_dir / "brands"

    def _safe_component(value: str) -> str:
        # Restrict to a strict filename-safe alphabet so a malicious
        # ``tenant_id`` like ``"../../etc/passwd"`` cannot escape the
        # brand directory. Anything outside [A-Za-z0-9._-] is replaced
        # with an underscore.
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)

    candidates: list[Path] = []
    if job_id:
        candidates.append(brands_dir / f"{_safe_component(job_id)}.json")
    if tenant_id:
        candidates.append(brands_dir / f"{_safe_component(tenant_id)}.json")
    candidates.append(presets_dir / "brand.json")

    last_missing: Path | None = None
    for path in candidates:
        if not path.exists():
            last_missing = path
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        brand = Brand(**data)
        logger.info(
            "brand_resolved",
            source=str(path),
            brand=brand.brand,
            tenant_id=tenant_id,
            job_id=job_id,
        )
        return brand

    # All candidates exhausted — fall through to the historical error.
    raise PresetError(
        f"Brand file not found: {last_missing or (presets_dir / 'brand.json')}. "
        f"Tried {len(candidates)} candidate(s)."
    )


def load_preset(name: str, presets_dir: Path | str, brand: Brand | None = None) -> Preset:
    presets_dir = Path(presets_dir)
    if name not in PRESET_NAMES:
        raise PresetError(f"Unknown preset '{name}'. Available: {sorted(PRESET_NAMES)}")

    preset_path = presets_dir / f"{name}.json"
    if not preset_path.exists():
        raise PresetError(f"Preset file not found: {preset_path}")

    with open(preset_path, encoding="utf-8") as f:
        data = json.load(f)

    preset = Preset(**data)
    if brand is not None:
        preset = merge_preset_with_brand(preset, brand)

    logger.info("preset_loaded", name=name, width=preset.width, height=preset.height)
    return preset
