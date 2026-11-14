"""YouTube publish operations: upload a new video and update a live one.

Both operations validate offline first, never make anything visible without
``confirm=True``, and record what happened in ``publish-log.json`` beside the
manifest. They are idempotent: a manifest that already has a log entry cannot be
uploaded again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...core.errors import PrivateLocked, SocialError, ThumbnailNotAllowed
from ...core.manifest import LoadedManifest, load_manifest, validate
from ...core.publish_log import PlatformEntry, PublishLog
from .api import YouTubeApi
from .rules import render_description

_SNIPPET_FIELDS = {
    "title": "title",
    "description": "description",
    "tags": "tags",
    "categoryId": "category",
    "defaultLanguage": "language",
    "defaultAudioLanguage": "audio_language",
}
_STATUS_FIELDS = {
    "privacyStatus": "privacy",
    "publishAt": "publish_at",
    "selfDeclaredMadeForKids": "made_for_kids",
    "containsSyntheticMedia": "synthetic_media",
}
_VISIBILITY_FIELDS = frozenset({"privacy", "publish_at"})
_SYNTHETIC_WARNING = (
    "this API build has no containsSyntheticMedia field; set the altered/synthetic "
    "content disclosure in Studio"
)


@dataclass
class OpResult:
    """The outcome of one publish operation."""

    action: str
    video_id: str | None
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class VideoSummary:
    """One row of the channel's recent videos."""

    id: str
    title: str
    privacy: str
    published_at: str
    views: int


@dataclass
class VideoStats:
    """Window metrics for one video plus its lifetime counters.

    Impressions click-through rate is not exposed by the Analytics API; read it
    in YouTube Studio.
    """

    video_id: str
    days: int
    views: int
    estimated_minutes_watched: float
    average_view_duration: float
    average_view_percentage: float
    likes: int
    subscribers_gained: int
    lifetime_views: int
    lifetime_likes: int
    lifetime_comments: int


def _load_valid(manifest_path: str | Path) -> LoadedManifest:
    loaded = load_manifest(manifest_path)
    errors = [issue for issue in validate(loaded) if issue.severity == "error"]
    if errors:
        details = "; ".join(f"{issue.field}: {issue.message}" for issue in errors)
        raise SocialError(
            f"{loaded.path} has {len(errors)} validation error(s): {details}",
            code="validation_failed",
            next_step="Run `social validate <manifest>` and fix the listed fields.",
        )
    if loaded.manifest.youtube is None:
        raise SocialError(
            f"{loaded.path} has no youtube: section",
            code="no_youtube_section",
            next_step=(
                "Add a youtube: section, or draft one with "
                "`social init <folder> --account <name>`."
            ),
        )
    return loaded


def _supports_synthetic_media(api: YouTubeApi) -> bool:
    supports = getattr(api, "supports_synthetic_media", None)
    return bool(supports()) if callable(supports) else False


def _desired_snippet(loaded: LoadedManifest) -> dict[str, Any]:
    section = loaded.manifest.youtube
    assert section is not None
    snippet: dict[str, Any] = {
        "title": section.title if section.title is not None else loaded.manifest.title,
        "description": render_description(loaded),
        "tags": list(section.tags),
        "defaultLanguage": loaded.manifest.language,
        "defaultAudioLanguage": loaded.manifest.language,
    }
    if section.category is not None:
        snippet["categoryId"] = str(section.category)
    return snippet


def _desired_status(
    loaded: LoadedManifest, *, synthetic_supported: bool
) -> dict[str, Any]:
    section = loaded.manifest.youtube
    assert section is not None
    status: dict[str, Any] = {
        "privacyStatus": section.privacy,
        "selfDeclaredMadeForKids": section.made_for_kids,
    }
    if section.publish_at:
        status["publishAt"] = section.publish_at
    if synthetic_supported:
        status["containsSyntheticMedia"] = loaded.manifest.synthetic_media
    return status


def _confirmation_error(loaded: LoadedManifest, action: str) -> SocialError:
    return SocialError(
        f"{action} {loaded.path} would change what is visible on YouTube",
        code="confirmation_required",
        next_step=(
            "Review the summary, then rerun with --yes (CLI) or confirm: true (MCP)."
        ),
    )


