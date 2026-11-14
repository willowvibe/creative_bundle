"""Tests for the MCP tool surface, confirm guard and allowed-root checks."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from social_engine import mcp_server
from social_engine.core.browser import BrowserResult
from social_engine.platforms.youtube.ops import OpResult, VideoStats, VideoSummary


@pytest.fixture(autouse=True)
def allowed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("SOCIAL_ENGINE_ROOTS", str(allowed))
    monkeypatch.setenv("SOCIAL_ENGINE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("social_engine.core.accounts._port_in_use", lambda _port: False)
    mcp_server._pending.clear()
    return allowed


def _manifest(
    folder: Path,
    *,
    title: str = "A title",
    account: str = "mantra-shakti",
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "final.mp4").write_bytes(b"not really a video")
    (folder / "thumbnail.jpg").write_bytes(b"not really an image")
    text = "\n".join(
        [
            "video: final.mp4",
            f"title: {json.dumps(title)}",
            "description: A description",
            "language: te",
            "thumbnail: thumbnail.jpg",
            "youtube:",
            f"  account: {account}",
            "  kind: long",
            "  privacy: private",
            "  category: 24",
        ]
    )
    path = folder / "publish.yaml"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def test_the_tool_list_contains_the_five_social_tools() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())

    assert {tool.name for tool in tools} >= {
        "social_validate",
        "social_publish",
        "social_update",
        "social_list",
        "social_stats",
    }


def test_validate_returns_issues_and_a_quota_estimate(allowed_root: Path) -> None:
    manifest = _manifest(allowed_root / "episode")

    result = mcp_server.social_validate(str(manifest))

    assert result["ok"] is True
    assert result["issues"] == []
    assert result["quota_estimate"] > 0
    assert result["manifest"] == str(manifest)


def test_validate_reports_an_error_for_a_blank_title(allowed_root: Path) -> None:
    manifest = _manifest(allowed_root / "episode", title="")

    result = mcp_server.social_validate(str(manifest))

    assert result["ok"] is False
    assert result["issues"][0]["field"] == "title"


def test_publish_refuses_the_api_lane_without_a_literal_true(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.upload",
        lambda *args, **kwargs: pytest.fail("must not upload without confirm"),
    )

    assert mcp_server.social_publish(str(manifest), via="api")["status"] == "refused"
    assert (
        mcp_server.social_publish(str(manifest), via="api", confirm=1)["status"]
        == "refused"
    )


def test_publish_api_lane_with_confirm_true_calls_upload(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("social_engine.mcp_server._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.upload",
        lambda path, api, *, confirm: calls.append((path, confirm))
        or OpResult(action="upload", video_id="v1", changed=["video"], warnings=[]),
    )

    result = mcp_server.social_publish(str(manifest), via="api", confirm=True)

    assert result["action"] == "upload"
    assert result["video_id"] == "v1"
    assert calls == [(manifest, True)]


def test_update_requires_confirm_true(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.update",
        lambda *args, **kwargs: pytest.fail("must not update without confirm"),
    )

    assert mcp_server.social_update(str(manifest))["status"] == "refused"
    assert mcp_server.social_update(str(manifest), confirm=1)["status"] == "refused"


def test_update_with_confirm_true_calls_update(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    monkeypatch.setattr("social_engine.mcp_server._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.update",
        lambda path, api, *, confirm: OpResult(
            action="update", video_id="v1", changed=["title"], warnings=[]
        ),
    )

    result = mcp_server.social_update(str(manifest), confirm=True)

    assert result["action"] == "update"
    assert result["changed"] == ["title"]


def test_the_browser_lane_stages_then_publishes_with_the_returned_id(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "social_engine.mcp_server.browser_ops.browser_upload",
        lambda path: calls.append(("upload", path))
        or BrowserResult(
            status="ready_to_confirm",
            summary={"title": "A title"},
            id="v1",
            message="review",
        ),
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.browser_ops.browser_confirm",
        lambda path, video_id, expect: calls.append(("confirm", path, video_id, expect))
        or BrowserResult(status="done", summary={"button": "Publish"}, id="v1"),
    )

    staged = mcp_server.social_publish(str(manifest), via="browser")
    assert staged["status"] == "ready_to_confirm"
    assert staged["id"] == "v1"
    assert mcp_server._pending[str(manifest)] == "v1"

    done = mcp_server.social_publish(
        str(manifest), via="browser", confirm=True, video_id="v1"
    )
    assert done["status"] == "done"
    assert calls == [
        ("upload", str(manifest)),
        ("confirm", str(manifest), "v1", "publish"),
    ]
    assert str(manifest) not in mcp_server._pending


def test_a_second_browser_call_can_use_the_pending_id(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    monkeypatch.setattr(
        "social_engine.mcp_server.browser_ops.browser_upload",
        lambda path: BrowserResult(
            status="ready_to_confirm", summary={}, id="v1", message="review"
        ),
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.browser_ops.browser_confirm",
        lambda path, video_id, expect: BrowserResult(
            status="done", summary={}, id=video_id
        ),
    )

    mcp_server.social_publish(str(manifest), via="browser")
    done = mcp_server.social_publish(str(manifest), via="browser", confirm=True)

    assert done["status"] == "done"
    assert done["id"] == "v1"


def test_browser_confirm_without_a_staged_upload_is_refused(allowed_root: Path) -> None:
    manifest = _manifest(allowed_root / "episode")

    result = mcp_server.social_publish(str(manifest), via="browser", confirm=True)

    assert result["status"] == "refused"
    assert "no staged browser upload" in result["message"]


def test_a_manifest_outside_the_roots_is_refused(tmp_path: Path) -> None:
    outside = _manifest(tmp_path / "outside")

    result = mcp_server.social_validate(str(outside))

    assert result["status"] == "error"
    assert result["code"] == "path_not_allowed"


def test_a_symlink_escape_is_refused(allowed_root: Path, tmp_path: Path) -> None:
    outside = _manifest(tmp_path / "outside")
    escape = allowed_root / "escape.yaml"
    os.symlink(outside, escape)

    result = mcp_server.social_validate(str(escape))

    assert result["status"] == "error"
    assert result["code"] == "path_not_allowed"


def test_list_returns_structured_rows(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "social_engine.mcp_server.load_credentials", lambda account: object()
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.GoogleYouTubeApi", lambda credentials: object()
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.list_videos",
        lambda api, limit: [
            VideoSummary(
                id="v1",
                title="A title",
                privacy="private",
                published_at="2026-01-01T00:00:00Z",
                views=42,
            )
        ],
    )

    result = mcp_server.social_list("youtube", "mantra-shakti", 5)

    assert result["videos"][0]["id"] == "v1"
    assert result["videos"][0]["views"] == 42


def test_stats_returns_structured_metrics(
    allowed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(allowed_root / "episode")
    log = mcp_server.PublishLog.load(manifest)
    log.record(
        "youtube",
        account="mantra-shakti",
        id="v1",
        url="https://youtu.be/v1",
        via="api",
        action="upload",
        changed=["video"],
        warnings=[],
    )
    log.save()
    monkeypatch.setattr(
        "social_engine.mcp_server.load_credentials", lambda account: object()
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.GoogleYouTubeApi", lambda credentials: object()
    )
    monkeypatch.setattr(
        "social_engine.mcp_server.youtube_ops.stats",
        lambda api, video_id, days: VideoStats(
            video_id=video_id,
            days=days,
            views=1000,
            estimated_minutes_watched=500.5,
            average_view_duration=30.2,
            average_view_percentage=45.6,
            likes=80,
            subscribers_gained=3,
            lifetime_views=5000,
            lifetime_likes=300,
            lifetime_comments=20,
        ),
    )

    result = mcp_server.social_stats(str(manifest), 7)

    assert result["video_id"] == "v1"
    assert result["days"] == 7
    assert result["views"] == 1000
    assert result["lifetime_views"] == 5000
