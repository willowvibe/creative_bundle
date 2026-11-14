"""The ``social`` CLI: one command surface over the API and browser lanes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer

from .core.accounts import load_account
from .core.browser import BrowserResult, browser_call
from .core.draft import draft_manifest
from .core.errors import BrowserNeedsUser, SocialError
from .core.manifest import LoadedManifest, YouTubeSection, load_manifest, validate
from .core.publish_log import PublishLog
from .platforms.youtube import browser_ops
from .platforms.youtube import ops as youtube_ops
from .platforms.youtube.api import GoogleYouTubeApi
from .platforms.youtube.auth import authorize, load_credentials
from .platforms.youtube.costs import estimate

YOUTUBE = "youtube"

app = typer.Typer(help="Publish and manage social videos.", no_args_is_help=True)
yt_app = typer.Typer(help="YouTube extras: captions, localizations, playlists, dubs.")
app.add_typer(yt_app, name="yt")


def _guard(action: Callable[[], None]) -> None:
    try:
        action()
    except BrowserNeedsUser as exc:
        typer.echo(f"{exc.code}: {exc.message}")
        if exc.screenshot:
            typer.echo(f"screenshot: {exc.screenshot}")
        typer.echo(f"next: {exc.next_step}")
        raise typer.Exit(3) from exc
    except SocialError as exc:
        typer.echo(f"{exc.code}: {exc.message}")
        if exc.screenshot:
            typer.echo(f"screenshot: {exc.screenshot}")
        typer.echo(f"next: {exc.next_step}")
        raise typer.Exit(2) from exc


def _require_youtube(platform: str) -> None:
    if platform != YOUTUBE:
        raise SocialError(
            f"{platform} is not implemented yet",
            code="platform_not_supported",
            next_step="YouTube is the only platform in this version.",
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


def _validated(manifest_path: Path) -> LoadedManifest:
    loaded = load_manifest(manifest_path)
    issues = validate(loaded)
    for issue in issues:
        typer.echo(f"{issue.severity}: {issue.field}: {issue.message}")
    if any(issue.severity == "error" for issue in issues):
        raise typer.Exit(1)
    return loaded


def _api_for(loaded: LoadedManifest) -> GoogleYouTubeApi:
    section = _section(loaded)
    credentials = load_credentials(section.account)
    return GoogleYouTubeApi(credentials)


def _default_via(platform: str, manifest_path: Path) -> str:
    if platform != YOUTUBE:
        return "browser"
    loaded = load_manifest(manifest_path)
    section = _section(loaded)
    account = load_account(YOUTUBE, section.account)
    return "api" if account.api_audited else "browser"


def _print_op_result(result: youtube_ops.OpResult) -> None:
    typer.echo(f"action: {result.action}")
    if result.video_id:
        typer.echo(f"video: {result.video_id}")
    if result.changed:
        typer.echo(f"changed: {', '.join(result.changed)}")
    for warning in result.warnings:
        typer.echo(f"warning: {warning}")


def _print_browser_result(result: BrowserResult) -> None:
    typer.echo(f"status: {result.status}")
    if result.id:
        typer.echo(f"video: {result.id}")
    if result.message:
        typer.echo(result.message)
    for key, value in result.summary.items():
        typer.echo(f"{key}: {value}")
    if result.screenshot:
        typer.echo(f"screenshot: {result.screenshot}")


def _print_publish_preview(loaded: LoadedManifest, via: str) -> None:
    section = _section(loaded)
    title = section.title if section.title is not None else loaded.manifest.title
    typer.echo(f"title: {title}")
    typer.echo(f"privacy: {section.privacy}")
    if section.publish_at:
        typer.echo(f"publish_at: {section.publish_at}")
    typer.echo(f"account: {section.account}")
    typer.echo(f"via: {via}")
    typer.echo(f"quota estimate: {estimate(loaded)} units")
    typer.echo("not published; rerun with --yes to continue")


def _print_update_preview(loaded: LoadedManifest) -> None:
    section = _section(loaded)
    typer.echo(f"would update {loaded.path}")
    typer.echo(f"account: {section.account}")
    typer.echo(f"privacy: {section.privacy}")
    typer.echo("rerun with --yes to apply")


def _confirm_or_prompt(
    manifest_path: Path,
    result: BrowserResult,
    *,
    yes: bool,
    expect: str,
) -> None:
    if result.id is None:
        raise SocialError(
            "the browser lane returned no video id",
            code="browser_no_video",
            next_step="Check the open Chrome window, then retry.",
        )
    if yes or typer.confirm("Publish now?", default=False, abort=False):
        done = browser_ops.browser_confirm(manifest_path, result.id, expect)
        _print_browser_result(done)
    else:
        typer.echo("left at the confirmation step; the Chrome window is still open")


def _login(platform: str, account: str) -> None:
    result = browser_call(platform, "login", {"account": account})
    _print_browser_result(result)


def _auth(platform: str, account: str) -> None:
    _require_youtube(platform)
    authorize(account)
    typer.echo(f"authorized {account}")


def _init(folder: Path, account: str, kind: str | None) -> None:
    path = draft_manifest(folder, account=account, kind=kind)
    typer.echo(f"drafted {path}")
    typer.echo("edit the TODO(owner) fields, then run `social validate <manifest>`")


def _validate(manifest: Path) -> None:
    loaded = _validated(manifest)
    typer.echo("ok")
    if loaded.manifest.youtube is not None:
        typer.echo(f"quota estimate: {estimate(loaded)} units")


def _publish(
    manifest: Path,
    platform: str,
    via: str | None,
    yes: bool,
) -> None:
    _require_youtube(platform)
    chosen = via or _default_via(platform, manifest)
    if chosen not in {"browser", "api"}:
        raise SocialError(
            f"unknown lane {chosen!r}",
            code="bad_lane",
            next_step="Use --via browser or --via api.",
        )
    if chosen == "api":
        loaded = _validated(manifest)
        if not yes:
            _print_publish_preview(loaded, chosen)
            return
        _print_op_result(youtube_ops.upload(manifest, _api_for(loaded), confirm=True))
        return

    result = browser_ops.browser_upload(manifest)
    _print_browser_result(result)
    if result.status == "ready_to_confirm":
        _confirm_or_prompt(manifest, result, yes=yes, expect="publish")


def _update(manifest: Path, yes: bool) -> None:
    loaded = _validated(manifest)
    if not yes:
        _print_update_preview(loaded)
        return
    _print_op_result(youtube_ops.update(manifest, _api_for(loaded), confirm=True))


def _list(platform: str, account: str, limit: int) -> None:
    _require_youtube(platform)
    credentials = load_credentials(account)
    api = GoogleYouTubeApi(credentials)
    rows = youtube_ops.list_videos(api, limit)
    if not rows:
        typer.echo("no videos")
        return
    for row in rows:
        typer.echo(f"{row.id}\t{row.privacy}\t{row.published_at}\t{row.views}\t{row.title}")


def _stats(manifest: Path, days: int) -> None:
    loaded = _validated(manifest)
    section = _section(loaded)
    entry = PublishLog.load(loaded.path).entry("youtube")
    if entry is None:
        raise SocialError(
            f"{loaded.path} has no YouTube entry in its publish log",
            code="not_published",
            next_step="Publish the video first with `social publish <manifest> --yes`.",
        )
    credentials = load_credentials(section.account)
    api = GoogleYouTubeApi(credentials)
    result = youtube_ops.stats(api, entry.id, days)
    typer.echo(f"video: {result.video_id} ({result.days} days)")
    typer.echo(f"views: {result.views}")
    typer.echo(f"estimated minutes watched: {result.estimated_minutes_watched}")
    typer.echo(f"average view duration: {result.average_view_duration}")
    typer.echo(f"average view percentage: {result.average_view_percentage}")
    typer.echo(f"likes: {result.likes}")
    typer.echo(f"subscribers gained: {result.subscribers_gained}")
    typer.echo(f"lifetime views: {result.lifetime_views}")
    typer.echo(f"lifetime likes: {result.lifetime_likes}")
    typer.echo(f"lifetime comments: {result.lifetime_comments}")
    typer.echo("impressions CTR: read it in YouTube Studio")


def _sync(
    manifest: Path,
    action: Callable[[Path, GoogleYouTubeApi], youtube_ops.OpResult],
) -> None:
    loaded = _validated(manifest)
    _print_op_result(action(manifest, _api_for(loaded)))


def _dubs(manifest: Path, yes: bool) -> None:
    result = browser_ops.browser_dubs(manifest)
    _print_browser_result(result)
    if result.status == "ready_to_confirm":
        _confirm_or_prompt(manifest, result, yes=yes, expect="dubs")


@app.command("login")
def login_cmd(
    platform: str = typer.Argument(..., help="Platform name, e.g. youtube."),
    account: str = typer.Argument(..., help="Named account."),
) -> None:
    """Open the platform in the owner's dedicated Chrome profile for sign-in."""
    _guard(lambda: _login(platform, account))


