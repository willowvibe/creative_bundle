"""Tests for the YouTube upload and update operations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes.youtube_api import FakeYouTubeApi

from social_engine.core.errors import AlreadyPublished, SocialError, ThumbnailNotAllowed
from social_engine.core.publish_log import PublishLog
from social_engine.platforms.youtube.ops import update, upload


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "final.mp4").write_bytes(b"not really a video")
    (tmp_path / "thumbnail.jpg").write_bytes(b"not really an image")
    return tmp_path


def _text(
    *,
    title: str = "A title",
    privacy: str = "private",
    publish_at: str | None = None,
    thumbnail: bool = True,
    synthetic_media: bool = True,
    category: int | None = 24,
) -> str:
    lines = [
        "video: final.mp4",
        f"title: {json.dumps(title)}",
        "description: A description",
        "language: te",
        f"synthetic_media: {'true' if synthetic_media else 'false'}",
    ]
    if thumbnail:
        lines.append("thumbnail: thumbnail.jpg")
    lines += [
        "youtube:",
        "  account: mantra-shakti",
        "  kind: long",
        f"  privacy: {privacy}",
    ]
    if category is not None:
        lines.append(f"  category: {category}")
    if publish_at is not None:
        lines.append(f"  publish_at: {publish_at}")
    return "\n".join(lines) + "\n"


def _write(folder: Path, **kwargs: object) -> Path:
    path = folder / "publish.yaml"
    path.write_text(_text(**kwargs), encoding="utf-8")  # type: ignore[arg-type]
    return path


def test_upload_happy_path_writes_the_video_then_the_log(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)

    result = upload(path, api, confirm=True)

    assert result.action == "upload"
    assert result.video_id == "fake-video-1"
    assert result.changed == ["video", "thumbnail"]
    assert result.warnings == []
    assert api.call_names == ["insert_video", "get_video", "set_thumbnail"]

    body = api.calls[0].kwargs["body"]
    assert body["snippet"] == {
        "title": "A title",
        "description": "A description",
        "tags": [],
        "defaultLanguage": "te",
        "defaultAudioLanguage": "te",
        "categoryId": "24",
    }
    assert body["status"] == {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }

    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.account == "mantra-shakti"
    assert entry.id == "fake-video-1"
    assert entry.url == "https://youtu.be/fake-video-1"
    assert entry.via == "api"
    assert entry.history[0]["action"] == "upload"
    assert entry.history[0]["changed"] == ["video", "thumbnail"]


def test_upload_refuses_without_confirmation(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)

    with pytest.raises(SocialError) as caught:
        upload(path, api)

    assert caught.value.code == "confirmation_required"
    assert "--yes" in caught.value.next_step
    assert api.call_names == []


def test_upload_refuses_a_manifest_already_in_the_log(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)
    log = PublishLog.load(path)
    log.record(
        "youtube",
        account="mantra-shakti",
        id="existing",
        url="https://youtu.be/existing",
        via="api",
        action="upload",
        changed=["video"],
        warnings=[],
    )
    log.save()

    with pytest.raises(AlreadyPublished, match="existing"):
        upload(path, api, confirm=True)
    assert api.call_names == []


def test_upload_aborts_on_validation_errors(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder, title="")

    with pytest.raises(SocialError) as caught:
        upload(path, api, confirm=True)

    assert caught.value.code == "validation_failed"
    assert "title" in str(caught.value)
    assert api.call_names == []


def test_upload_warns_when_youtube_locks_the_video_private(folder: Path) -> None:
    api = FakeYouTubeApi()
    api.force_private = True
    path = _write(folder, privacy="unlisted")

    result = upload(path, api, confirm=True)

    assert any("private_locked" in warning for warning in result.warnings)
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.history[0]["warnings"] == result.warnings


def test_upload_turns_a_thumbnail_rejection_into_a_warning(folder: Path) -> None:
    api = FakeYouTubeApi()
    api.errors["set_thumbnail"] = ThumbnailNotAllowed("not allowed")
    path = _write(folder)

    result = upload(path, api, confirm=True)

    assert "thumbnail" not in result.changed
    joined = " ".join(result.warnings)
    assert "thumbnail_not_allowed" in joined
    assert "youtube.com/verify" in joined
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.history[0]["warnings"] == result.warnings


def test_upload_warns_when_the_disclosure_field_is_not_supported(folder: Path) -> None:
    api = FakeYouTubeApi()
    api.synthetic_supported = False
    path = _write(folder)

    result = upload(path, api, confirm=True)

    assert any("Studio" in warning for warning in result.warnings)
    body = api.calls[0].kwargs["body"]
    assert "containsSyntheticMedia" not in body["status"]


def test_update_is_a_no_op_when_nothing_differs(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)
    upload(path, api, confirm=True)
    api.calls.clear()

    result = update(path, api)

    assert result.action == "update"
    assert result.video_id == "fake-video-1"
    assert result.changed == []
    assert api.call_names == ["get_video"]
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert [item["action"] for item in entry.history] == ["upload"]


def test_update_sends_only_the_snippet_when_the_title_changes(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)
    upload(path, api, confirm=True)
    api.calls.clear()
    path = _write(folder, title="New title")

    result = update(path, api)

    assert result.changed == ["title"]
    assert api.call_names == ["get_video", "update_video"]
    body = api.calls[1].kwargs["body"]
    parts = api.calls[1].kwargs["parts"]
    assert parts == ["snippet"]
    assert body["id"] == "fake-video-1"
    assert body["snippet"]["title"] == "New title"
    assert "status" not in body
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert [item["action"] for item in entry.history] == ["upload", "update"]
    assert entry.history[1]["changed"] == ["title"]


def test_update_requires_confirmation_for_a_privacy_change(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)
    upload(path, api, confirm=True)
    api.calls.clear()
    path = _write(folder, privacy="public")

    with pytest.raises(SocialError) as caught:
        update(path, api)
    assert caught.value.code == "confirmation_required"
    assert api.call_names == ["get_video"]

    result = update(path, api, confirm=True)
    assert result.changed == ["privacy"]
    assert api.call_names == ["get_video", "get_video", "update_video"]
    body = api.calls[-1].kwargs["body"]
    parts = api.calls[-1].kwargs["parts"]
    assert parts == ["status"]
    assert body["status"]["privacyStatus"] == "public"


def test_update_requires_confirmation_for_a_publish_at_change(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)
    upload(path, api, confirm=True)
    api.calls.clear()
    path = _write(folder, publish_at="2099-01-01T00:00:00Z")

    with pytest.raises(SocialError) as caught:
        update(path, api)

    assert caught.value.code == "confirmation_required"
    result = update(path, api, confirm=True)
    assert result.changed == ["publish_at"]


def test_update_requires_an_existing_publish_log_entry(folder: Path) -> None:
    api = FakeYouTubeApi()
    path = _write(folder)

    with pytest.raises(SocialError) as caught:
        update(path, api, confirm=True)

    assert caught.value.code == "not_published"
    assert api.call_names == []
