"""ve check-config: validate config.yaml provider API keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog
import typer
from video_editor_config import ProjectConfig

logger = structlog.get_logger(__name__)

_CONFIG_OPT = typer.Option(None, "--config", help="Path to config.yaml")


def _check_key(env_var: str) -> tuple[bool, str]:
    """Check if an environment variable is set and non-empty.

    The value is never echoed, not even a prefix: this output is routinely
    pasted into chats and issue reports. Presence is all the caller needs.
    """
    value = os.getenv(env_var, "")
    if not value:
        return False, f"MISSING: {env_var} is not set or empty"
    return True, f"OK: {env_var} is set"


def validate_provider_keys(config_path: Path | None = None) -> dict[str, Any]:
    """Validate that all configured provider API keys are available.

    Returns a dict with provider_name -> {status, message, key_env}.
    """
    config = ProjectConfig.load(config_path)
    results: dict[str, Any] = {}

    providers = getattr(config.assets, "providers", None)
    if providers is None:
        return results

    for name, provider in providers.items():
        api_key_env = getattr(provider, "api_key_env", None)
        if api_key_env:
            ok, msg = _check_key(api_key_env)
            results[name] = {
                "status": "ready" if ok else "missing_key",
                "message": msg,
                "key_env": api_key_env,
            }
        else:
            results[name] = {
                "status": "ready",
                "message": "OK: no API key required",
                "key_env": None,
            }

    return results


def register_check_config(app: typer.Typer) -> None:
    """Register the check-config command on the Typer app."""

    @app.command(name="check-config")
    def check_config(
        config_path: Path | None = _CONFIG_OPT,
    ) -> None:
        """Validate that all configured provider API keys are available."""
        results = validate_provider_keys(config_path)

        ready_count = sum(1 for r in results.values() if r["status"] == "ready")
        missing_count = sum(1 for r in results.values() if r["status"] == "missing_key")

        typer.echo(f"\nProvider API Key Status ({ready_count} ready, {missing_count} missing):\n")

        for name, info in sorted(results.items()):
            icon = "✅" if info["status"] == "ready" else "❌"
            typer.echo(f"  {icon} {name}: {info['message']}")

        if missing_count > 0:
            typer.echo(
                f"\n⚠️  {missing_count} provider(s) have missing API keys. "
                f"Set them in .env or environment variables.\n"
            )
            raise typer.Exit(code=1)
        else:
            typer.echo("\n✅ All provider API keys are configured.\n")
