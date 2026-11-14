"""ve poyo-catalog: compare configured PoYo models with the live catalog.

This is a development/ops check, not a render-path dependency. It uses the
PoYo CLI's JSON catalog (``poyo models ... --format json``) so a renamed,
retired or repriced model is visible before a paid run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import typer
import yaml
from video_editor_config import ProjectConfig
from video_editor_ffmpeg.run import PROBE_TIMEOUT_SECONDS, CommandError, run_subprocess

from video_editor_cli.constants import REPO_ROOT

_CLI_OPT = typer.Option(
    None,
    "--cli",
    help="PoYo CLI executable (default: poyo if installed, else npx -y @poyoapi/cli)",
)
_CONFIG_OPT = typer.Option(None, "--config", help="Path to config.yaml")
_LIMIT_OPT = typer.Option(100, "--limit", help="Maximum catalog entries (API max: 100)")
_CHECK_OPT = typer.Option(False, "--check", help="Exit 1 on a missing or retired model")
_JSON_OPT = typer.Option(False, "--json", help="Print the result as JSON")


def _cli_command(cli: str | None) -> list[str]:
    if cli:
        return [cli]
    if shutil.which("poyo"):
        return ["poyo"]
    return ["npx", "-y", "@poyoapi/cli"]


CATEGORIES = ("image", "img2img", "video", "audio", "3d")
#: Model ids the API accepts as aliases but the catalog does not list.
KNOWN_ALIASES = {"generate-music": "PoYo music alias"}


def fetch_catalog(*, cli: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return the CLI's generate-model catalog as JSON.

    The API caps ``limit`` at 100 and returns one category at a time, so query
    each category and merge by ``model_id``.
    """
    limit = min(max(limit, 1), 100)
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for category in CATEGORIES:
        command = [
            *_cli_command(cli),
            "models",
            category,
            "--limit",
            str(limit),
            "--format",
            "json",
        ]
        try:
            result = run_subprocess(command, timeout=PROBE_TIMEOUT_SECONDS)
            data = json.loads(result.stdout)
        except (CommandError, FileNotFoundError, ValueError) as exc:
            errors.append(f"{category}: {exc}")
            continue
        if not isinstance(data, list):
            errors.append(f"{category}: non-list catalog")
            continue
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("model_id"), str):
                merged[item["model_id"]] = item
    if not merged:
        detail = "; ".join(errors) if errors else "no models returned"
        raise RuntimeError(f"could not run the PoYo CLI: {detail}")
    return list(merged.values())


def _configured_models(config_path: Path | None) -> dict[str, str]:
    """Model id -> where it is configured."""
    config = ProjectConfig.load(config_path)
    configured: dict[str, str] = {}
    poyo = config.assets.providers.get("poyo_ai")
    if poyo is not None:
        for key in ("default_model",):
            value = getattr(poyo, key, None)
            if isinstance(value, str) and value:
                configured.setdefault(value, f"config assets.poyo_ai.{key}")
        options = poyo.provider_options or {}
        for key in ("video_model",):
            value = options.get(key)
            if isinstance(value, str) and value:
                configured.setdefault(value, f"config assets.poyo_ai.{key}")
    music = config.assets.providers.get("poyo_music")
    if music is not None:
        for key in ("default_model",):
            value = getattr(music, key, None)
            if isinstance(value, str) and value:
                configured.setdefault(value, f"config assets.poyo_music.{key}")
        options = music.provider_options or {}
        for key in ("fallback_music_model",):
            value = options.get(key)
            if isinstance(value, str) and value:
                configured.setdefault(value, f"config assets.poyo_music.{key}")

    models_path = REPO_ROOT / "models.yaml"
    if models_path.exists():
        try:
            registry = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            registry = {}
        providers = registry.get("providers", {}) if isinstance(registry, dict) else {}
        for provider_name in ("poyo_ai", "poyo_music"):
            provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
            models = provider.get("models", {}) if isinstance(provider, dict) else {}
            if isinstance(models, dict):
                for model_id in models:
                    configured.setdefault(str(model_id), f"models.yaml {provider_name}")
    return configured


def compare_catalog(catalog: list[dict[str, Any]], configured: dict[str, str]) -> dict[str, Any]:
    """Match configured ids against live catalog entries."""
    by_id = {
        str(item.get("model_id")): item for item in catalog if isinstance(item.get("model_id"), str)
    }
    rows: list[dict[str, Any]] = []
    for model_id, source in sorted(configured.items()):
        item = by_id.get(model_id)
        if item is None:
            alias = KNOWN_ALIASES.get(model_id)
            if alias:
                rows.append(
                    {
                        "model": model_id,
                        "status": "ok",
                        "source": source,
                        "category": "alias",
                        "credits": None,
                        "note": alias,
                    }
                )
                continue
            # A config default must exist. Historical models.yaml rows are a
            # warning: they may be kept for old jobs or a provider alias.
            status = "missing" if source.startswith("config") else "warning"
            rows.append(
                {
                    "model": model_id,
                    "status": status,
                    "source": source,
                    "category": "",
                    "credits": None,
                }
            )
            continue
        status = str(item.get("status") or "unknown")
        billing = item.get("billing")
        credits = billing.get("credits") if isinstance(billing, dict) else None
        rows.append(
            {
                "model": model_id,
                "status": "retired" if status not in {"stable", "unknown"} else "ok",
                "source": source,
                "category": str(item.get("category") or ""),
                "credits": credits,
                "catalog_status": status,
            }
        )
    return {
        "checked": len(rows),
        "ok": not any(row["status"] in {"missing", "retired"} for row in rows),
        "warnings": sum(1 for row in rows if row["status"] == "warning"),
        "rows": rows,
        "catalog_size": len(catalog),
    }


def register_poyo_catalog(app: typer.Typer) -> None:
    """Register ``poyo-catalog`` on the Typer app."""

    @app.command(name="poyo-catalog")
    def poyo_catalog(
        cli: str | None = _CLI_OPT,
        config_path: Path | None = _CONFIG_OPT,
        limit: int = _LIMIT_OPT,
        check: bool = _CHECK_OPT,
        json_output: bool = _JSON_OPT,
    ) -> None:
        """Compare configured PoYo models with the live model catalog."""
        try:
            catalog = fetch_catalog(cli=cli, limit=limit)
        except RuntimeError as exc:
            typer.echo(f"error: {exc}", err=True)
            if check:
                raise typer.Exit(1) from exc
            return
        result = compare_catalog(catalog, _configured_models(config_path))
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            typer.echo(
                f"PoYo catalog: {result['catalog_size']} generate model(s); "
                f"{result['checked']} configured"
            )
            for row in result["rows"]:
                credits = "" if row["credits"] is None else f", {row['credits']} credits"
                typer.echo(
                    f"  {row['status'].upper():<8} {row['model']:<32} "
                    f"{row['category'] or '-':<8}{credits}  ({row['source']})"
                )
        if check and not result["ok"]:
            raise typer.Exit(1)
