"""The ``social-engine-mcp`` stdio MCP server.

Tools mirror the CLI, but paths are confined to ``SOCIAL_ENGINE_ROOTS`` (colon
separated, default the video-editor videos folder). The browser lane is a
two-call flow: the first call stages the Studio dialog and returns
``ready_to_confirm``; the second call needs ``confirm: true`` and the returned id.

Live Studio can reset the staged dialog when the driver disconnects; the returned
id is then a private draft. See ``docs/live-verification.md``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core.accounts import load_account
from .core.browser import BrowserResult
from .core.errors import SocialError
from .core.manifest import LoadedManifest, YouTubeSection, load_manifest, validate
from .core.publish_log import PublishLog
from .platforms.youtube import browser_ops
from .platforms.youtube import ops as youtube_ops
from .platforms.youtube.api import GoogleYouTubeApi
from .platforms.youtube.auth import load_credentials
from .platforms.youtube.costs import estimate

YOUTUBE = "youtube"
DEFAULT_ROOTS = "/mnt/data2/git_repos/video-editor/videos"

mcp = FastMCP("social-engine")
_pending: dict[str, str] = {}


def _allowed_roots() -> list[Path]:
    raw = os.environ.get("SOCIAL_ENGINE_ROOTS") or DEFAULT_ROOTS
    return [Path(part).expanduser().resolve() for part in raw.split(":") if part.strip()]


def _safe_manifest(manifest: str) -> Path:
    """Resolve a manifest path (following symlinks) and require an allowed root."""
    candidate = Path(manifest).expanduser()
    if candidate.is_dir():
        candidate = candidate / "publish.yaml"
    resolved = candidate.resolve()
    for root in _allowed_roots():
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    raise SocialError(
        f"manifest path is outside SOCIAL_ENGINE_ROOTS: {manifest}",
        code="path_not_allowed",
        next_step="Move the manifest under an allowed root or update SOCIAL_ENGINE_ROOTS.",
    )


def _section(loaded: LoadedManifest) -> YouTubeSection:
    section = loaded.manifest.youtube
    if section is None:
        raise SocialError(
            f"{loaded.path} has no youtube: section",
            code="no_youtube_section",
            next_step="Add a youtube: section or run `social init <folder> --account <name>`.",
        )
    return section


def _validated(path: Path) -> LoadedManifest:
    loaded = load_manifest(path)
    issues = validate(loaded)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        details = "; ".join(f"{issue.field}: {issue.message}" for issue in errors)
        raise SocialError(
            f"{loaded.path} has {len(errors)} validation error(s): {details}",
            code="validation_failed",
            next_step="Fix the manifest, then call social_validate again.",
        )
    return loaded


def _api_for(loaded: LoadedManifest) -> GoogleYouTubeApi:
    section = _section(loaded)
    credentials = load_credentials(section.account)
    return GoogleYouTubeApi(credentials)


def _default_via(loaded: LoadedManifest) -> str:
    section = _section(loaded)
    account = load_account(YOUTUBE, section.account)
    return "api" if account.api_audited else "browser"


def _error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, SocialError):
        return {
            "status": "error",
            "code": exc.code,
            "message": exc.message,
            "next_step": exc.next_step,
            "screenshot": exc.screenshot,
        }
    return {"status": "error", "code": "unexpected", "message": str(exc)}


def _capture(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return action()
    except Exception as exc:  # noqa: BLE001 - tool boundaries return errors, not tracebacks
        return _error(exc)


def _op_dict(result: Any) -> dict[str, Any]:
    return asdict(result)


def _browser_dict(result: BrowserResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "summary": result.summary,
        "id": result.id,
        "screenshot": result.screenshot,
        "message": result.message,
    }


def _refused(message: str) -> dict[str, Any]:
    return {"status": "refused", "message": message}


@mcp.tool()
def social_validate(manifest: str) -> dict[str, Any]:
    """Validate a publish.yaml offline and return issues plus a quota estimate."""

    def action() -> dict[str, Any]:
        path = _safe_manifest(manifest)
        loaded = load_manifest(path)
        issues = validate(loaded)
        errors = [issue for issue in issues if issue.severity == "error"]
        return {
            "manifest": str(path),
            "ok": not errors,
            "issues": [asdict(issue) for issue in issues],
            "quota_estimate": estimate(loaded),
        }

    return _capture(action)


@mcp.tool()
def social_publish(
    manifest: str,
    platform: str = YOUTUBE,
    via: str | None = None,
    confirm: bool = False,
    video_id: str | None = None,
) -> dict[str, Any]:
    """Publish a video. The API lane needs confirm=True; the browser lane stages first."""

    def action() -> dict[str, Any]:
        path = _safe_manifest(manifest)
        if platform != YOUTUBE:
            raise SocialError(
                f"{platform} is not implemented yet",
                code="platform_not_supported",
                next_step="YouTube is the only platform in this version.",
            )
        loaded = load_manifest(path)
        chosen = via or _default_via(loaded)
        if chosen not in {"browser", "api"}:
            raise SocialError(
                f"unknown lane {chosen!r}",
                code="bad_lane",
                next_step="Use via browser or via api.",
            )
        if chosen == "api":
            if confirm is not True:
                return _refused("confirm must be true to publish through the API lane")
            valid = _validated(path)
            return _op_dict(youtube_ops.upload(path, _api_for(valid), confirm=True))

        if confirm is True:
            target = video_id or _pending.get(str(path))
            if not target:
                return _refused(
                    "no staged browser upload; call social_publish without confirm "
                    "first or pass the returned video id"
                )
            result = browser_ops.browser_confirm(str(path), target, "publish")
            _pending.pop(str(path), None)
            return _browser_dict(result)

        result = browser_ops.browser_upload(str(path))
        if result.status == "ready_to_confirm" and result.id:
            _pending[str(path)] = result.id
        return _browser_dict(result)

    return _capture(action)


@mcp.tool()
def social_update(manifest: str, confirm: bool = False) -> dict[str, Any]:
    """Update a live YouTube video; requires confirm=True."""

    def action() -> dict[str, Any]:
        path = _safe_manifest(manifest)
        if confirm is not True:
            return _refused("confirm must be true to update a live video")
        loaded = _validated(path)
        return _op_dict(youtube_ops.update(path, _api_for(loaded), confirm=True))

    return _capture(action)


@mcp.tool()
def social_list(platform: str, account: str, limit: int = 20) -> dict[str, Any]:
    """List a channel's newest videos."""

    def action() -> dict[str, Any]:
        if platform != YOUTUBE:
            raise SocialError(
                f"{platform} is not implemented yet",
                code="platform_not_supported",
                next_step="YouTube is the only platform in this version.",
            )
        credentials = load_credentials(account)
        api = GoogleYouTubeApi(credentials)
        rows = youtube_ops.list_videos(api, limit)
        return {"videos": [asdict(row) for row in rows]}

    return _capture(action)


@mcp.tool()
def social_stats(manifest: str, days: int = 28) -> dict[str, Any]:
    """Return window and lifetime stats for a published video."""

    def action() -> dict[str, Any]:
        path = _safe_manifest(manifest)
        loaded = _validated(path)
        section = _section(loaded)
        entry = PublishLog.load(loaded.path).entry("youtube")
        if entry is None:
            raise SocialError(
                f"{loaded.path} has no YouTube entry in its publish log",
                code="not_published",
                next_step="Publish the video first.",
            )
        credentials = load_credentials(section.account)
        api = GoogleYouTubeApi(credentials)
        return asdict(youtube_ops.stats(api, entry.id, days))

    return _capture(action)


def main() -> None:
    """Run the stdio server (the console script entry point)."""
    mcp.run()


if __name__ == "__main__":
    main()
