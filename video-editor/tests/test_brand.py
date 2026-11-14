"""Tests for the brand resolver and per-tenant resolution chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from graphics.preset import PresetError, load_brand, load_brand_for_job


def _write_brand(path: Path, brand: str) -> None:
    path.write_text(
        json.dumps(
            {
                "brand": brand,
                "colors": {
                    "primary": "#000000",
                    "secondary": "#111111",
                    "background": "#222222",
                    "text": "#FFFFFF",
                },
                "fonts": {"headline": "Coolvetica", "body": "Inter", "captions": "Coolvetica"},
                "logo": {"path": "assets/logos/logo.png", "width": 160, "height": 160},
                "defaults": {
                    "caption_animation": "pop_in",
                    "music_duck_db": -14,
                    "final_loudness": -16,
                },
            }
        ),
        encoding="utf-8",
    )


def test_load_brand_returns_default(tmp_path: Path) -> None:
    """The historical primitive: single global brand.json."""
    _write_brand(tmp_path / "brand.json", "default-brand")
    brand = load_brand(tmp_path)
    assert brand.brand == "default-brand"


def test_load_brand_for_job_falls_back_to_global(tmp_path: Path) -> None:
    """No override files present -> resolver must fall through to global."""
    _write_brand(tmp_path / "brand.json", "global")
    brand = load_brand_for_job(tmp_path, tenant_id="acme", job_id="proj-1")
    assert brand.brand == "global"


def test_load_brand_for_job_tenant_override(tmp_path: Path) -> None:
    """A tenant-specific brand file wins over the global default."""
    _write_brand(tmp_path / "brand.json", "global")
    (tmp_path / "brands").mkdir()
    _write_brand(tmp_path / "brands" / "acme.json", "acme-tenant")
    brand = load_brand_for_job(tmp_path, tenant_id="acme", job_id="proj-1")
    assert brand.brand == "acme-tenant"


def test_load_brand_for_job_project_override_beats_tenant(tmp_path: Path) -> None:
    """Job-specific brand wins over tenant-specific which wins over global."""
    _write_brand(tmp_path / "brand.json", "global")
    brands = tmp_path / "brands"
    brands.mkdir()
    _write_brand(brands / "acme.json", "acme-tenant")
    _write_brand(brands / "proj-1.json", "project-specific")
    brand = load_brand_for_job(tmp_path, tenant_id="acme", job_id="proj-1")
    assert brand.brand == "project-specific"


def test_load_brand_for_job_raises_when_all_missing(tmp_path: Path) -> None:
    """No brand.json and no override files -> PresetError."""
    (tmp_path / "brands").mkdir()
    with pytest.raises(PresetError, match="Brand file not found"):
        load_brand_for_job(tmp_path, tenant_id="acme", job_id="proj-1")


def test_load_brand_for_job_sanitizes_path_traversal(tmp_path: Path) -> None:
    """A tenant_id of '../etc/passwd' must NOT escape the brands/ dir.

    The sanitizer replaces '/' and '..' characters with '_', so the
    resolver looks up '.._etc_passwd.json' (which doesn't exist) and
    falls through to the global default.
    """
    _write_brand(tmp_path / "brand.json", "global")
    brand = load_brand_for_job(tmp_path, tenant_id="../../etc/passwd", job_id="x")
    assert brand.brand == "global"


def test_load_brand_for_job_missing_override_silently_falls_through(
    tmp_path: Path,
) -> None:
    """A missing override file is silently skipped (not raised)."""
    _write_brand(tmp_path / "brand.json", "global")
    (tmp_path / "brands").mkdir()
    # Tenant file doesn't exist; resolver must keep looking.
    brand = load_brand_for_job(tmp_path, tenant_id="nonexistent", job_id="nonexistent")
    assert brand.brand == "global"


def test_load_brand_for_job_only_job_id_provided(tmp_path: Path) -> None:
    """When only job_id is supplied, only the project override is tried."""
    _write_brand(tmp_path / "brand.json", "global")
    brands = tmp_path / "brands"
    brands.mkdir()
    _write_brand(brands / "saju-2026-07-03.json", "saju-brand")
    brand = load_brand_for_job(tmp_path, job_id="saju-2026-07-03")
    assert brand.brand == "saju-brand"
