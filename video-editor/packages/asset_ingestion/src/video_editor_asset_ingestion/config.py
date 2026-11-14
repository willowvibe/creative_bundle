"""Provider configuration for the asset ingestion pipeline."""

from __future__ import annotations

import ipaddress
import os
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator
from video_editor_core.config_paths import find_config_path, load_yaml_with_overlay

logger = structlog.get_logger(__name__)


def _is_internal_host(hostname: str | None) -> bool:
    """Return True if the hostname resolves to a non-public IP or localhost."""
    if hostname is None:
        return True
    lowered = hostname.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        addr = ipaddress.ip_address(lowered)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        # A DNS name is treated as public for this validator; the actual
        # HTTP request goes through httpx which resolves it at call time.
        return False


def validate_provider_url(url: str, field: str) -> str:
    """Validate a provider URL to prevent SSRF via config-level endpoints.

    Rejects non-HTTPS schemes, missing hosts, and internal/private hosts.
    Empty strings are allowed (the provider will use its secure default).
    """
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in {"https"}:
        raise ValueError(f"{field} must use https scheme; got {url!r}")
    if not parsed.hostname:
        raise ValueError(f"{field} is missing a host: {url!r}")
    if _is_internal_host(parsed.hostname):
        raise ValueError(f"{field} must not point to internal/private hosts; got {url!r}")
    return url


#: The free stock libraries, best first: Pexels and Pixabay have API search with
#: 1080p files. Coverr (now 401 without a key) and Mixkit (page format changed,
#: 404) failed every request in chai-stall-pilot-1, so they are opt-in through
#: ``stock_video_chain`` until fixed.
DEFAULT_STOCK_VIDEO_CHAIN: tuple[str, ...] = ("pexels", "pixabay")


class ProviderConfig(BaseModel):
    """Connection and default settings for a single provider."""

    api_key_env: str = ""
    base_url: str = ""
    default_model: str = ""
    default_voice: str = ""  # TTS-specific convenience
    timeout: float = 120.0
    # Provider-specific knobs (e.g. ``status_url`` for FAL, ``video_model``
    # for the FAL video path). Named ``provider_options`` to avoid
    # colliding with Pydantic's reserved ``model_config`` class attribute.
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_urls(self) -> ProviderConfig:
        """Bug #174: reject SSRF-prone base_url / status_url values."""
        if self.base_url:
            self.base_url = validate_provider_url(self.base_url, "base_url")
        status_url = self.provider_options.get("status_url")
        if status_url is not None and status_url != "":
            self.provider_options["status_url"] = validate_provider_url(
                str(status_url), "provider_options.status_url"
            )
        return self

    def api_key(self) -> str | None:
        """Read the configured API key from the environment."""
        if not self.api_key_env:
            return None
        return os.getenv(self.api_key_env)


class ProviderConfigError(RuntimeError):
    """Raised when a requested provider is missing or misconfigured."""


