"""YouTube Data API quota costs in one place.

Values copied from Google's quota calculator on 2026-11-03:
https://developers.google.com/youtube/v3/determine_quota_cost

The daily allowance is 10,000 units per Google Cloud project. ``videos.insert``
is reported inconsistently (1,600 from the shared pool, or a separate uploads
bucket); the calculator's documented value is kept here.
"""

from __future__ import annotations

from ...core.manifest import LoadedManifest

QUOTA_UNITS: dict[str, int] = {
    "videos.insert": 1600,
    "videos.update": 50,
    "videos.list": 1,
    "thumbnails.set": 50,
    "captions.insert": 400,
    "captions.update": 450,
    "captions.list": 50,
    "playlists.insert": 50,
    "playlists.list": 1,
    "playlistItems.insert": 50,
    "playlistItems.list": 1,
}


def estimate(loaded: LoadedManifest) -> int:
    """Estimate the quota units one upload pass over this manifest will spend.

    An upload pass inserts the video, reads it back, sets the thumbnail, inserts
    caption tracks and creates/adds playlists. Update passes get their estimate
    once the publish log exists (plan step 2).
    """
    manifest = loaded.manifest
    section = manifest.youtube
    if section is None:
        return 0

    units = QUOTA_UNITS["videos.insert"] + QUOTA_UNITS["videos.list"]
    if manifest.thumbnail:
        units += QUOTA_UNITS["thumbnails.set"]
    units += QUOTA_UNITS["captions.insert"] * len(manifest.captions)
    if section.playlists:
        units += QUOTA_UNITS["playlists.list"]
        units += QUOTA_UNITS["playlistItems.insert"] * len(section.playlists)
    return units
