"""Tests for the YouTube API wrapper, error mapping and discovery feature check."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes.youtube_api import FakeYouTubeApi
from googleapiclient.errors import HttpError

import social_engine.platforms.youtube.api as api_module
from social_engine.core.errors import (
    AuthExpired,
    QuotaExceeded,
    SocialError,
    ThumbnailNotAllowed,
    UploadLimitExceeded,
)
from social_engine.platforms.youtube.api import (
    MAX_UPLOAD_RETRIES,
    UPLOAD_CHUNK_SIZE,
    GoogleYouTubeApi,
    map_http_error,
)


class FakeResponse:
    def __init__(self, status: int, reason: str = "") -> None:
        self.status = status
        self.reason = reason


def http_error(
    status: int, *, reason: str | None = None, message: str = "boom"
) -> HttpError:
    details = [{"reason": reason, "message": message}] if reason is not None else []
    content = json.dumps(
        {"error": {"code": status, "message": message, "errors": details}}
    ).encode("utf-8")
    return HttpError(FakeResponse(status), content)


class FakeRequest:
    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
        next_chunks: list[Any] | None = None,
    ) -> None:
        self.payload = payload if payload is not None else {}
        self.error = error
        self.next_chunks = list(next_chunks or [])
        self.next_chunk_calls = 0

    def execute(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.payload

    def next_chunk(self) -> Any:
        self.next_chunk_calls += 1
        outcome = self.next_chunks.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeResource:
    def __init__(self, service: FakeService, resource: str) -> None:
        self.service = service
        self.resource = resource

    def __getattr__(self, method: str) -> Callable[..., FakeRequest]:
        def call(**kwargs: Any) -> FakeRequest:
            self.service.calls.append((self.resource, method, kwargs))
            return self.service.request_for(self.resource, method)

        return call


DEFAULT_PAYLOADS: dict[tuple[str, str], dict[str, Any]] = {
    ("channels", "list"): {"items": [{"id": "UC123"}]},
    ("videos", "list"): {"items": []},
    ("captions", "list"): {"items": []},
    ("playlists", "list"): {"items": []},
    ("playlistItems", "list"): {"items": []},
    ("search", "list"): {"items": []},
    ("reports", "query"): {"columnHeaders": [], "rows": []},
}


class FakeService:
    def __init__(self, root_desc: dict[str, Any] | None = None) -> None:
        self._rootDesc = root_desc or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.requests: dict[tuple[str, str], FakeRequest] = {}

    def __getattr__(self, resource: str) -> Callable[[], FakeResource]:
        if resource.startswith("_"):
            raise AttributeError(resource)

        def resource_factory() -> FakeResource:
            return FakeResource(self, resource)

        return resource_factory

    def request_for(self, resource: str, method: str) -> FakeRequest:
        request = self.requests.get((resource, method))
        if request is not None:
            return request
        if (resource, method) == ("videos", "insert"):
            return FakeRequest(next_chunks=[(None, {"id": "default-video"})])
        return FakeRequest(payload=DEFAULT_PAYLOADS.get((resource, method), {}))


class FakeFactory:
    def __init__(self, service: FakeService) -> None:
        self.service = service
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, api: str, version: str, **kwargs: Any) -> FakeService:
        self.calls.append((api, version, kwargs))
        return self.service


class FakeProgress:
    def __init__(self, value: float = 0.5) -> None:
        self.value = value

    def progress(self) -> float:
        return self.value


@dataclass
class FakeMediaUpload:
    filename: str
    chunksize: int | None = None
    resumable: bool | None = None
    mimetype: str | None = None


def _api(service: FakeService, **kwargs: Any) -> tuple[GoogleYouTubeApi, FakeFactory]:
    factory = FakeFactory(service)
    return GoogleYouTubeApi(object(), service_factory=factory, **kwargs), factory


def _video(tmp_path: Path) -> Path:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"not really a video")
    return path


def test_upload_retries_503_then_succeeds(tmp_path: Path) -> None:
    service = FakeService()
    request = FakeRequest(
        next_chunks=[
            http_error(503),
            http_error(503),
            (FakeProgress(0.5), {"id": "abc"}),
        ]
    )
    service.requests[("videos", "insert")] = request
    sleeps: list[float] = []
    api, _ = _api(service, sleep=sleeps.append)
    progress: list[float] = []

    result = api.insert_video(
        {"snippet": {"title": "x"}},
        _video(tmp_path),
        on_progress=progress.append,
    )

    assert result == {"id": "abc"}
    assert request.next_chunk_calls == 3
    assert sleeps == [1, 2]
    assert progress == [0.5]
    resource, method, kwargs = service.calls[0]
    assert (resource, method) == ("videos", "insert")
    assert kwargs["part"] == "snippet,status"


def test_upload_retries_connection_errors(tmp_path: Path) -> None:
    service = FakeService()
    request = FakeRequest(
        next_chunks=[ConnectionError("reset"), (None, {"id": "abc"})]
    )
    service.requests[("videos", "insert")] = request
    sleeps: list[float] = []
    api, _ = _api(service, sleep=sleeps.append)

    assert api.insert_video({}, _video(tmp_path)) == {"id": "abc"}
    assert sleeps == [1]


def test_upload_gives_up_after_five_retries(tmp_path: Path) -> None:
    service = FakeService()
    request = FakeRequest(next_chunks=[http_error(503) for _ in range(MAX_UPLOAD_RETRIES + 1)])
    service.requests[("videos", "insert")] = request
    sleeps: list[float] = []
    api, _ = _api(service, sleep=sleeps.append)

    with pytest.raises(SocialError):
        api.insert_video({}, _video(tmp_path))
    assert sleeps == [1, 2, 4, 8, 16]


def test_insert_video_uses_resumable_8_mib_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeMediaUpload] = []

    def fake_media(filename: str, **kwargs: Any) -> FakeMediaUpload:
        upload = FakeMediaUpload(filename=filename, **kwargs)
        created.append(upload)
        return upload

    monkeypatch.setattr(api_module, "MediaFileUpload", fake_media)
    service = FakeService()
    api, _ = _api(service)

    api.insert_video({}, Path("video.mp4"))

    assert created == [
        FakeMediaUpload(
            filename="video.mp4",
            chunksize=UPLOAD_CHUNK_SIZE,
            resumable=True,
        )
    ]


@pytest.mark.parametrize(
    ("error", "operation", "expected"),
    [
        (http_error(403, reason="quotaExceeded"), None, QuotaExceeded),
        (http_error(403, reason="uploadLimitExceeded"), None, UploadLimitExceeded),
        (http_error(401, message="invalid credentials"), None, AuthExpired),
        (http_error(403, reason="authError", message="token expired"), None, AuthExpired),
        (
            http_error(403, message="Custom thumbnails are not allowed"),
            None,
            ThumbnailNotAllowed,
        ),
        (http_error(403, reason="forbidden"), "thumbnails.set", ThumbnailNotAllowed),
        (http_error(400, message="Bad request"), None, SocialError),
    ],
)
def test_http_errors_map_to_social_errors(
    error: HttpError, operation: str | None, expected: type[SocialError]
) -> None:
    mapped = map_http_error(error, operation=operation)

    assert type(mapped) is expected
    if expected is ThumbnailNotAllowed:
        assert "youtube.com/verify" in mapped.next_step


def test_set_thumbnail_maps_a_403_to_thumbnail_not_allowed(tmp_path: Path) -> None:
    service = FakeService()
    service.requests[("thumbnails", "set")] = FakeRequest(
        error=http_error(403, reason="forbidden", message="not allowed")
    )
    api, _ = _api(service)
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"png")

    with pytest.raises(ThumbnailNotAllowed):
        api.set_thumbnail("vid", thumbnail)

    assert service.calls[0][:2] == ("thumbnails", "set")
    assert service.calls[0][2]["videoId"] == "vid"


def test_get_video_joins_the_requested_parts() -> None:
    service = FakeService()
    service.requests[("videos", "list")] = FakeRequest(payload={"items": [{"id": "vid"}]})
    api, _ = _api(service)

    response = api.get_video("vid", ["snippet", "status"])

    assert response == {"items": [{"id": "vid"}]}
    assert service.calls[0] == ("videos", "list", {"part": "snippet,status", "id": "vid"})


def test_analytics_report_uses_a_video_filter() -> None:
    service = FakeService()
    api, _ = _api(service)

    api.analytics_report("vid", "2026-01-01", "2026-01-28", ["views", "likes"])

    assert service.calls[0][0:2] == ("reports", "query")
    assert service.calls[0][2] == {
        "ids": "channel==MINE",
        "startDate": "2026-01-01",
        "endDate": "2026-01-28",
        "metrics": "views,likes",
        "filters": "video==vid",
    }


def test_my_channel_id_reads_the_first_channel() -> None:
    api, _ = _api(FakeService())
    assert api.my_channel_id() == "UC123"


def test_synthetic_media_detection_is_true_and_cached() -> None:
    service = FakeService(
        root_desc={
            "schemas": {
                "VideoStatus": {
                    "properties": {"containsSyntheticMedia": {"type": "boolean"}}
                }
            }
        }
    )
    api, factory = _api(service)

    assert api.supports_synthetic_media() is True
    assert api.supports_synthetic_media() is True
    assert len(factory.calls) == 2


def test_synthetic_media_detection_is_false_when_the_field_is_absent() -> None:
    service = FakeService(root_desc={"schemas": {"VideoStatus": {"properties": {}}}})
    api, _ = _api(service)

    assert api.supports_synthetic_media() is False


def test_the_fake_records_calls_and_injects_errors() -> None:
    fake = FakeYouTubeApi()
    fake.errors["list_playlists"] = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fake.list_playlists()
    assert fake.call_names == ["list_playlists"]


def test_the_fake_can_simulate_the_unaudited_private_lock() -> None:
    fake = FakeYouTubeApi()
    fake.force_private = True

    fake.insert_video({"status": {"privacyStatus": "public"}}, Path("video.mp4"))

    assert fake.videos["fake-video-1"]["status"]["privacyStatus"] == "private"
