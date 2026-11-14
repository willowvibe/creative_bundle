"""Tests for the formalized brand profile system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_state.brand_profile import (
    BrandResolutionError,
    resolve_brand_profile,
)


def _brand_data(brand: str = "Acme") -> dict[str, object]:
    return {
        "brand": brand,
        "colors": {"primary": "#ff0000", "text": "#ffffff"},
        "fonts": {"captions": "Inter", "title": "Roboto"},
        "logo": {"path": "logo.png"},
        "defaults": {"lower_third_duration": 3.0},
    }


def test_resolve_global_brand(tmp_path: Path) -> None:
    """A single brand.json at presets_dir resolves as the global brand."""
    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    profile = resolve_brand_profile(tmp_path)
    assert profile is not None
    assert profile.brand == "GlobalCo"
    assert profile.colors["primary"] == "#ff0000"


def test_resolve_job_override(tmp_path: Path) -> None:
    """A job-specific brand overrides the global default."""
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir()
    (tmp_path / "brand.json").write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    (brands_dir / "job_123.json").write_text(json.dumps(_brand_data("JobCo")), encoding="utf-8")
    profile = resolve_brand_profile(tmp_path, job_id="job_123")
    assert profile is not None
    assert profile.brand == "JobCo"


def test_resolve_tenant_override(tmp_path: Path) -> None:
    """A tenant-specific brand resolves when no job override exists."""
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir()
    (tmp_path / "brand.json").write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    (brands_dir / "tenant_a.json").write_text(json.dumps(_brand_data("TenantA")), encoding="utf-8")
    profile = resolve_brand_profile(tmp_path, tenant_id="tenant_a")
    assert profile is not None
    assert profile.brand == "TenantA"


def test_job_wins_over_tenant(tmp_path: Path) -> None:
    """Job-specific brand takes precedence over tenant-specific brand."""
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir()
    (tmp_path / "brand.json").write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    (brands_dir / "tenant_a.json").write_text(json.dumps(_brand_data("TenantA")), encoding="utf-8")
    (brands_dir / "job_123.json").write_text(json.dumps(_brand_data("JobCo")), encoding="utf-8")
    profile = resolve_brand_profile(tmp_path, tenant_id="tenant_a", job_id="job_123")
    assert profile is not None
    assert profile.brand == "JobCo"


def test_missing_brand_raises(tmp_path: Path) -> None:
    """When no brand profile exists and raise_on_missing is True, raise."""
    with pytest.raises(BrandResolutionError, match="No brand profile found"):
        resolve_brand_profile(tmp_path)


def test_missing_brand_returns_none_when_allowed(tmp_path: Path) -> None:
    """raise_on_missing=False returns None instead of raising."""
    assert resolve_brand_profile(tmp_path, raise_on_missing=False) is None


def test_malformed_json_skipped_to_fallback(tmp_path: Path) -> None:
    """A malformed override file is skipped and the global fallback is used."""
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir()
    (brands_dir / "job_123.json").write_text("not json", encoding="utf-8")
    (tmp_path / "brand.json").write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    profile = resolve_brand_profile(tmp_path, job_id="job_123")
    assert profile is not None
    assert profile.brand == "GlobalCo"


def test_tenant_id_path_traversal_is_sanitized(tmp_path: Path) -> None:
    """A malicious tenant id cannot escape the brands directory."""
    brands_dir = tmp_path / "brands"
    brands_dir.mkdir()
    (tmp_path / "brand.json").write_text(json.dumps(_brand_data("GlobalCo")), encoding="utf-8")
    # This would try to read outside brands/ if not sanitized.
    profile = resolve_brand_profile(tmp_path, tenant_id="../../etc/passwd")
    assert profile is not None
    assert profile.brand == "GlobalCo"


def test_brand_profile_validation_requires_brand_field(tmp_path: Path) -> None:
    """The Pydantic model rejects a missing brand field."""
    from pydantic import ValidationError

    brand_path = tmp_path / "brand.json"
    brand_path.write_text(json.dumps({"colors": {}}), encoding="utf-8")
    with pytest.raises(ValidationError):
        resolve_brand_profile(tmp_path)