def upload(
    manifest_path: str | Path,
    api: YouTubeApi,
    *,
    confirm: bool = False,
) -> OpResult:
    """Insert a new YouTube video and record it in the publish log."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None

    log = PublishLog.load(loaded.path)
    log.ensure_unpublished("youtube")
    if not confirm:
        raise _confirmation_error(loaded, "publishing")

    warnings: list[str] = []
    synthetic_supported = _supports_synthetic_media(api)
    if not synthetic_supported:
        warnings.append(_SYNTHETIC_WARNING)

    body = {
        "snippet": _desired_snippet(loaded),
        "status": _desired_status(loaded, synthetic_supported=synthetic_supported),
    }
    video_path = loaded.video_path
    if video_path is None:
        raise SocialError(
            "the manifest has no video",
            code="validation_failed",
            next_step="Set `video:` in the manifest.",
        )
    response = api.insert_video(body, video_path)
    video_id = str(response.get("id") or "")
    if not video_id:
        raise SocialError(
            "YouTube returned no video id for the upload",
            code="upload_failed",
            next_step="Check YouTube Studio for a partial upload, then retry.",
        )

    current = api.get_video(video_id, ["snippet", "status"])
    items = current.get("items") or []
    returned = items[0] if items else {}
    returned_privacy = (returned.get("status") or {}).get("privacyStatus")
    if section.privacy in {"unlisted", "public"} and returned_privacy == "private":
        warnings.append(f"private_locked: {PrivateLocked.next_step}")

    changed = ["video"]
    if loaded.thumbnail_path is not None:
        try:
            api.set_thumbnail(video_id, loaded.thumbnail_path)
        except ThumbnailNotAllowed as exc:
            warnings.append(f"thumbnail_not_allowed: {exc.next_step}")
        else:
            changed.append("thumbnail")

    url = f"https://youtu.be/{video_id}"
    log.record(
        "youtube",
        account=section.account,
        id=video_id,
        url=url,
        via="api",
        action="upload",
        changed=changed,
        warnings=warnings,
    )
    log.save()
    return OpResult(action="upload", video_id=video_id, changed=changed, warnings=warnings)


def update(
    manifest_path: str | Path,
    api: YouTubeApi,
    *,
    confirm: bool = False,
) -> OpResult:
    """Send only the sections whose managed fields differ from the live video."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None

    log = PublishLog.load(loaded.path)
    entry = log.entry("youtube")
    if entry is None:
        raise SocialError(
            f"{loaded.path} has no YouTube entry in {log.path}",
            code="not_published",
            next_step="Publish the video first with `social publish <manifest> --yes`.",
        )

    response = api.get_video(entry.id, ["snippet", "status"])
    items = response.get("items") or []
    if not items:
        raise SocialError(
            f"YouTube has no video {entry.id} for this manifest",
            code="video_not_found",
            next_step="Check that the video still exists in Studio.",
        )
    current = items[0]
    current_snippet = current.get("snippet") or {}
    current_status = current.get("status") or {}

    warnings: list[str] = []
    synthetic_supported = _supports_synthetic_media(api)
    if not synthetic_supported:
        warnings.append(_SYNTHETIC_WARNING)

    desired_snippet = _desired_snippet(loaded)
    desired_status = _desired_status(loaded, synthetic_supported=synthetic_supported)
    snippet_changes = [
        name
        for key, name in _SNIPPET_FIELDS.items()
        if desired_snippet.get(key) != current_snippet.get(key)
    ]
    status_changes = [
        name
        for key, name in _STATUS_FIELDS.items()
        if key in desired_status and desired_status.get(key) != current_status.get(key)
    ]
    changed = snippet_changes + status_changes
    if not changed:
        return OpResult(action="update", video_id=entry.id, changed=[], warnings=warnings)

    if _VISIBILITY_FIELDS.intersection(changed) and not confirm:
        raise _confirmation_error(loaded, "updating")

    body: dict[str, Any] = {"id": entry.id}
    parts: list[str] = []
    if snippet_changes:
        body["snippet"] = desired_snippet
        parts.append("snippet")
    if status_changes:
        body["status"] = desired_status
        parts.append("status")
    api.update_video(body, parts)

    log.record(
        "youtube",
        account=section.account,
        id=entry.id,
        url=entry.url or f"https://youtu.be/{entry.id}",
        via="api",
        action="update",
        changed=changed,
        warnings=warnings,
    )
    log.save()
    return OpResult(action="update", video_id=entry.id, changed=changed, warnings=warnings)


