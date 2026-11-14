"""A4: pricing/models ship inside the asset package and honor overrides."""

from __future__ import annotations

from pathlib import Path

from video_editor_asset_ingestion.pricing import (
    PricingRegistry,
    default_models_path,
    default_pricing_path,
)


def test_package_data_exists() -> None:
    assert default_pricing_path().exists()
    assert default_models_path().exists()


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_package_data_matches_the_root_sources() -> None:
    import video_editor_asset_ingestion

    data_dir = Path(video_editor_asset_ingestion.__file__).parent / "data"
    for name in ("models.yaml", "pricing.yaml"):
        assert (data_dir / name).read_bytes() == (REPO_ROOT / name).read_bytes()


def test_a_pricing_override_wins(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text("last_updated: '2026-10-27'\nproviders:\n  test:\n    models: {}\n")
    monkeypatch.setenv("VIDEO_EDITOR_PRICING", str(path))

    registry = PricingRegistry.load()

    assert "test" in registry.providers
