"""YouTube browser-lane operations.

Upload and dub flows stop at ``ready_to_confirm``; only a later ``confirm`` that
returns ``done`` writes the publish log. The owner's yes is the Python caller's
job (CLI ``--yes`` / MCP ``confirm: true``).

Live Studio can reset the dialog when the driver disconnects; the upload then
remains a private draft. See ``docs/live-verification.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.browser import BrowserResult, browser_call
from ...core.errors import SocialError
from ...core.manifest import LoadedManifest
from ...core.publish_log import PublishLog
from .ops import _load_valid
from .rules import render_description


def _resolved_payload(loaded: LoadedManifest) -> dict[str, Any]:
    manifest = loaded.manifest
    section = manifest.youtube
    assert section is not None
    thumbnail = loaded.thumbnail_path
    video = loaded.video_path
    return {
        "account": section.account,
        "video": str(video) if video is not None else "",
        "title": section.title if section.title is not None else manifest.title,
        "description": render_description(loaded),
        "thumbnail": str(thumbnail) if thumbnail is not None else None,
        "playlists": list(section.playlists),
        "made_for_kids": section.made_for_kids,
        "synthetic_media": manifest.synthetic_media,
        "language": manifest.language,
        "privacy": section.privacy,
        "publish_at": section.publish_at,
        "kind": section.kind,
    }


def browser_upload(manifest_path: str | Path) -> BrowserResult:
    """Open Studio, fill the upload details and stop before the final button."""
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None
    PublishLog.load(loaded.path).ensure_unpublished("youtube")
    return browser_call("youtube", "upload", _resolved_payload(loaded))


def browser_confirm(
    manifest_path: str | Path,
    video_id: str,
    expect: str,
) -> BrowserResult:
    """Click the final Studio button and, only when done, write the publish log."""
    if expect not in {"publish", "dubs"}:
        raise ValueError(f"expect must be 'publish' or 'dubs'; got {expect!r}")
    loaded = _load_valid(manifest_path)
    section = loaded.manifest.youtube
    assert section is not None

    result = browser_call(
        "youtube",
        "confirm",
        {"account": section.account, "id": video_id, "expect": expect},
    )
    if result.status == "done":
        if expect == "dubs":
            raw = result.summary.get("dubs")
            changed = [str(item) for item in raw] if isinstance(raw, list) else []
        else:
            changed = ["publish"]
        log = PublishLog.load(loaded.path)
        log.record(
            "youtube",
            account=section.account,
            id=video_id,
            url=f"https://youtu.be/{video_id}",
            via="browser",
            action=expect,
            changed=changed,
            warnings=[],
        )
        log.save()
    return result


def browser_dubs(manifest_path: str | Path) -> BrowserResult:
    """Add the manifest's dubbed tracks and stop before the final publish."""
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
    dubs = {language: str(path) for language, path in loaded.dub_paths.items()}
    return browser_call(
        "youtube",
        "add-dubs",
        {"account": section.account, "id": entry.id, "dubs": dubs},
    )