def _require_entry(loaded: LoadedManifest) -> tuple[PublishLog, PlatformEntry]:
    log = PublishLog.load(loaded.path)
    entry = log.entry("youtube")
    if entry is None:
        raise SocialError(
            f"{loaded.path} has no YouTube entry in {log.path}",
            code="not_published",
            next_step="Publish the video first with `social publish <manifest> --yes`.",
        )
    return log, entry


def _video_url(entry: PlatformEntry) -> str:
    return entry.url or f"https://youtu.be/{entry.id}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _playlist_video_id(item: dict[str, Any]) -> str:
    snippet = item.get("snippet") or {}
    resource = snippet.get("resourceId") or {}
    if resource.get("videoId"):
        return str(resource["videoId"])
    content = item.get("contentDetails") or {}
    if content.get("videoId"):
        return str(content["videoId"])
    return ""


def _search_video_id(item: dict[str, Any]) -> str:
    raw = item.get("id")
    if isinstance(raw, dict):
        return str(raw.get("videoId") or "")
    return str(raw or "")


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _report_values(report: dict[str, Any], metrics: list[str]) -> dict[str, Any]:
    headers = report.get("columnHeaders") or []
    names = [str(header.get("name", "")) for header in headers if isinstance(header, dict)]
    rows = report.get("rows") or []
    row = rows[0] if rows and isinstance(rows[0], list) else []
    values: dict[str, Any] = {}
    for index, name in enumerate(names):
        if index < len(row):
            values[name] = row[index]
    return {metric: values.get(metric, 0) for metric in metrics}


def sync_captions(manifest_path: str | Path, api: YouTubeApi) -> OpResult:
    """Insert missing tracks, update changed SRTs and never delete an extra track."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None
    log, entry = _require_entry(loaded)

    response = api.list_captions(entry.id)
    tracks = [track for track in response.get("items") or [] if isinstance(track, dict)]
    by_language: dict[str, dict[str, Any]] = {}
    for track in tracks:
        snippet = track.get("snippet") or {}
        language = snippet.get("language")
        if language and str(language) not in by_language:
            by_language[str(language)] = track

    hashes = dict(entry.caption_hashes)
    changed: list[str] = []
    warnings: list[str] = []
    for language, path in loaded.caption_paths.items():
        digest = _sha256(path)
        existing_track = by_language.get(language)
        if existing_track is None:
            api.insert_caption(entry.id, language, f"{language} captions", path)
            changed.append(language)
        elif hashes.get(language) != digest:
            caption_id = str(existing_track.get("id") or "")
            if not caption_id:
                raise SocialError(
                    f"the caption track for {language!r} has no id",
                    code="caption_not_found",
                    next_step="Check the caption tracks in Studio, then retry.",
                )
            api.update_caption(caption_id, path)
            changed.append(language)
        hashes[language] = digest

    for language in by_language:
        if language not in loaded.manifest.captions:
            warnings.append(
                f"caption track {language!r} is not in the manifest; left untouched"
            )

    if changed:
        log.set_caption_hashes("youtube", hashes)
        log.record(
            "youtube",
            account=section.account,
            id=entry.id,
            url=_video_url(entry),
            via="api",
            action="captions",
            changed=changed,
            warnings=warnings,
        )
        log.save()
    return OpResult(
        action="captions",
        video_id=entry.id,
        changed=changed,
        warnings=warnings,
    )


def sync_localizations(manifest_path: str | Path, api: YouTubeApi) -> OpResult:
    """Set the default language when missing and replace the localizations map."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None
    log, entry = _require_entry(loaded)

    response = api.get_video(entry.id, ["snippet"])
    items = response.get("items") or []
    if not items:
        raise SocialError(
            f"YouTube has no video {entry.id} for this manifest",
            code="video_not_found",
            next_step="Check that the video still exists in Studio.",
        )
    current = items[0].get("snippet") or {}
    desired = {
        language: localization.model_dump(exclude_none=True)
        for language, localization in section.localizations.items()
    }

    changed: list[str] = []
    snippet: dict[str, Any] = {}
    if not current.get("defaultLanguage"):
        snippet["defaultLanguage"] = loaded.manifest.language
        changed.append("default_language")
    if (current.get("localizations") or {}) != desired:
        snippet["localizations"] = desired
        changed.append("localizations")
    if not changed:
        return OpResult(action="localizations", video_id=entry.id, changed=[], warnings=[])

    api.update_video({"id": entry.id, "snippet": snippet}, ["snippet"])
    log.record(
        "youtube",
        account=section.account,
        id=entry.id,
        url=_video_url(entry),
        via="api",
        action="localizations",
        changed=changed,
        warnings=[],
    )
    log.save()
    return OpResult(action="localizations", video_id=entry.id, changed=changed, warnings=[])


