"""Tests for captions, localizations, playlists, listing and stats."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes.youtube_api import FakeYouTubeApi

from social_engine.core.publish_log import PublishLog
from social_engine.platforms.youtube.ops import (
    VideoStats,
    list_videos,
    stats,
    sync_captions,
    sync_localizations,
    sync_playlists,
    upload,
)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "final.mp4").write_bytes(b"not really a video")
    return tmp_path


def _text(
    *,
    captions: dict[str, str] | None = None,
    localizations: dict[str, dict[str, str]] | None = None,
    playlists: list[str] | None = None,
) -> str:
    lines = [
        "video: final.mp4",
        'title: "A title"',
        "description: A description",
        "language: te",
        "synthetic_media: true",
        "youtube:",
        "  account: mantra-shakti",
        "  kind: long",
        "  privacy: private",
    ]
    if captions:
        lines.append("captions:")
        for language, path in captions.items():
            lines.append(f"  {language}: {path}")
    if localizations:
        lines.append("  localizations:")
        for language, values in localizations.items():
            lines.append(f"    {language}: {json.dumps(values)}")
    if playlists:
        lines.append("  playlists:")
        for playlist in playlists:
            lines.append(f"    - {json.dumps(playlist)}")
    return "\n".join(lines) + "\n"


def _write(folder: Path, **kwargs: object) -> Path:
    path = folder / "publish.yaml"
    path.write_text(_text(**kwargs), encoding="utf-8")  # type: ignore[arg-type]
    return path


def _caption(folder: Path, relative: str, text: str) -> None:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _uploaded(folder: Path, **kwargs: object) -> tuple[Path, FakeYouTubeApi]:
    api = FakeYouTubeApi()
    path = _write(folder, **kwargs)
    upload(path, api, confirm=True)
    api.calls.clear()
    return path, api


def test_sync_captions_inserts_missing_tracks_and_records_hashes(folder: Path) -> None:
    _caption(folder, "subs/te.srt", "te v1")
    _caption(folder, "subs/en.srt", "en v1")
    path, api = _uploaded(
        folder,
        captions={"te": "subs/te.srt", "en": "subs/en.srt"},
    )

    result = sync_captions(path, api)

    assert result.action == "captions"
    assert result.video_id == "fake-video-1"
    assert result.changed == ["te", "en"]
    assert api.call_names == ["list_captions", "insert_caption", "insert_caption"]
    inserted = [call for call in api.calls if call.name == "insert_caption"]
    assert [call.kwargs["language"] for call in inserted] == ["te", "en"]

    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.caption_hashes == {
        "te": hashlib.sha256(b"te v1").hexdigest(),
        "en": hashlib.sha256(b"en v1").hexdigest(),
    }
    assert entry.history[-1]["action"] == "captions"


def test_sync_captions_skips_unchanged_and_updates_a_changed_srt(folder: Path) -> None:
    _caption(folder, "subs/te.srt", "te v1")
    _caption(folder, "subs/en.srt", "en v1")
    path, api = _uploaded(
        folder,
        captions={"te": "subs/te.srt", "en": "subs/en.srt"},
    )
    sync_captions(path, api)

    api.calls.clear()
    skipped = sync_captions(path, api)
    assert skipped.changed == []
    assert api.call_names == ["list_captions"]
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert [item["action"] for item in entry.history] == ["upload", "captions"]

    _caption(folder, "subs/en.srt", "en v2")
    api.calls.clear()
    updated = sync_captions(path, api)
    assert updated.changed == ["en"]
    assert api.call_names == ["list_captions", "update_caption"]
    assert api.calls[1].kwargs["caption_id"] == "caption-2"
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.caption_hashes["en"] == hashlib.sha256(b"en v2").hexdigest()


def test_sync_captions_warns_about_extra_tracks_and_never_deletes(folder: Path) -> None:
    _caption(folder, "subs/te.srt", "te v1")
    path, api = _uploaded(folder, captions={"te": "subs/te.srt"})
    sync_captions(path, api)

    api.insert_caption(
        "fake-video-1",
        "hi",
        "hi captions",
        folder / "subs" / "hi.srt",
    )
    api.calls.clear()
    result = sync_captions(path, api)

    assert result.changed == []
    assert any("'hi'" in warning for warning in result.warnings)
    assert api.call_names == ["list_captions"]


def test_sync_localizations_updates_then_skips(folder: Path) -> None:
    localizations = {
        "en": {"title": "English", "description": "English description"},
        "te": {"title": "Telugu"},
    }
    path, api = _uploaded(folder, localizations=localizations)

    first = sync_localizations(path, api)
    assert first.changed == ["localizations"]
    assert api.call_names == ["get_video", "update_video"]
    body = api.calls[1].kwargs["body"]
    assert body["snippet"] == {"localizations": localizations}

    api.calls.clear()
    second = sync_localizations(path, api)
    assert second.changed == []
    assert api.call_names == ["get_video"]
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert [item["action"] for item in entry.history] == ["upload", "localizations"]


def test_sync_localizations_sets_a_missing_default_language(folder: Path) -> None:
    path, api = _uploaded(folder, localizations={"en": {"title": "English"}})
    sync_localizations(path, api)
    api.videos["fake-video-1"]["snippet"].pop("defaultLanguage")
    api.calls.clear()

    result = sync_localizations(path, api)

    assert result.changed == ["default_language"]
    body = api.calls[1].kwargs["body"]
    assert body["snippet"] == {"defaultLanguage": "te"}


def test_sync_playlists_creates_private_playlists_and_adds_the_video(folder: Path) -> None:
    path, api = _uploaded(folder, playlists=["Mantra Rahasyam", "Second"])

    result = sync_playlists(path, api)

    assert result.changed == ["Mantra Rahasyam", "Second"]
    assert api.call_names == [
        "list_playlists",
        "insert_playlist",
        "list_playlist_items",
        "insert_playlist_item",
        "insert_playlist",
        "list_playlist_items",
        "insert_playlist_item",
    ]
    created = [call for call in api.calls if call.name == "insert_playlist"]
    assert [call.kwargs["privacy"] for call in created] == ["private", "private"]
    assert len(api.playlists) == 2
    assert api.playlist_items["playlist-1"][0]["snippet"]["resourceId"]["videoId"] == "fake-video-1"
    entry = PublishLog.load(path).entry("youtube")
    assert entry is not None
    assert entry.history[-1]["action"] == "playlists"


def test_sync_playlists_skips_present_items_and_adds_missing_ones(folder: Path) -> None:
    path, api = _uploaded(folder, playlists=["Mantra Rahasyam"])
    sync_playlists(path, api)

    api.calls.clear()
    assert sync_playlists(path, api).changed == []
    assert api.call_names == ["list_playlists", "list_playlist_items"]

    api.playlist_items["playlist-1"].clear()
    api.calls.clear()
    result = sync_playlists(path, api)
    assert result.changed == ["Mantra Rahasyam"]
    assert api.call_names == ["list_playlists", "list_playlist_items", "insert_playlist_item"]
    assert "insert_playlist" not in api.call_names


def test_list_videos_is_newest_first_with_privacy_and_views() -> None:
    api = FakeYouTubeApi()
    api.insert_video(
        {
            "snippet": {"title": "Older", "publishedAt": "2026-01-01T00:00:00Z"},
            "status": {"privacyStatus": "private"},
        },
        Path("older.mp4"),
    )
    api.videos["fake-video-1"]["statistics"] = {"viewCount": "10"}
    api.insert_video(
        {
            "snippet": {"title": "Newer", "publishedAt": "2026-02-01T00:00:00Z"},
            "status": {"privacyStatus": "public"},
        },
        Path("newer.mp4"),
    )
    api.videos["fake-video-2"]["statistics"] = {"viewCount": "25"}
    api.calls.clear()

    result = list_videos(api, 10)

    assert [video.id for video in result] == ["fake-video-2", "fake-video-1"]
    assert result[0].title == "Newer"
    assert result[0].privacy == "public"
    assert result[0].published_at == "2026-02-01T00:00:00Z"
    assert result[0].views == 25
    assert result[1].views == 10
    assert api.calls[0].kwargs["limit"] == 10


def test_stats_reads_the_window_and_lifetime_counts() -> None:
    api = FakeYouTubeApi()
    metrics = [
        "views",
        "estimatedMinutesWatched",
        "averageViewDuration",
        "averageViewPercentage",
        "likes",
        "subscribersGained",
    ]
    api.analytics_result = {
        "columnHeaders": [{"name": name} for name in metrics],
        "rows": [[1000, 500.5, 30.2, 45.6, 80, 3]],
    }
    api.videos["vid"] = {
        "id": "vid",
        "snippet": {},
        "status": {},
        "statistics": {"viewCount": "5000", "likeCount": "300", "commentCount": "20"},
    }

    result = stats(api, "vid", 28)

    assert result.video_id == "vid"
    assert result.days == 28
    assert result.views == 1000
    assert result.estimated_minutes_watched == 500.5
    assert result.average_view_duration == 30.2
    assert result.average_view_percentage == 45.6
    assert result.likes == 80
    assert result.subscribers_gained == 3
    assert result.lifetime_views == 5000
    assert result.lifetime_likes == 300
    assert result.lifetime_comments == 20

    today = datetime.now(UTC).date()
    analytics_call = api.calls[0]
    assert analytics_call.name == "analytics_report"
    assert analytics_call.kwargs["metrics"] == metrics
    assert analytics_call.kwargs["start"] == (today - timedelta(days=27)).isoformat()
    assert analytics_call.kwargs["end"] == today.isoformat()
    assert api.calls[1].name == "get_video"
    assert api.calls[1].kwargs["parts"] == ["statistics"]
    assert "ctr" not in {field.name for field in fields(VideoStats)}
    assert "click" not in " ".join(field.name for field in fields(VideoStats))


def test_stats_defaults_to_zero_when_the_report_has_no_rows() -> None:
    api = FakeYouTubeApi()
    api.analytics_result = {
        "columnHeaders": [{"name": "views"}],
        "rows": [],
    }
    api.videos["vid"] = {"id": "vid", "snippet": {}, "status": {}, "statistics": {}}

    result = stats(api, "vid")

    assert result.days == 28
    assert result.views == 0
    assert result.lifetime_views == 0
