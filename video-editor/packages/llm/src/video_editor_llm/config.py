"""Project-level LLM provider configuration loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field, field_validator
from video_editor_core.config_paths import find_config_path, load_yaml_with_overlay

logger = structlog.get_logger(__name__)


class ProviderConfig(BaseModel):
    """Connection and default model for a single provider."""

    name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    default_model: str = ""
    # Optional per-provider cost used by the LLM router's budget guardrail.
    # When omitted, the router falls back to its built-in per-model estimates.
    cost_per_million_tokens: float | None = None


class TaskRouting(BaseModel):
    """Routing rule for a task type."""

    task_type: str = ""
    provider: str = ""
    model: str = ""


class LLMConfig(BaseModel):
    """Project-level configuration for model-agnostic provider selection."""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tasks: dict[str, TaskRouting] = Field(default_factory=dict)
    default_provider: str = ""

    # Bug #70: model_config is Pydantic's reserved class attribute. Use a
    # normal field named ``strict_validation`` instead if we ever need a
    # top-level knob.

    # Cost / concurrency guardrails
    max_tokens_per_job: int | None = None
    max_cost_usd_per_job: float | None = None
    max_llm_attempts_per_job: int = 50
    # How many times to try the primary model before giving up and switching
    # to the fallback. ``primary_attempts=1, fallback_attempts=2`` means
    # "1 primary + 2 fallback = up to 3 total attempts." Set
    # ``fallback_attempts=0`` to disable fallback entirely.
    primary_attempts: int = 1
    fallback_attempts: int = 2
    # Open the circuit breaker AFTER this many consecutive failures (not AT
    # this many — see the comment in ``ModelRouter._check_circuit``).
    circuit_breaker_failure_threshold: int = 5

    # Exponential-backoff settings for retryable LLM HTTP errors.
    retry_backoff_base_seconds: float = 1.0
    retry_backoff_max_seconds: float = 60.0
    retry_jitter: bool = True

    @field_validator("providers", mode="before")
    @classmethod
    def _inject_provider_names(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Use the YAML mapping key as the provider name when omitted."""
        if not isinstance(value, dict):
            return value
        return {
            key: {**provider, "name": provider.get("name", key)} for key, provider in value.items()
        }

    @field_validator("tasks", mode="before")
    @classmethod
    def _inject_task_types(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Use the YAML mapping key as the task type when omitted."""
        if not isinstance(value, dict):
            return value
        return {
            key: {**task, "task_type": task.get("task_type", key)} for key, task in value.items()
        }

    @classmethod
    def load(cls, path: Path | str | None = None) -> LLMConfig:
        """Load configuration from a YAML file.

        If no path is given, look for ``config.yaml`` in the current working
        directory and then for ``VIDEO_EDITOR_CONFIG`` in the environment.

        Supports two on-disk layouts for backward compatibility:

        1. **Sectioned** (the convention used by ``config.yaml`` and
           ``config.example.yaml``'s ``llm:`` block):
           ``default_provider``/``providers``/``tasks`` live under an
           ``llm:`` mapping — pulled out before validating.
        2. **Top-level** (the historical layout): the LLM fields sit at
           the document root. Used by the test suite and the v1
           ``config.example.yaml``. Validated directly.

        The full-document pass would silently drop the ``llm:`` mapping
        because ``LLMConfig`` has no ``llm`` field — so prefer the
        sectioned form for new configs.
        """
        target = cls._resolve_path(path)
        if target is None:
            return cls()
        raw = load_yaml_with_overlay(target)

        if isinstance(raw, dict) and "llm" in raw and isinstance(raw["llm"], dict):
            section = raw["llm"]
            cls._warn_unknown_keys(section, target, section="llm")
            return cls.model_validate(section)

        cls._warn_unknown_keys(raw, target, section="")
        return cls.model_validate(raw)

    @classmethod
    def _warn_unknown_keys(
        cls,
        data: dict[str, Any],
        path: Path,
        section: str,
    ) -> None:
        """Bug #70: warn when unknown top-level keys would be ignored.

        ``model_validate`` silently drops fields that are not declared on
        the model. For a project config file that is supposed to drive
        behavior, ignoring ``transcription:``, ``audio:``, etc. is
        confusing — those sections belong to other loaders, but misspelled
        LLM keys like ``default_model`` or ``modle`` are real bugs.
        """
        if not isinstance(data, dict):
            return
        allowed = set(cls.model_fields.keys())
        unknown = [k for k in data if k not in allowed]
        if unknown:
            prefix = f"{section}: " if section else ""
            logger.warning(
                f"{prefix}unknown keys {unknown} in {path} will be ignored",
            )

    @classmethod
    def _resolve_path(cls, path: Path | str | None) -> Path | None:
        return find_config_path(path)

    def provider_for_task(self, task_type: str) -> tuple[str, str]:
        """Return the (provider_name, model) to use for a task.

        Falls back to the default provider and its default model when a task is
        not explicitly routed.
        """
        routing = self.tasks.get(task_type)
        if routing:
            provider_name = routing.provider or self.default_provider
            model = routing.model
        else:
            provider_name = self.default_provider
            model = ""

        provider = self.providers.get(provider_name)
        if provider and not model:
            model = provider.default_model

        return provider_name, model

    def client_kwargs(self, provider_name: str) -> dict[str, Any]:
        """Return kwargs for constructing a client for the named provider."""
        provider = self.providers.get(provider_name)
        if not provider:
            return {}
        # Bug #65 regression: if the configured provider has an api_key_env
        # but that env var is unset, warn instead of silently constructing
        # a client with ``api_key=None``. The client (OllamaClient) still
        # falls back to its own env defaults for backward compatibility, but
        # callers get a visible signal that their config was ignored.
        api_key = None
        if provider.api_key_env:
            api_key = os.getenv(provider.api_key_env)
            if api_key is None:
                logger.warning(
                    f"{provider.api_key_env} is unset; provider {provider_name!r} "
                    "will send unauthenticated requests",
                )
        return {
            "base_url": provider.base_url or None,
            "api_key": api_key,
        }