@app.command("auth")
def auth_cmd(
    platform: str = typer.Argument(..., help="Platform name, e.g. youtube."),
    account: str = typer.Argument(..., help="Named account."),
) -> None:
    """Run the platform's OAuth flow for one account."""
    _guard(lambda: _auth(platform, account))


@app.command("init")
def init_cmd(
    folder: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    account: str = typer.Option(..., "--account", help="Account name for the platform."),
    kind: str | None = typer.Option(None, "--kind", help="long or short; detected when omitted."),
) -> None:
    """Draft publish.yaml from a finished video folder."""
    _guard(lambda: _init(folder, account, kind))


@app.command("validate")
def validate_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Validate a manifest offline and print a quota estimate."""
    _guard(lambda: _validate(manifest))


@app.command("publish")
def publish_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
    platform: str = typer.Option(YOUTUBE, "--platform", help="Platform name."),
    via: str | None = typer.Option(None, "--via", help="browser or api."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the visible action."),
) -> None:
    """Publish the video to a platform, stopping for confirmation by default."""
    _guard(lambda: _publish(manifest, platform, via, yes))


@app.command("update")
def update_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the visible change."),
) -> None:
    """Update a live YouTube video from the manifest."""
    _guard(lambda: _update(manifest, yes))


@app.command("list")
def list_cmd(
    platform: str = typer.Argument(..., help="Platform name, e.g. youtube."),
    account: str = typer.Argument(..., help="Named account."),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum videos to list."),
) -> None:
    """List a channel's newest videos."""
    _guard(lambda: _list(platform, account, limit))


@app.command("stats")
def stats_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
    days: int = typer.Option(28, "--days", min=1, help="Analytics window in days."),
) -> None:
    """Print window and lifetime stats for a published video."""
    _guard(lambda: _stats(manifest, days))


@yt_app.command("captions")
def captions_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Insert or update caption tracks from the manifest."""
    _guard(lambda: _sync(manifest, youtube_ops.sync_captions))


@yt_app.command("localize")
def localize_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Set the default language and localized titles/descriptions."""
    _guard(lambda: _sync(manifest, youtube_ops.sync_localizations))


@yt_app.command("playlist")
def playlist_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Create missing playlists and add the video to each."""
    _guard(lambda: _sync(manifest, youtube_ops.sync_playlists))


@yt_app.command("dubs")
def dubs_cmd(
    manifest: Path = typer.Argument(..., exists=True, readable=True),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the final publish."),
) -> None:
    """Add dubbed audio tracks in Studio, stopping before the final publish."""
    _guard(lambda: _dubs(manifest, yes))
