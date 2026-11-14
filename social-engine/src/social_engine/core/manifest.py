"""The per-video ``publish.yaml`` manifest: load and validate.

Shared fields live at the top of the file; each platform gets one optional
section (``youtube:`` today). Paths are relative to the manifest. Validation is
offline: it reads local files and probes the video with FFprobe, but never
touches a platform.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

BCP47_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
REQUIRED_FIELDS = ("video", "title", "language")


class Localization(BaseModel):
    """A localized title and/or description for one language."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    description: str | None = None


class YouTubeSection(BaseModel):
    """YouTube-specific fields; ``title``/``description`` override the shared ones."""

    model_config = ConfigDict(extra="allow")

    account: str = ""
    kind: str = "long"
    title: str | None = None
    description: str | None = None
    chapters: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: int | None = None
    made_for_kids: bool = False
    privacy: str = "private"
    publish_at: str | None = None
    localizations: dict[str, Localization] = Field(default_factory=dict)
    playlists: list[str] = Field(default_factory=list)
    dubs: dict[str, str] = Field(default_factory=dict)

    @field_validator("publish_at", mode="before")
    @classmethod
    def _publish_at_as_text(cls, value: object) -> object:
        """YAML turns an unquoted ISO timestamp into ``datetime``; keep text."""
        if isinstance(value, datetime):
            return value.isoformat()
        return value


class Manifest(BaseModel):
    """Parsed ``publish.yaml``.

    Required-field checks live in :func:`validate` rather than in the model so a
    freshly drafted manifest can be loaded and reported on field by field.
    ``extra="allow"`` keeps sections for platforms this version does not model.
    """

    model_config = ConfigDict(extra="allow")

    video: str = ""
    title: str = ""
    description: str = ""
    thumbnail: str | None = None
    language: str = "en"
    synthetic_media: bool = True
    captions: dict[str, str] = Field(default_factory=dict)
    youtube: YouTubeSection | None = None


@dataclass(frozen=True)
class Issue:
    """One validation finding."""

    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class LoadedManifest:
    """A manifest plus the directory relative paths resolve against."""

    manifest: Manifest
    path: Path
    base_dir: Path

    def resolve(self, value: str | None) -> Path | None:
        """Resolve a manifest-relative path (absolute paths are left alone)."""
        if not value:
            return None
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = self.base_dir / candidate
        return candidate

    @property
    def video_path(self) -> Path | None:
        return self.resolve(self.manifest.video)

    @property
    def thumbnail_path(self) -> Path | None:
        return self.resolve(self.manifest.thumbnail)

    @property
    def chapters_path(self) -> Path | None:
        section = self.manifest.youtube
        return self.resolve(section.chapters) if section is not None else None

    @property
    def caption_paths(self) -> dict[str, Path]:
        return {lang: self._resolve_required(rel) for lang, rel in self.manifest.captions.items()}

    @property
    def dub_paths(self) -> dict[str, Path]:
        section = self.manifest.youtube
        if section is None:
            return {}
        return {lang: self._resolve_required(rel) for lang, rel in section.dubs.items()}

    def _resolve_required(self, value: str) -> Path:
        resolved = self.resolve(value)
        assert resolved is not None
        return resolved


@dataclass
class _ValidationState:
    issues: list[Issue] = field(default_factory=list)

    def error(self, field_name: str, message: str) -> None:
        self.issues.append(Issue(field_name, message))


def load_manifest(path: str | Path) -> LoadedManifest:
    """Load and parse ``publish.yaml`` without validating it.

    A directory argument is read as the ``publish.yaml`` inside it.
    """
    manifest_path = Path(path).expanduser()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "publish.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path} must contain a YAML mapping")
    return LoadedManifest(
        manifest=Manifest.model_validate(data),
        path=manifest_path,
        base_dir=manifest_path.parent,
    )


def validate(loaded: LoadedManifest) -> list[Issue]:
    """Return every offline validation problem, most fundamental first."""
    manifest = loaded.manifest
    state = _ValidationState()

    for name in REQUIRED_FIELDS:
        if not getattr(manifest, name):
            state.error(name, f"{name} is required")

    _validate_languages(manifest, state)
    _validate_files(loaded, state)
    if manifest.youtube is not None:
        from ..platforms.youtube.rules import validate_youtube

        state.issues.extend(validate_youtube(loaded))

    return state.issues


def _validate_languages(manifest: Manifest, state: _ValidationState) -> None:
    if manifest.language and not BCP47_RE.fullmatch(manifest.language):
        state.error("language", f"language code {manifest.language!r} is not BCP-47-like")
    for code in manifest.captions:
        if not BCP47_RE.fullmatch(code):
            state.error("captions", f"language code {code!r} is not BCP-47-like")


def _referenced_files(loaded: LoadedManifest) -> list[tuple[str, Path]]:
    manifest = loaded.manifest
    files: list[tuple[str, Path]] = []
    for field_name, value in (
        ("video", manifest.video),
        ("thumbnail", manifest.thumbnail),
    ):
        path = loaded.resolve(value)
        if path is not None:
            files.append((field_name, path))
    for language, path in loaded.caption_paths.items():
        files.append((f"captions.{language}", path))
    return files


def _validate_files(loaded: LoadedManifest, state: _ValidationState) -> None:
    for field_name, path in _referenced_files(loaded):
        if not path.is_file():
            state.error(field_name, f"referenced file is missing: {path}")
