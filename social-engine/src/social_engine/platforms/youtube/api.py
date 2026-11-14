"""YouTube Data and Analytics API clients behind a small Protocol.

The rest of the code depends on :class:`YouTubeApi`, never on
``google-api-python-client`` directly, so tests run against an in-memory fake.
HTTP errors become :class:`SocialError` subclasses with an actionable next step.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build  # type: ignore[import-untyped]
from googleapiclient.errors import HttpError  # type: ignore[import-untyped]
from googleapiclient.http import MediaFileUpload  # type: ignore[import-untyped]

from ...core.errors import (
    AuthExpired,
    QuotaExceeded,
    SocialError,
    ThumbnailNotAllowed,
    UploadLimitExceeded,
)

UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
MAX_UPLOAD_RETRIES = 5
BACKOFF_SECONDS = (1, 2, 4, 8, 16)
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})

ServiceFactory = Callable[..., Any]
Sleep = Callable[[float], None]
ProgressCallback = Callable[[float], None]


class YouTubeApi(Protocol):
    """The slice of the YouTube API the publish operations use."""

    def insert_video(
        self,
        body: dict[str, Any],
        media_path: Path,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]: ...

    def update_video(self, body: dict[str, Any], parts: list[str]) -> dict[str, Any]: ...

    def get_video(self, video_id: str, parts: list[str]) -> dict[str, Any]: ...

    def set_thumbnail(self, video_id: str, path: Path) -> dict[str, Any]: ...

    def list_captions(self, video_id: str) -> dict[str, Any]: ...

    def insert_caption(
        self, video_id: str, language: str, name: str, path: Path
    ) -> dict[str, Any]: ...

    def update_caption(self, caption_id: str, path: Path) -> dict[str, Any]: ...

    def list_playlists(self) -> dict[str, Any]: ...

    def insert_playlist(self, title: str, privacy: str) -> dict[str, Any]: ...

    def list_playlist_items(self, playlist_id: str) -> dict[str, Any]: ...

    def insert_playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]: ...

    def list_my_videos(self, limit: int) -> dict[str, Any]: ...

    def my_channel_id(self) -> str: ...

    def analytics_report(
        self, video_id: str, start: str, end: str, metrics: list[str]
    ) -> dict[str, Any]: ...


def _status(err: HttpError) -> int | None:
    resp = getattr(err, "resp", None)
    status = getattr(resp, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _payload(err: HttpError) -> dict[str, Any]:
    content = getattr(err, "content", b"") or b""
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _reason(err: HttpError) -> str | None:
    error = _payload(err).get("error")
    if not isinstance(error, dict):
        return None
    details = error.get("errors")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        reason = details[0].get("reason")
        return str(reason) if reason else None
    return None


def _api_message(err: HttpError) -> str:
    error = _payload(err).get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return str(err)


def map_http_error(err: HttpError, *, operation: str | None = None) -> SocialError:
    """Translate a Google HTTP error into the matching social-engine error."""
    status = _status(err)
    reason = _reason(err)
    message = _api_message(err)

    if reason == "quotaExceeded":
        return QuotaExceeded(message)
    if reason == "uploadLimitExceeded":
        return UploadLimitExceeded(message)
    if status == 401 or reason == "authError":
        return AuthExpired(message)
    if status == 403 and (operation == "thumbnails.set" or "thumbnail" in message.lower()):
        return ThumbnailNotAllowed(message)
    return SocialError(message)


def _discovery_has_synthetic_media(service: Any) -> bool:
    root = getattr(service, "_rootDesc", None)
    schemas = root.get("schemas") if isinstance(root, dict) else None
    if not isinstance(schemas, dict):
        return False
    status = schemas.get("VideoStatus")
    if isinstance(status, dict):
        properties = status.get("properties")
        if isinstance(properties, dict) and "containsSyntheticMedia" in properties:
            return True
    video = schemas.get("Video")
    if isinstance(video, dict):
        properties = video.get("properties")
        status_ref = properties.get("status") if isinstance(properties, dict) else None
        if isinstance(status_ref, dict):
            ref_properties = status_ref.get("properties")
            if isinstance(ref_properties, dict) and "containsSyntheticMedia" in ref_properties:
                return True
    return False


def _image_mimetype(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


class GoogleYouTubeApi:
    """Thin wrapper over the official clients, built for one account's credentials."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        service_factory: ServiceFactory = build,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._youtube = service_factory(
            "youtube", "v3", credentials=credentials, cache_discovery=False
        )
        self._analytics = service_factory(
            "youtubeAnalytics", "v2", credentials=credentials, cache_discovery=False
        )
        self._sleep = sleep
        self._synthetic_supported: bool | None = None

    def _execute(self, request: Any, *, operation: str | None = None) -> Any:
        try:
            return request.execute()
        except HttpError as exc:
            raise map_http_error(exc, operation=operation) from exc

    def insert_video(
        self,
        body: dict[str, Any],
        media_path: Path,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Upload with resumable 8 MiB chunks, retrying transient failures."""
        media = MediaFileUpload(
            str(media_path),
            chunksize=UPLOAD_CHUNK_SIZE,
            resumable=True,
        )
        request = self._youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        retries = 0
        while True:
            try:
                status, response = request.next_chunk()
            except HttpError as exc:
                if _status(exc) not in RETRYABLE_STATUS or retries >= MAX_UPLOAD_RETRIES:
                    raise map_http_error(exc, operation="videos.insert") from exc
                self._sleep(BACKOFF_SECONDS[retries])
                retries += 1
                continue
            except (ConnectionError, TimeoutError) as exc:
                if retries >= MAX_UPLOAD_RETRIES:
                    raise SocialError(
                        f"upload failed after {MAX_UPLOAD_RETRIES} retries: {exc}",
                        code="upload_retry_exhausted",
                    ) from exc
                self._sleep(BACKOFF_SECONDS[retries])
                retries += 1
                continue
            if status is not None and on_progress is not None:
                on_progress(float(status.progress()))
            if response is not None:
                return dict(response)

    def update_video(self, body: dict[str, Any], parts: list[str]) -> dict[str, Any]:
        request = self._youtube.videos().update(part=",".join(parts), body=body)
        return self._execute(request, operation="videos.update")

    def get_video(self, video_id: str, parts: list[str]) -> dict[str, Any]:
        request = self._youtube.videos().list(part=",".join(parts), id=video_id)
        return self._execute(request, operation="videos.list")

    def set_thumbnail(self, video_id: str, path: Path) -> dict[str, Any]:
        media = MediaFileUpload(str(path), mimetype=_image_mimetype(path), resumable=False)
        request = self._youtube.thumbnails().set(videoId=video_id, media_body=media)
        return self._execute(request, operation="thumbnails.set")

    def list_captions(self, video_id: str) -> dict[str, Any]:
        request = self._youtube.captions().list(part="snippet", videoId=video_id)
        return self._execute(request, operation="captions.list")

    def insert_caption(
        self, video_id: str, language: str, name: str, path: Path
    ) -> dict[str, Any]:
        body = {"snippet": {"videoId": video_id, "language": language, "name": name}}
        media = MediaFileUpload(str(path), mimetype="text/plain", resumable=False)
        request = self._youtube.captions().insert(part="snippet", body=body, media_body=media)
        return self._execute(request, operation="captions.insert")

    def update_caption(self, caption_id: str, path: Path) -> dict[str, Any]:
        body = {"id": caption_id}
        media = MediaFileUpload(str(path), mimetype="text/plain", resumable=False)
        request = self._youtube.captions().update(part="snippet", body=body, media_body=media)
        return self._execute(request, operation="captions.update")

    def list_playlists(self) -> dict[str, Any]:
        request = self._youtube.playlists().list(part="snippet,status", mine=True, maxResults=50)
        return self._execute(request, operation="playlists.list")

    def insert_playlist(self, title: str, privacy: str) -> dict[str, Any]:
        body = {"snippet": {"title": title}, "status": {"privacyStatus": privacy}}
        request = self._youtube.playlists().insert(part="snippet,status", body=body)
        return self._execute(request, operation="playlists.insert")

    def list_playlist_items(self, playlist_id: str) -> dict[str, Any]:
        request = self._youtube.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, maxResults=50
        )
        return self._execute(request, operation="playlistItems.list")

    def insert_playlist_item(self, playlist_id: str, video_id: str) -> dict[str, Any]:
        body = {
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }
        request = self._youtube.playlistItems().insert(part="snippet", body=body)
        return self._execute(request, operation="playlistItems.insert")

    def list_my_videos(self, limit: int) -> dict[str, Any]:
        request = self._youtube.search().list(
            part="snippet",
            forMine=True,
            type="video",
            order="date",
            maxResults=min(max(limit, 1), 50),
        )
        return self._execute(request, operation="search.list")

    def my_channel_id(self) -> str:
        response = self._execute(
            self._youtube.channels().list(mine=True, part="id"),
            operation="channels.list",
        )
        items = response.get("items") or []
        channel_id = items[0].get("id") if items else None
        if not channel_id:
            raise SocialError(
                "the signed-in Google account has no YouTube channel",
                next_step="Sign in with the Google account that owns the channel, then retry.",
            )
        return str(channel_id)

    def analytics_report(
        self, video_id: str, start: str, end: str, metrics: list[str]
    ) -> dict[str, Any]:
        request = self._analytics.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics=",".join(metrics),
            filters=f"video=={video_id}",
        )
        return self._execute(request, operation="reports.query")

    def supports_synthetic_media(self) -> bool:
        """Whether this build of the discovery document exposes the disclosure field."""
        if self._synthetic_supported is None:
            self._synthetic_supported = _discovery_has_synthetic_media(self._youtube)
        return self._synthetic_supported
