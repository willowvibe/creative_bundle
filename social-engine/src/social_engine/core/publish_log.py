"""The publish log beside a manifest: what happened, per platform and account.

``publish-log.json`` is written by the tool, never by the owner. An upload
refuses to run again when a platform already has an entry; later actions append
to that entry's history instead.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import AlreadyPublished

LOG_NAME = "publish-log.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class PlatformEntry:
    """Everything this tool has done for one platform on one manifest."""

    account: str
    id: str
    url: str
    via: str
    published_at: str
    history: list[dict[str, Any]] = field(default_factory=list)
    caption_hashes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatformEntry:
        history = data.get("history")
        hashes = data.get("caption_hashes")
        return cls(
            account=str(data.get("account", "")),
            id=str(data.get("id", "")),
            url=str(data.get("url", "")),
            via=str(data.get("via", "")),
            published_at=str(data.get("published_at", "")),
            history=[item for item in history if isinstance(item, dict)]
            if isinstance(history, list)
            else [],
            caption_hashes={
                str(language): str(value)
                for language, value in hashes.items()
                if isinstance(hashes, dict)
            }
            if isinstance(hashes, dict)
            else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "id": self.id,
            "url": self.url,
            "via": self.via,
            "published_at": self.published_at,
            "history": self.history,
            "caption_hashes": self.caption_hashes,
        }


@dataclass
class PublishLog:
    """The log file for one manifest, loaded lazily into memory."""

    path: Path
    entries: dict[str, PlatformEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, manifest_path: str | Path) -> PublishLog:
        """Read ``publish-log.json`` beside the manifest (missing means empty)."""
        path = Path(manifest_path).expanduser()
        if path.is_dir():
            path = path / "publish.yaml"
        log_path = path.parent / LOG_NAME
        if not log_path.is_file():
            return cls(path=log_path)
        data = json.loads(log_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{log_path} must contain a JSON object")
        entries = {
            str(platform): PlatformEntry.from_dict(entry)
            for platform, entry in data.items()
            if isinstance(entry, dict)
        }
        return cls(path=log_path, entries=entries)

    def entry(self, platform: str) -> PlatformEntry | None:
        """Return the entry for ``platform``, if this manifest published there."""
        return self.entries.get(platform)

    def ensure_unpublished(self, platform: str) -> None:
        """Raise :class:`AlreadyPublished` when ``platform`` already has an entry."""
        entry = self.entry(platform)
        if entry is None:
            return
        detail = entry.url or entry.id or "an existing entry"
        raise AlreadyPublished(
            f"{platform} is already in {self.path}: {detail}; "
            "publishing again would create a duplicate."
        )

    def record(
        self,
        platform: str,
        *,
        account: str,
        id: str,
        url: str,
        via: str,
        action: str,
        changed: list[str],
        warnings: list[str],
    ) -> PlatformEntry:
        """Append one action to the entry and update its current id/url/via."""
        at = _now()
        entry = self.entries.get(platform)
        if entry is None:
            entry = PlatformEntry(
                account=account,
                id=id,
                url=url,
                via=via,
                published_at=at,
            )
            self.entries[platform] = entry
        else:
            entry.account = account
            entry.id = id
            entry.url = url
            entry.via = via
            entry.published_at = at
        entry.history.append(
            {
                "action": action,
                "at": at,
                "changed": list(changed),
                "warnings": list(warnings),
            }
        )
        return entry

    def set_caption_hashes(self, platform: str, hashes: dict[str, str]) -> None:
        """Replace the stored caption hashes for a platform (plan step 6)."""
        entry = self.entries[platform]
        entry.caption_hashes = dict(hashes)

    def save(self) -> None:
        """Write the log atomically: a temp file in the same directory, then rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            platform: entry.to_dict() for platform, entry in sorted(self.entries.items())
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
