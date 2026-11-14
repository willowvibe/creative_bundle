#!/usr/bin/env python3
"""Extract every edit made in FableCut compared to the exported source.

This creates a machine-readable manifest of all timeline changes so the engine
(or a direct renderer) can reproduce the user's edit exactly. It is
"foolproof" because it does not rely on the narrow ScenePlan importer; it
diffs the full FableCut project JSON.

Usage::

    python scripts/extract_fablecut_changes.py \
        --source videos/2026-07-12/suryastakam/fablecut/project.json \
        --live   tools/FableCut/project.json \
        --output videos/2026-07-12/suryastakam/fablecut/fablecut_changes.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

IGNORED_PROPS: frozenset[str] = frozenset()


def _match_key(clip: dict[str, Any]) -> str:
    """Return a stable identifier for matching a clip across source/live.

    Prefer ``name`` (we control the export names), then ``mediaId`` for
    media-backed clips, then ``kind+track+index`` as a last resort.
    """
    name = clip.get("name", "")
    if name:
        return f"name:{name}"
    media_id = clip.get("mediaId")
    if media_id:
        return f"media:{media_id}"
    return f"kind:{clip.get('kind')} track:{clip.get('track')}"


def _clip_identity(clip: dict[str, Any]) -> dict[str, Any]:
    """Return a human-readable identity block for a clip."""
    return {
        "id": clip.get("id"),
        "name": clip.get("name"),
        "kind": clip.get("kind"),
        "track": clip.get("track"),
        "mediaId": clip.get("mediaId"),
    }


def _sorted_clips(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(clips, key=lambda c: (c.get("track", ""), c.get("start", 0.0)))


def _diff_props(old_props: dict[str, Any], new_props: dict[str, Any]) -> dict[str, Any]:
    """Return changed props as {prop: {"old": ..., "new": ...}}."""
    changes: dict[str, Any] = {}
    all_keys = set(old_props) | set(new_props)
    for k in all_keys:
        if k in IGNORED_PROPS:
            continue
        o = old_props.get(k)
        n = new_props.get(k)
        # Normalize floats to avoid spurious differences from JSON serialization.
        if isinstance(o, float) and isinstance(n, float):
            if abs(o - n) > 1e-6:
                changes[k] = {"old": o, "new": n}
        elif o != n:
            changes[k] = {"old": o, "new": n}
    return changes


def _diff_keyframes(
    old_kf: dict[str, list[dict[str, Any]]],
    new_kf: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Return changed keyframe channels."""
    changes: dict[str, Any] = {}
    for channel in set(old_kf) | set(new_kf):
        old_list = old_kf.get(channel, [])
        new_list = new_kf.get(channel, [])
        if old_list != new_list:
            changes[channel] = {"old": old_list, "new": new_list}
    return changes


def extract_changes(
    source_project: dict[str, Any],
    live_project: dict[str, Any],
) -> dict[str, Any]:
    """Build a manifest of every difference between source and live."""
    manifest: dict[str, Any] = {
        "source_revision": source_project.get("revision"),
        "live_revision": live_project.get("revision"),
        "project_settings": {},
        "clips": {
            "added": [],
            "removed": [],
            "modified": [],
        },
        "media": {
            "added": [],
            "removed": [],
        },
    }

    # Project-level settings.
    for key in ["name", "width", "height", "fps", "background"]:
        if source_project.get(key) != live_project.get(key):
            manifest["project_settings"][key] = {
                "old": source_project.get(key),
                "new": live_project.get(key),
            }

    # Media library changes.
    src_media = {m.get("id"): m for m in source_project.get("media", [])}
    live_media = {m.get("id"): m for m in live_project.get("media", [])}
    for mid in set(live_media) - set(src_media):
        manifest["media"]["added"].append(live_media[mid])
    for mid in set(src_media) - set(live_media):
        manifest["media"]["removed"].append(src_media[mid])

    # Clip-level changes.
    src_clips = _sorted_clips(source_project.get("clips", []))
    live_clips = _sorted_clips(live_project.get("clips", []))
    src_by_key = {_match_key(c): c for c in src_clips}
    live_by_key = {_match_key(c): c for c in live_clips}

    for key in set(src_by_key) - set(live_by_key):
        manifest["clips"]["removed"].append(_clip_identity(src_by_key[key]))
    for key in set(live_by_key) - set(src_by_key):
        manifest["clips"]["added"].append(_clip_identity(live_by_key[key]))

    for key in set(src_by_key) & set(live_by_key):
        src_clip = src_by_key[key]
        live_clip = live_by_key[key]
        clip_changes: dict[str, Any] = {"identity": _clip_identity(live_clip)}

        for field in ["track", "start", "in", "duration"]:
            if src_clip.get(field) != live_clip.get(field):
                clip_changes[field] = {"old": src_clip.get(field), "new": live_clip.get(field)}

        props_diff = _diff_props(
            src_clip.get("props", {}),
            live_clip.get("props", {}),
        )
        if props_diff:
            clip_changes["props"] = props_diff

        kf_diff = _diff_keyframes(
            src_clip.get("keyframes", {}),
            live_clip.get("keyframes", {}),
        )
        if kf_diff:
            clip_changes["keyframes"] = kf_diff

        for trans in ["transitionIn", "transitionOut"]:
            if src_clip.get(trans) != live_clip.get(trans):
                clip_changes[trans] = {"old": src_clip.get(trans), "new": live_clip.get(trans)}

        if len(clip_changes) > 1:  # identity always present
            manifest["clips"]["modified"].append(clip_changes)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract every FableCut edit into a machine-readable manifest."
    )
    parser.add_argument("--source", required=True, type=Path, help="Exported source project.json")
    parser.add_argument(
        "--live", required=True, type=Path, help="Live/edited FableCut project.json"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Where to write the manifest JSON"
    )
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    live = json.loads(args.live.read_text(encoding="utf-8"))
    manifest = extract_changes(source, live)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    modified = len(manifest["clips"]["modified"])
    added = len(manifest["clips"]["added"])
    removed = len(manifest["clips"]["removed"])
    print(
        f"Extracted FableCut changes: {modified} modified, {added} added, {removed} removed clips.\n"
        f"Manifest written to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
