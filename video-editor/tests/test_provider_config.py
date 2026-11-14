"""Tests for provider configuration validation (Bug #174)."""

from __future__ import annotations

from pathlib import Path

import pytest
from video_editor_asset_ingestion.config import (
    AssetProviderConfig,
    ProviderConfig,
    validate_provider_url,
)


def test_validate_provider_url_accepts_https_public() -> None:
    assert (
        validate_provider_url("https://api.elevenlabs.io", "base_url")
        == "https://api.elevenlabs.io"
    )
    assert validate_provider_url("https://queue.fal.run/fal-ai/status/123", "status_url") == (
        "https://queue.fal.run/fal-ai/status/123"
    )


def test_validate_provider_url_rejects_http() -> None:
    with pytest.raises(ValueError, match="https scheme"):
        validate_provider_url("http://api.elevenlabs.io", "base_url")


def test_validate_provider_url_rejects_localhost() -> None:
    with pytest.raises(ValueError, match="internal/private"):
        validate_provider_url("https://localhost:8080", "base_url")


def test_validate_provider_url_rejects_private_ip() -> None:
    with pytest.raises(ValueError, match="internal/private"):
        validate_provider_url("https://192.168.1.1/v1/audio", "base_url")
    with pytest.raises(ValueError, match="internal/private"):
        validate_provider_url("https://10.0.0.1/v1/audio", "base_url")
    with pytest.raises(ValueError, match="internal/private"):
        validate_provider_url("https://127.0.0.1/v1/audio", "base_url")


def test_validate_provider_url_allows_empty() -> None:
    assert validate_provider_url("", "base_url") == ""


def test_provider_config_validates_base_url() -> None:
    config = ProviderConfig(base_url="https://api.elevenlabs.io")
    assert config.base_url == "https://api.elevenlabs.io"


def test_provider_config_rejects_bad_base_url() -> None:
    with pytest.raises(ValueError, match="https scheme"):
        ProviderConfig(base_url="ftp://api.elevenlabs.io")


def test_provider_config_rejects_internal_base_url() -> None:
    with pytest.raises(ValueError, match="internal/private"):
        ProviderConfig(base_url="https://127.0.0.1:8000")


def test_provider_config_validates_status_url_option() -> None:
    config = ProviderConfig(
        provider_options={"status_url": "https://queue.fal.run/fal-ai/queue/requests"}
    )
    assert config.provider_options["status_url"] == "https://queue.fal.run/fal-ai/queue/requests"


def test_provider_config_rejects_internal_status_url() -> None:
    with pytest.raises(ValueError, match="internal/private"):
        ProviderConfig(provider_options={"status_url": "https://localhost:8000/status"})


def test_asset_provider_config_load_rejects_ssrf(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
assets:
  providers:
    elevenlabs:
      api_key_env: ELEVENLABS_API_KEY
      base_url: https://localhost:8080
"""
    )
    with pytest.raises(ValueError, match="internal/private"):
        AssetProviderConfig.load(config_path)


def test_image_chain_defaults_to_empty() -> None:
    from video_editor_asset_ingestion.config import AssetProviderConfig

    config = AssetProviderConfig()

    assert config.image_chain == []
    assert config.max_images_per_run == 25


def test_image_chain_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        AssetProviderConfig(image_chain=["agy_image", "not_a_provider"])


def test_image_chain_accepts_registered_providers() -> None:
    from video_editor_asset_ingestion.config import AssetProviderConfig

    config = AssetProviderConfig(
        image_chain=["agy_image", "poyo_ai", "fal_ai"],
        providers={
            "agy_image": ProviderConfig(),
            "poyo_ai": ProviderConfig(),
            "fal_ai": ProviderConfig(),
        },
    )

    assert config.image_chain == ["agy_image", "poyo_ai", "fal_ai"]


def test_image_chain_rejects_an_unconfigured_provider() -> None:
    """A chain member missing from ``assets.providers`` fails at load, not mid-job."""
    from video_editor_asset_ingestion.config import AssetProviderConfig

    with pytest.raises(ValueError) as excinfo:
        AssetProviderConfig(
            image_chain=["agy_image", "poyo_ai"],
            providers={"agy_image": ProviderConfig()},
        )

    message = str(excinfo.value)
    assert "poyo_ai" in message
    assert "not configured" in message
    assert "agy_image" in message  # the chain is named too


def test_image_chain_validation_runs_at_load(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
assets:
  image_chain: [agy_image, poyo_ai]
  providers:
    agy_image: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="poyo_ai"):
        AssetProviderConfig.load(config_path)


def test_a_fully_configured_chain_loads(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
assets:
  image_chain: [agy_image, poyo_ai, openrouter, fal_ai]
  providers:
    agy_image: {}
    poyo_ai: {api_key_env: POYO_API_KEY}
    openrouter: {api_key_env: OPENROUTER_API_KEY}
    fal_ai: {api_key_env: FAL_KEY}
""",
        encoding="utf-8",
    )

    config = AssetProviderConfig.load(config_path)

    assert config.image_chain == ["agy_image", "poyo_ai", "openrouter", "fal_ai"]


def test_the_shipped_example_config_can_serve_its_chain() -> None:
    """The example advertises a chain, so every member must be configured."""
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"

    config = AssetProviderConfig.load(example)

    assert config.image_chain
    assert set(config.image_chain) <= set(config.providers)