def sync_playlists(manifest_path: str | Path, api: YouTubeApi) -> OpResult:
    """Create missing playlists as private and add the video once."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None
    log, entry = _require_entry(loaded)

    response = api.list_playlists()
    by_title = {
        str((playlist.get("snippet") or {}).get("title", "")): playlist
        for playlist in response.get("items") or []
        if isinstance(playlist, dict)
    }
    changed: list[str] = []
    for title in section.playlists:
        playlist = by_title.get(title)
        touched = False
        if playlist is None:
            created = api.insert_playlist(title, "private")
            playlist_id = str(created.get("id") or "")
            touched = True
        else:
            playlist_id = str(playlist.get("id") or "")
        if not playlist_id:
            raise SocialError(
                f"YouTube returned no id for playlist {title!r}",
                code="playlist_not_found",
                next_step="Check the playlist in Studio, then retry.",
            )
        items = api.list_playlist_items(playlist_id).get("items") or []
        if not any(_playlist_video_id(item) == entry.id for item in items):
            api.insert_playlist_item(playlist_id, entry.id)
            touched = True
        if touched:
            changed.append(title)

    if changed:
        log.record(
            "youtube",
            account=section.account,
            id=entry.id,
            url=_video_url(entry),
            via="api",
            action="playlists",
            changed=changed,
            warnings=[],
        )
        log.save()
    return OpResult(action="playlists", video_id=entry.id, changed=changed, warnings=[])


def list_videos(api: YouTubeApi, limit: int = 20) -> list[VideoSummary]:
    """Return the channel's newest videos first with privacy and view count."""
    response = api.list_my_videos(limit)
    summaries: list[VideoSummary] = []
    for item in response.get("items") or []:
        if not isinstance(item, dict):
            continue
        video_id = _search_video_id(item)
        if not video_id:
            continue
        detail = api.get_video(video_id, ["snippet", "status", "statistics"])
        items = detail.get("items") or []
        info = items[0] if items else item
        snippet = info.get("snippet") or {}
        status = info.get("status") or {}
        statistics = info.get("statistics") or {}
        summaries.append(
            VideoSummary(
                id=video_id,
                title=str(snippet.get("title", "")),
                privacy=str(status.get("privacyStatus", "")),
                published_at=str(snippet.get("publishedAt", "")),
                views=int(_number(statistics.get("viewCount"))),
            )
        )
    return summaries[:limit]


def stats(api: YouTubeApi, video_id: str, days: int = 28) -> VideoStats:
    """Window metrics plus lifetime counters.

    Impressions click-through rate is not exposed by the Analytics API; read it
    in YouTube Studio.
    """
    end = datetime.now(UTC).date()
    start = end - timedelta(days=max(days - 1, 0))
    metrics = [
        "views",
        "estimatedMinutesWatched",
        "averageViewDuration",
        "averageViewPercentage",
        "likes",
        "subscribersGained",
    ]
    report = api.analytics_report(video_id, start.isoformat(), end.isoformat(), metrics)
    values = _report_values(report, metrics)

    detail = api.get_video(video_id, ["statistics"])
    items = detail.get("items") or []
    statistics = (items[0].get("statistics") if items else {}) or {}
    return VideoStats(
        video_id=video_id,
        days=days,
        views=int(_number(values.get("views"))),
        estimated_minutes_watched=_number(values.get("estimatedMinutesWatched")),
        average_view_duration=_number(values.get("averageViewDuration")),
        average_view_percentage=_number(values.get("averageViewPercentage")),
        likes=int(_number(values.get("likes"))),
        subscribers_gained=int(_number(values.get("subscribersGained"))),
        lifetime_views=int(_number(statistics.get("viewCount"))),
        lifetime_likes=int(_number(statistics.get("likeCount"))),
        lifetime_comments=int(_number(statistics.get("commentCount"))),
    )
