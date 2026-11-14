"""ve cache-prune: evict old assets from the generation cache."""

from __future__ import annotations

import time
from pathlib import Path

import structlog
import typer

logger = structlog.get_logger(__name__)

DEFAULT_CACHE_DIR = Path(".cache/assets")
DEFAULT_MAX_AGE_DAYS = 30


def prune_cache(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    dry_run: bool = False,
) -> dict[str, int]:
    """Remove cached assets older than max_age_days.

    Returns a dict with 'removed', 'kept', 'freed_bytes'.
    """
    if not cache_dir.exists():
        return {"removed": 0, "kept": 0, "freed_bytes": 0}

    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    kept = 0
    freed_bytes = 0

    for entry in cache_dir.iterdir():
        if entry.is_file():
            mtime = entry.stat().st_mtime
            if mtime < cutoff:
                size = entry.stat().st_size
                if not dry_run:
                    entry.unlink()
                removed += 1
                freed_bytes += size
            else:
                kept += 1
        elif entry.is_dir():
            # Recurse into subdirectories
            for sub in entry.rglob("*"):
                if sub.is_file():
                    mtime = sub.stat().st_mtime
                    if mtime < cutoff:
                        size = sub.stat().st_size
                        if not dry_run:
                            sub.unlink()
                        removed += 1
                        freed_bytes += size
                    else:
                        kept += 1

    return {"removed": removed, "kept": kept, "freed_bytes": freed_bytes}


def register_cache_prune(app: typer.Typer) -> None:
    """Register the cache-prune command."""

    @app.command(name="cache-prune")
    def cache_prune(
        max_age_days: int = typer.Option(
            30, "--max-age-days", help="Remove assets older than N days"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be removed without deleting"
        ),
    ) -> None:
        """Remove cached generated assets older than the specified age."""
        result = prune_cache(
            cache_dir=DEFAULT_CACHE_DIR,
            max_age_days=max_age_days,
            dry_run=dry_run,
        )

        action = "Would remove" if dry_run else "Removed"
        typer.echo(
            f"{action} {result['removed']} cached asset(s) "
            f"({result['freed_bytes'] / 1024 / 1024:.1f} MB), "
            f"{result['kept']} kept."
        )
