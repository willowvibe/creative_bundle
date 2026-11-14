"""`ve brand-capture`: seed a BrandProfile from a product page (P4.5)."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import typer


def _asset_name(url: str, fallback: str) -> str:
    name = Path(urlparse(url).path).name
    return name if name and name not in {".", ".."} else fallback


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "brand"


def register_brand_capture(app: typer.Typer) -> None:
    """Register the ``brand-capture`` command."""

    @app.command("brand-capture")
    def brand_capture(
        url: str = typer.Argument(..., help="Product/home page to capture."),
        out: Path | None = typer.Option(
            None, "--out", help="BrandProfile JSON path (default: brands/<slug>.json)."
        ),
        assets_dir: Path | None = typer.Option(
            None, "--assets-dir", help="Where to save the logo/OG image."
        ),
        download: bool = typer.Option(
            True, "--download/--no-download", help="Download logo and OG image."
        ),
    ) -> None:
        """Capture colours, fonts, logo and OG image; review before a build uses it."""
        from video_editor_core.brand_capture import BrandCaptureError, capture_brand

        try:
            capture = capture_brand(url)
        except BrandCaptureError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc

        slug = _slug(capture.name)
        profile_path = out or Path("brands") / f"{slug}.json"
        asset_root = assets_dir or Path("brands") / slug
        logo_path = ""
        og_path = ""

        if download:
            from video_editor_asset_ingestion.providers._media import (
                MAX_IMAGE_BYTES,
                new_download_client,
                stream_download,
            )

            async def _download() -> tuple[str, str]:
                client = new_download_client(20.0)
                try:
                    logo = ""
                    if capture.logo_url:
                        target = asset_root / "logo" / _asset_name(capture.logo_url, "logo")
                        await stream_download(
                            client, capture.logo_url, target, max_bytes=MAX_IMAGE_BYTES
                        )
                        logo = str(target)
                    og = ""
                    if capture.og_image_url:
                        target = asset_root / "og" / _asset_name(capture.og_image_url, "og.png")
                        await stream_download(
                            client, capture.og_image_url, target, max_bytes=MAX_IMAGE_BYTES
                        )
                        og = str(target)
                    return logo, og
                finally:
                    await client.aclose()

            try:
                logo_path, og_path = asyncio.run(_download())
            except (OSError, ValueError, RuntimeError) as exc:
                typer.echo(f"Asset download failed (profile still written): {exc}", err=True)

        colors = list(capture.colors)
        fonts = list(capture.fonts)
        profile = {
            "brand": capture.name,
            "colors": {
                "primary": colors[0] if colors else "",
                "secondary": colors[1] if len(colors) > 1 else "",
                "accent": colors[2] if len(colors) > 2 else "",
                "muted": colors[3] if len(colors) > 3 else "",
            },
            "fonts": {
                "heading": fonts[0] if fonts else "",
                "body": fonts[1] if len(fonts) > 1 else (fonts[0] if fonts else ""),
            },
            "logo": {
                "path": logo_path,
                "source": capture.logo_url,
                "og_image": og_path or capture.og_image_url,
            },
            "defaults": {"captured_from": capture.url},
        }
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")

        typer.echo(f"Brand: {capture.name}")
        typer.echo(f"Colours: {', '.join(colors) or '(none)'}")
        typer.echo(f"Fonts: {', '.join(fonts) or '(none)'}")
        if logo_path:
            typer.echo(f"Logo: {logo_path}")
        typer.echo(f"Profile: {profile_path}")
        typer.echo("Review the profile before a build uses it.")
