"""Draft a ``publish.yaml`` from the files a finished video folder already holds.

``draft_manifest`` never guesses the text fields. It writes the detected media
paths plus empty title/description/tags marked ``# TODO(owner)`` so
:func:`social_engine.core.manifest.validate` points at what a person must write
before publishing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .media import MediaInfoError, probe_media

LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
THUMBNAIL_NAMES = ("thumbnail.jpg", "thumbnail.jpeg", "thumbnail.png")
AUDIO_SUFFIXES = {".m4a", ".wav", ".mp3", ".aac"}
SHORT_MAX_SECONDS = 180.0
MANIFEST_NAME = "publish.yaml"

_MANIFEST_ORDER = (
    "video",
    "title",
    "description",
    "thumbnail",
    "language",
    "synthetic_media",
    "captions",
    "youtube",
)
_YOUTUBE_ORDER = (
    "account",
    "kind",
    "chapters",
    "tags",
    "category",
    "made_for_kids",
    "privacy",
    "localizations",
    "playlists",
    "dubs",
)


class DraftError(ValueError):
    """The folder does not contain an unambiguous video to publish."""


def _pick_video(folder: Path) -> Path:
    finals = sorted(folder.glob("final*.mp4"))
    if len(finals) > 1:
        names = ", ".join(path.name for path in finals)
        raise DraftError(f"more than one final cut in {folder}: {names}; remove one or pass it")
    if len(finals) == 1:
        return finals[0]
    candidates = sorted(folder.glob("*.mp4"))
    if not candidates:
        raise DraftError(f"no .mp4 video found in {folder}")
    return max(candidates, key=lambda path: path.stat().st_size)


def _media_shape(video: Path) -> tuple[int, int, float | None]:
    """Return ``(width, height, duration)``; unreadable media defaults to landscape."""
    try:
        info = probe_media(video)
    except (MediaInfoError, OSError):
        return 0, 0, None
    return info.width, info.height, info.duration


def _is_short(width: int, height: int, duration: float | None) -> bool:
    vertical = bool(width and height and height > width)
    return vertical and duration is not None and duration <= SHORT_MAX_SECONDS


def _language_of(path: Path) -> str | None:
    """Return the trailing language code of a filename stem (``ep1.en`` → ``en``)."""
    stem = path.stem
    candidate = stem.rsplit(".", 1)[-1]
    return candidate if LANGUAGE_RE.fullmatch(candidate) else None


def _detected_media(folder: Path, suffix: str) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for path in sorted(folder.rglob(f"*{suffix}")):
        if not path.is_file():
            continue
        language = _language_of(path)
        if language is None:
            continue
        found.append((language, path))
    return found


def _relative(folder: Path, path: Path) -> str:
    return path.relative_to(folder).as_posix()


def _thumbnail(folder: Path) -> str | None:
    for name in THUMBNAIL_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate.name
    return None


def _ordered(data: dict[str, Any], order: tuple[str, ...]) -> dict[str, Any]:
    return {key: data[key] for key in order if key in data}


def _write(folder: Path, data: dict[str, Any]) -> Path:
    ordered = _ordered(data, _MANIFEST_ORDER)
    youtube = ordered.get("youtube")
    if isinstance(youtube, dict):
        ordered["youtube"] = _ordered(youtube, _YOUTUBE_ORDER)
    text = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    todos = {
        "title": "# TODO(owner): the video title, at most 100 characters",
        "description": "# TODO(owner): the description; chapters are appended from chapters.json",
        "tags": "# TODO(owner): comma-separated search tags, at most 500 characters",
    }
    lines: list[str] = []
    for line in text.splitlines():
        key = line.strip().split(":", 1)[0]
        if key in todos:
            line = f"{line}  {todos[key]}"
        lines.append(line)
    target = folder / MANIFEST_NAME
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def draft_manifest(
    folder: Path,
    *,
    platform: str = "youtube",
    account: str,
    kind: str | None = None,
) -> Path:
    """Write ``publish.yaml`` into ``folder`` and return its path.

    Raises :class:`FileExistsError` when a manifest already exists and
    :class:`DraftError` when the video is missing or ambiguous.
    """
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise DraftError(f"not a folder: {folder}")
    if platform != "youtube":
        raise DraftError(f"drafting is not implemented for {platform} yet")
    target = folder / MANIFEST_NAME
    if target.exists():
        raise FileExistsError(f"{target} already exists; edit it instead of drafting")

    video = _pick_video(folder)
    width, height, duration = _media_shape(video)
    resolved_kind = kind or ("short" if _is_short(width, height, duration) else "long")

    captions = _detected_media(folder, ".srt")
    language = captions[0][0] if captions else "en"
    dubs = [
        (dub_language, path)
        for dub_language, path in _detected_media(folder, "")
        if path.suffix.lower() in AUDIO_SUFFIXES and dub_language != language
    ]

    data: dict[str, Any] = {
        "video": _relative(folder, video),
        "title": "",
        "description": "",
        "thumbnail": _thumbnail(folder),
        "language": language,
        "synthetic_media": True,
        "captions": {
            caption_language: _relative(folder, path) for caption_language, path in captions
        },
        "youtube": {
            "account": account,
            "kind": resolved_kind,
            "tags": [],
            "category": 24,
            "made_for_kids": False,
            "privacy": "private",
            "localizations": {},
            "playlists": [],
            "dubs": {dub_language: _relative(folder, path) for dub_language, path in dubs},
        },
    }
    if (folder / "chapters.json").is_file():
        youtube = data["youtube"]
        assert isinstance(youtube, dict)
        youtube["chapters"] = "chapters.json"

    return _write(folder, data)
