"""YouTube-specific validation rules and description rendering.

These checks apply only when a manifest has a ``youtube:`` section. The shared
fields they read (title, description, thumbnail) are validated here because the
limits are YouTube's, not universal.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ...core.manifest import BCP47_RE, Issue, LoadedManifest, YouTubeSection
from ...core.media import MediaInfoError, probe_media

TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
SHORT_MAX_SECONDS = 180.0
THUMBNAIL_SUFFIXES = {".jpg", ".jpeg", ".png"}
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
PRIVACY_VALUES = {"private", "unlisted", "public"}
KIND_VALUES = {"long", "short"}

_Error = Callable[[str, str], None]


def validate_youtube(loaded: LoadedManifest) -> list[Issue]:
    """Return every YouTube validation problem for a manifest with a section."""
    section = loaded.manifest.youtube
    if section is None:
        return []

    issues: list[Issue] = []

    def error(field_name: str, message: str) -> None:
        issues.append(Issue(field_name, message))

    if not section.account:
        error("youtube.account", "youtube.account is required")
    if section.kind not in KIND_VALUES:
        error("youtube.kind", "youtube.kind must be long or short")

    title = section.title if section.title is not None else loaded.manifest.title
    _validate_title(title, error)
    _validate_description(render_description(loaded), error)
    _validate_tags(section.tags, error)
    _validate_privacy(section, error)
    _validate_publish_at(section, error)
    _validate_languages(section, error)
    _validate_files(loaded, error)
    _validate_thumbnail(loaded, error)
    _validate_short(loaded, section, error)

    return issues


def _validate_title(title: str, error: _Error) -> None:
    if len(title) > TITLE_MAX_CHARS:
        error("title", f"title is {len(title)} characters; the limit is {TITLE_MAX_CHARS}")
    if "<" in title or ">" in title:
        error("title", "title must not contain '<' or '>'")


def _validate_description(rendered: str, error: _Error) -> None:
    size = len(rendered.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        error(
            "description",
            f"rendered description is {size} bytes; the limit is {DESCRIPTION_MAX_BYTES}",
        )
    if "<" in rendered or ">" in rendered:
        error("description", "description must not contain '<' or '>'")


def _tag_length(tag: str) -> int:
    # YouTube adds two quote characters around a tag that contains a space.
    return len(tag) + (2 if " " in tag else 0)


def _validate_tags(tags: list[str], error: _Error) -> None:
    joined = ",".join(tag.strip() for tag in tags)
    total = sum(_tag_length(tag.strip()) for tag in tags) + max(len(tags) - 1, 0)
    if total > TAGS_MAX_CHARS:
        error("youtube.tags", f"tags are {total} characters; the limit is {TAGS_MAX_CHARS}")
    if "<" in joined or ">" in joined:
        error("youtube.tags", "tags must not contain '<' or '>'")


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _validate_privacy(section: YouTubeSection, error: _Error) -> None:
    if section.privacy not in PRIVACY_VALUES:
        error("youtube.privacy", "privacy must be one of private, unlisted or public")


def _validate_publish_at(section: YouTubeSection, error: _Error) -> None:
    if not section.publish_at:
        return
    when = _parse_datetime(section.publish_at)
    if when is None:
        error("youtube.publish_at", "publish_at must be an ISO-8601 date-time")
        return
    if when <= datetime.now(UTC):
        error("youtube.publish_at", "publish_at must be in the future")
    if section.privacy != "private":
        error(
            "youtube.publish_at",
            "publish_at requires privacy: private (YouTube schedules a private video)",
        )


def _validate_languages(section: YouTubeSection, error: _Error) -> None:
    for field_name, codes in (
        ("youtube.localizations", list(section.localizations)),
        ("youtube.dubs", list(section.dubs)),
    ):
        for code in codes:
            if not BCP47_RE.fullmatch(code):
                error(field_name, f"language code {code!r} is not BCP-47-like")


def _validate_files(loaded: LoadedManifest, error: _Error) -> None:
    chapters = loaded.chapters_path
    if chapters is not None and not chapters.is_file():
        error("youtube.chapters", f"referenced file is missing: {chapters}")
    for language, path in loaded.dub_paths.items():
        if not path.is_file():
            error(f"youtube.dubs.{language}", f"referenced file is missing: {path}")


def _validate_thumbnail(loaded: LoadedManifest, error: _Error) -> None:
    thumbnail = loaded.thumbnail_path
    if thumbnail is None or not thumbnail.is_file():
        return
    if thumbnail.suffix.lower() not in THUMBNAIL_SUFFIXES:
        error("thumbnail", "thumbnail must be a JPG or PNG file")
    size = thumbnail.stat().st_size
    if size > THUMBNAIL_MAX_BYTES:
        error(
            "thumbnail",
            f"thumbnail is {size} bytes; the limit is {THUMBNAIL_MAX_BYTES} (2 MB)",
        )


def _validate_short(
    loaded: LoadedManifest, section: YouTubeSection, error: _Error
) -> None:
    if section.kind != "short":
        return
    video = loaded.video_path
    if video is None or not video.is_file():
        return
    try:
        info = probe_media(video)
    except (MediaInfoError, OSError) as exc:
        error("video", f"could not probe video: {exc}")
        return
    width, height = info.width, info.height
    if width and height and width > height:
        error("youtube.kind", "kind: short requires a vertical video")
    duration = info.duration
    if duration is not None and duration > SHORT_MAX_SECONDS:
        error(
            "youtube.kind",
            f"kind: short requires a video of at most 180 seconds; this one is {duration:.0f}s",
        )


def _format_chapter_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _chapter_entries(loaded: LoadedManifest) -> list[dict[str, Any]]:
    path = loaded.chapters_path
    if path is None or not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("chapters", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _description_text(loaded: LoadedManifest) -> str:
    section = loaded.manifest.youtube
    if section is not None and section.description is not None:
        return section.description
    return loaded.manifest.description


def render_description(loaded: LoadedManifest) -> str:
    """Return the description with chapter lines appended.

    ``chapters.json`` is the file the ``chapters`` skill writes. The first
    chapter is normalised to ``0:00`` because YouTube requires a chapter list to
    start there.
    """
    description = _description_text(loaded).strip()
    lines: list[str] = []
    for index, chapter in enumerate(_chapter_entries(loaded)):
        title = str(chapter.get("title", "")).strip()
        if not title:
            continue
        start = float(chapter.get("start", 0.0))
        stamp = "0:00" if index == 0 else _format_chapter_time(start)
        lines.append(f"{stamp} {title}")
    if not lines:
        return description
    joined = "\n".join(lines)
    return f"{description}\n\n{joined}" if description else joined
