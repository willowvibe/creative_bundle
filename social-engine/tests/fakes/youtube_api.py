"""In-memory YouTube API used by ops, CLI and MCP tests."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FakeCall:
    """One recorded API call."""

    name: str
    kwargs: dict[str, Any]


class FakeYouTubeApi:
    """A small, deterministic implementation of :class:`YouTubeApi`.

    ``errors`` injects an exception by method name. ``force_private`` mimics the
    unaudited API project lock: an upload asking for unlisted/public is stored as
    private, so the caller can detect the read-back mismatch.
    """

    def __init__(self) -> None:
        self.calls: list[FakeCall] = []
        self.errors: dict[str, Exception] = {}
        self.force_private = False
        self.synthetic_supported = True
        self.channel_id = "UC_FAKE"
        self.next_video_id = "fake-video-1"

        self.videos: dict[str, dict[str, Any]] = {}
        self.captions: dict[str, list[dict[str, Any]]] = {}
        self.caption_paths: dict[str, Path] = {}
        self.playlists: list[dict[str, Any]] = []
        self.playlist_items: dict[str, list[dict[str, Any]]] = {}
        self.thumbnail_paths: dict[str, Path] = {}
        self.analytics_result: dict[str, Any] = {"columnHeaders": [], "rows": []}

        self._caption_counter = 0
        self._playlist_counter = 0
        self._item_counter = 0

    @property
    def call_names(self) -> list[str]:
        return [call.name for call in self.calls]

    def _record(self, call_name: str, **kwargs: Any) -> None:
        self.calls.append(FakeCall(call_name, kwargs))
        error = self.errors.get(call_name)
        if error is not None:
            raise error

    # -- videos ---------------------------------------------------------

    def insert_video(
        self,
        body: dict[str, Any],
        media_path: Path,
        on_progress: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        self._record("insert_video", body=body, media_path=media_path)
        if on_progress is not None:
            on_progress(1.0)
        video_id = self.next_video_id
        snippet = copy.deepcopy(body.get("snippet") or {})
        status = copy.deepcopy(body.get("status") or {})
        if self.force_private:
            status["privacyStatus"] = "private"
        self.videos[video_id] = {"id": video_id, "snippet": snippet, "status": status}
        self.next_video_id = f"fake-video-{len(self.videos) + 1}"
        return {"id": video_id}

    def update_video(self, body: dict[str, Any], parts: list[str]) -> dict[str, Any]:
        self._record("update_video", body=body, parts=parts)
        video_id = str(body.get("id") or next(iter(self.videos), ""))
        video = self.videos.setdefault(video_id, {"id": video_id})
        for part in parts:
            section = body.get(part)
            if isinstance(section, dict):
                merged = dict(video.get(part) or {})
                merged.update(section)
                video[part] = merged
        return copy.deepcopy(video)

    def get_video(self, video_id: str, parts: list[str]) -> dict[str, Any]:
        self._record("get_video", video_id=video_id, parts=parts)
        video = self.videos.get(video_id)
        return {"items": [copy.deepcopy(video)] if video is not None else []}

    def set_thumbnail(self, video_id: str, path: Path) -> dict[str, Any]:
        self._record("set_thumbnail", video_id=video_id, path=path)
        self.thumbnail_paths[video_id] = path
        return {"videoId": video_id}

    # -- captions -------------------------------------------------------

    def list_captions(self, video_id: str) -> dict[str, Any]:
        self._record("list_captions", video_id=video_id)
        return {"items": copy.deepcopy(self.captions.get(video_id, []))}

    def insert_caption(
        self, video_id: str, language: str, name: str, path: Path
    ) -> dict[str, Any]:
        self._record(
            "insert_caption", video_id=video_id, language=language, name=name, path=path
        )
        self._caption_counter += 1
        caption_id = f"caption-{self._caption_counter}"
        item = {
            "id": caption_id,
            "snippet": {"videoId": video_id, "language": language, "name": name},
        }
        self.captions.setdefault(video_id, []).append(item)
        self.caption_paths[caption_id] = path
        return copy.deepcopy(item)

    def update_caption(self, caption_id: str, path: Path) -> dict[str, Any]:
        self._record("update_caption", caption_id=caption_id, path=path)
        for items in self.captions.values():
            for item in items:
                if item.get("id") == caption_id:
                    self.caption_paths[caption_id] = path
                    return copy.deepcopy(item)
        return {"id": caption_id}

    # -- playlists ------------------------------------------------------

    def list_playlists(self) -> dict[str, Any]:
        self._record("list_playlists")
        return {"items": copy.deepcopy(self.playlists)}

    def insert_playlist(self, title: str, privacy: str) -> dict[str, Any]:
        self._record("insert_playlist", title=title, privacy=privacy)
        self._playlist_counter += 1
        playlist = {
            "id": f"playlist-{self._playlist_counter}",
            "snippet": {"title": title},
            "status": {"privacyStatus": privacy},
        }
        self.playlists.append(playlist)
        return copy.deepcopy(playlist)

    def list_playlist_items(self, playlist_id: str) -> dict[str, Any]:
        self._record("list_playlist_items", playlist_id=playlist_id)
        return {"items": copy.deepcopy(self.playlist_items.get(playlist_id, []))}

    def insert_playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]:
        self._record("insert_playlist_item", playlist_id=playlist_id, video_id=video_id)
        self._item_counter += 1
        item = {
            "id": f"item-{self._item_counter}",
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            },
        }
        self.playlist_items.setdefault(playlist_id, []).append(item)
        return copy.deepcopy(item)

    # -- channel and analytics -----------------------------------------

    def list_my_videos(self, limit: int) -> dict[str, Any]:
        self._record("list_my_videos", limit=limit)
        newest_first = list(reversed(list(self.videos.values())))
        items = [
            {
                "id": {"kind": "youtube#video", "videoId": video["id"]},
                "snippet": copy.deepcopy(video.get("snippet") or {}),
            }
            for video in newest_first[:limit]
        ]
        return {"items": items}

    def my_channel_id(self) -> str:
        self._record("my_channel_id")
        return self.channel_id

    def analytics_report(
        self, video_id: str, start: str, end: str, metrics: list[str]
    ) -> dict[str, Any]:
        self._record(
            "analytics_report",
            video_id=video_id,
            start=start,
            end=end,
            metrics=metrics,
        )
        return copy.deepcopy(self.analytics_result)

    def supports_synthetic_media(self) -> bool:
        return self.synthetic_supported
