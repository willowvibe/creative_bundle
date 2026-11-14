"""Formalized brand profile system.

A brand profile carries colors, fonts, logo, and defaults. The resolver picks
the most specific profile available for a job/tenant, falling back to a
global default. This module centralizes brand handling so any skill (graphics,
export, verify, etc.) can consume it without duplicating file lookups.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class BrandProfile(BaseModel):
    """Validated brand profile for a project or tenant."""

    brand: str
    colors: dict[str, str] = Field(default_factory=dict)
    fonts: dict[str, str] = Field(default_factory=dict)
    logo: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)


class BrandResolutionError(RuntimeError):
    """Raised when no brand profile can be resolved."""


def _safe_filename_component(value: str) -> str:
    """Sanitize ``value`` so it can only be a plain filename component.

    Replaces anything outside ``[A-Za-z0-9._-]`` with an underscore to prevent
    directory traversal via a malicious tenant or job id.
    """
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in value)


def resolve_brand_profile(
    presets_dir: Path | str,
    tenant_id: str | None = None,
    job_id: str | None = None,
    raise_on_missing: bool = True,
) -> BrandProfile | None:
    """Resolve the best brand profile for the given context.

    Resolution order (first hit wins):
      1. ``<presets_dir>/brands/<job_id>.json``        — project-specific
      2. ``<presets_dir>/brands/<tenant_id>.json``     — tenant-specific
      3. ``<presets_dir>/brand.json``                  — global default

    Missing override files are silently skipped; only the final fallback can
    raise an error, and only when ``raise_on_missing`` is True.
    """
    presets_dir = Path(presets_dir)
    brands_dir = presets_dir / "brands"

    candidates: list[Path] = []
    if job_id:
        candidates.append(brands_dir / f"{_safe_filename_component(job_id)}.json")
    if tenant_id:
        candidates.append(brands_dir / f"{_safe_filename_component(tenant_id)}.json")
    candidates.append(presets_dir / "brand.json")

    for path in candidates:
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("brand_profile_load_failed", path=str(path), error=str(exc))
            continue
        profile = BrandProfile(**data)
        logger.info(
            "brand_profile_resolved",
            source=str(path),
            brand=profile.brand,
            tenant_id=tenant_id,
            job_id=job_id,
        )
        return profile

    if raise_on_missing:
        raise BrandResolutionError(
            f"No brand profile found. Tried {len(candidates)} candidate(s): "
            f"{[str(p) for p in candidates]}"
        )
    return None