class AssetProviderConfig(BaseModel):
    """Project-level configuration for generative asset providers."""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)
    #: Ordered image providers for drafts. Empty means "use ``defaults``/scored
    #: selection".
    image_chain: list[str] = Field(default_factory=list)
    #: Ordered image providers for a final render. Empty means "same as drafts".
    #: The shipped config starts with a direct image API so a slow or degraded
    #: ``agy`` cannot delay the final export.
    image_chain_final: list[str] = Field(default_factory=list)
    #: Bounds *chain runs*: every image request the configured chain serves
    #: (agy quota plus paid fallbacks) counts, and one request falling through
    #: agy to poyo consumes one unit for two generations. A request that pins
    #: its own provider bypasses the chain and does not count. Inert when
    #: ``image_chain`` is empty.
    max_images_per_run: int = 25
    #: Refuse to *fall back* to a stub (#281). With ``strict`` an ``auto``
    #: request that no configured provider can serve, or a terminal FAL failure,
    #: raises instead of returning a placeholder image, silent audio or a stub
    #: alignment that a render would ship. A stub the config or request names
    #: explicitly (tests, free mode) is still served.
    strict: bool = True
    #: Ordered stock-footage libraries a ``provider: stock`` VIDEO request walks.
    #: Empty means :data:`DEFAULT_STOCK_VIDEO_CHAIN`, minus the libraries this
    #: config has no ``providers`` entry for.
    stock_video_chain: list[str] = Field(default_factory=list)

    def stock_video_chain_for(self) -> list[str]:
        """The stock libraries to try, in order."""
        if self.stock_video_chain:
            return list(self.stock_video_chain)
        return [name for name in DEFAULT_STOCK_VIDEO_CHAIN if name in self.providers]

    def image_chain_for(self, final: bool = False) -> list[str]:
        """Return the chain a run will actually use."""
        if final and self.image_chain_final:
            return list(self.image_chain_final)
        return list(self.image_chain)

    @field_validator("image_chain", "image_chain_final", "stock_video_chain")
    @classmethod
    def _validate_chain(cls, value: list[str]) -> list[str]:
        """Reject typos at load time instead of mid-job."""
        # Imported here, not at module scope: ``providers.registry`` imports
        # provider modules, several of which import this module, so a
        # module-level import would create a cycle. Keeping the import local
        # also means pydantic never touches the registry while defining the
        # model (defaults are not validated, and ``validate_default`` stays
        # off, so ``AssetProviderConfig()`` does not load providers).
        from video_editor_asset_ingestion.providers.registry import PROVIDER_REGISTRY

        unknown = [name for name in value if name not in PROVIDER_REGISTRY]
        if unknown:
            raise ValueError(f"chain names unknown provider(s): {unknown}")
        return value

    @model_validator(mode="after")
    def _validate_chain_is_configured(self) -> AssetProviderConfig:
        """Every chain member must have an ``assets.providers`` entry.

        ``provider_for`` raises ``ProviderConfigError`` for a preferred
        provider that is not configured. A chain naming one would therefore
        die on the first fallback instead of degrading — exactly when the
        chain is supposed to help. Fail at load time instead.
        """
        for field_name, chain in (
            ("image_chain", self.image_chain),
            ("image_chain_final", self.image_chain_final),
            ("stock_video_chain", self.stock_video_chain),
        ):
            if not chain:
                continue
            missing = [name for name in chain if name not in self.providers]
            if missing:
                raise ValueError(
                    f"{field_name} names provider(s) that are not configured in "
                    f"assets.providers: {missing} (chain: {chain})"
                )
        return self

    @classmethod
    def load(cls, path: Path | str | None = None) -> AssetProviderConfig:
        """Load asset provider config from a YAML file.

        If no path is given, look for ``config.yaml`` in the current working
        directory and then for ``VIDEO_EDITOR_CONFIG`` in the environment.
        """
        target = cls._resolve_path(path)
        if target is None:
            return cls()
        raw = load_yaml_with_overlay(target)
        assets_section = raw.get("assets", {})
        cls._warn_unknown_keys(assets_section, target)
        return cls.model_validate(assets_section)

    @classmethod
    def _warn_unknown_keys(cls, data: dict[str, Any], path: Path) -> None:
        """Bug #70: warn about unknown keys in the ``assets:`` section.

        ``model_validate`` silently drops extra keys, so misspelled
        provider config (e.g. ``fal_ai`` -> ``fal``) or stray options
        would be ignored without any signal.
        """
        if not isinstance(data, dict):
            return
        allowed = set(cls.model_fields.keys())
        unknown = [k for k in data if k not in allowed]
        if unknown:
            warnings.warn(
                f"assets: unknown keys {unknown} in {path} will be ignored",
                stacklevel=3,
            )

    @classmethod
    def _resolve_path(cls, path: Path | str | None) -> Path | None:
        return find_config_path(path)

    def model_for_provider(self, provider_name: str, task_type: str) -> str:
        """Return a default model/voice string for a provider if configured."""
        provider = self.providers.get(provider_name)
        if not provider:
            return ""
        if task_type == "voice":
            return provider.default_voice
        return provider.default_model

    def provider_for(
        self,
        asset_type: str,
        preferred: str | None = None,
    ) -> tuple[str, ProviderConfig]:
        """Return the provider name and config for an asset type.

        Falls back to the configured default provider for the asset type, then
        to the first configured provider, then to an empty config.

        Bug #66: when the caller explicitly requests a provider that is not
        configured, raise a typed error instead of silently returning the
        first/default provider. This prevents passing the wrong ``api_key_env``
        to a provider (e.g. ``FalAIProvider`` configured with ElevenLabs keys).
        """
        if preferred and preferred in self.providers:
            return preferred, self.providers[preferred]

        # Explicit request for a missing provider is a configuration error.
        if preferred:
            raise ProviderConfigError(
                f"Provider {preferred!r} is not configured for asset type {asset_type!r}"
            )

        default_name = self.defaults.get(asset_type)
        if default_name and default_name in self.providers:
            return default_name, self.providers[default_name]

        if self.providers:
            first = next(iter(self.providers))
            logger.warning(
                "no_default_provider",
                asset_type=asset_type,
                falling_back=first,
            )
            return first, self.providers[first]

        logger.warning("no_provider_configured", asset_type=asset_type)
        return "", ProviderConfig()

    def select_provider(
        self,
        asset_type: str,
        preferred: str | None = None,
        criteria: dict[str, Any] | None = None,
        *,
        eligible: set[str] | None = None,
    ) -> tuple[str, ProviderConfig]:
        """Select a provider using scored selection.

        The lazy import avoids a circular dependency between ``config`` and
        ``scoring``. Pass ``criteria`` as a dict of
        ``{"cost_weight": ..., "quality_weight": ..., "latency_weight": ...,
        "reliability_weight": ..., "min_quality": ..., "require_api_key": ...}``.
        ``eligible`` optionally restricts the candidates to the given names.
        """
        from video_editor_asset_ingestion.providers.scoring import (
            ProviderCriteria,
            ScoredProviderSelector,
        )

        crit = ProviderCriteria(**(criteria or {}))
        return ScoredProviderSelector(self).select(asset_type, preferred, crit, eligible=eligible)
