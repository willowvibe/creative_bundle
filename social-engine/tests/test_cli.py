"""Tests for the social CLI: lanes, confirmation gates and exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from social_engine.cli import app
from social_engine.core.accounts import Account, save_account
from social_engine.core.browser import BrowserResult
from social_engine.core.errors import BrowserNeedsUser, SocialError
from social_engine.core.publish_log import PublishLog
from social_engine.platforms.youtube.ops import OpResult, VideoStats, VideoSummary

runner = CliRunner()


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SOCIAL_ENGINE_HOME", str(home))
    monkeypatch.setattr("social_engine.core.accounts._port_in_use", lambda _port: False)
    return home


def _manifest(
    folder: Path,
    *,
    title: str = "A title",
    privacy: str = "private",
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
            "  account: mantra-shakti",
            "  kind: long",
            f"  privacy: {privacy}",
            "  category: 24",
        ]
    )
    path = folder / "publish.yaml"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _audited_account(cdp_port: int = 9333) -> Account:
    account = Account(
        platform="youtube",
        name="mantra-shakti",
        cdp_port=cdp_port,
        api_audited=True,
    )
    save_account(account)
    return account


def _ready(video_id: str = "v1") -> BrowserResult:
    return BrowserResult(
        status="ready_to_confirm",
        summary={"title": "A title"},
        id=video_id,
        message="review the Publish step",
    )


def _done(video_id: str = "v1") -> BrowserResult:
    return BrowserResult(
        status="done",
        summary={"button": "Publish"},
        id=video_id,
        message="published",
    )


def test_publish_defaults_to_browser_and_prompts_without_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    calls: list[object] = []
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_upload",
        lambda path: calls.append(("upload", path)) or _ready(),
    )
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_confirm",
        lambda *args: calls.append(("confirm", args)) or _done(),
    )

    result = runner.invoke(app, ["publish", str(manifest)], input="n\n")

    assert result.exit_code == 0
    assert calls == [("upload", manifest)]
    assert "status: ready_to_confirm" in result.output
    assert "Publish now?" in result.output
    assert "left at the confirmation step" in result.output


def test_publish_with_yes_confirms_the_browser_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_upload",
        lambda path: calls.append(("upload", path)) or _ready(),
    )
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_confirm",
        lambda path, video_id, expect: calls.append(("confirm", path, video_id, expect))
        or _done(),
    )

    result = runner.invoke(app, ["publish", str(manifest), "--yes"])

    assert result.exit_code == 0
    assert calls == [
        ("upload", manifest),
        ("confirm", manifest, "v1", "publish"),
    ]
    assert "status: done" in result.output


def test_publish_defaults_to_api_when_the_account_is_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    _audited_account()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("social_engine.cli._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.upload",
        lambda path, api, *, confirm: calls.append((path, confirm))
        or OpResult(action="upload", video_id="v1", changed=["video"], warnings=[]),
    )
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_upload",
        lambda path: pytest.fail("browser lane must not run"),
    )

    result = runner.invoke(app, ["publish", str(manifest), "--yes"])

    assert result.exit_code == 0
    assert calls == [(manifest, True)]
    assert "action: upload" in result.output
    assert "video: v1" in result.output


def test_api_publish_without_yes_prints_a_preview_and_does_not_act(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    _audited_account()
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.upload",
        lambda *args, **kwargs: pytest.fail("must not upload without --yes"),
    )

    result = runner.invoke(app, ["publish", str(manifest)])

    assert result.exit_code == 0
    assert "via: api" in result.output
    assert "not published; rerun with --yes" in result.output
    assert "quota estimate:" in result.output


def test_publish_can_force_the_api_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    calls: list[str] = []
    monkeypatch.setattr("social_engine.cli._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.upload",
        lambda path, api, *, confirm: calls.append("api")
        or OpResult(action="upload", video_id="v1"),
    )

    result = runner.invoke(app, ["publish", str(manifest), "--via", "api", "--yes"])

    assert result.exit_code == 0
    assert calls == ["api"]


def test_browser_needs_user_exits_3_with_the_screenshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")

    def needs_user(_path: Path) -> BrowserResult:
        raise BrowserNeedsUser("sign in to Google", screenshot="/logs/shot.png")

    monkeypatch.setattr("social_engine.cli.browser_ops.browser_upload", needs_user)

    result = runner.invoke(app, ["publish", str(manifest)])

    assert result.exit_code == 3
    assert "browser_needs_user: sign in to Google" in result.output
    assert "screenshot: /logs/shot.png" in result.output
    assert "next:" in result.output


def test_a_social_error_exits_2_with_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")

    def broken(_path: Path) -> BrowserResult:
        raise SocialError("the page broke", next_step="close the page and retry")

    monkeypatch.setattr("social_engine.cli.browser_ops.browser_upload", broken)

    result = runner.invoke(app, ["publish", str(manifest)])

    assert result.exit_code == 2
    assert "social_error: the page broke" in result.output
    assert "next: close the page and retry" in result.output


def test_validate_exits_1_on_errors(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "episode", title="")

    result = runner.invoke(app, ["validate", str(manifest)])

    assert result.exit_code == 1
    assert "error: title: title is required" in result.output


def test_validate_prints_ok_and_the_quota_estimate(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "episode")

    result = runner.invoke(app, ["validate", str(manifest)])

    assert result.exit_code == 0
    assert "ok" in result.output
    assert "quota estimate:" in result.output


def test_update_without_yes_prints_a_preview_and_does_not_call_ops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.update",
        lambda *args, **kwargs: pytest.fail("must not update without --yes"),
    )

    result = runner.invoke(app, ["update", str(manifest)])

    assert result.exit_code == 0
    assert "would update" in result.output
    assert "rerun with --yes to apply" in result.output


def test_update_with_yes_calls_ops_with_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("social_engine.cli._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.update",
        lambda path, api, *, confirm: calls.append((path, confirm))
        or OpResult(action="update", video_id="v1", changed=["title"]),
    )

    result = runner.invoke(app, ["update", str(manifest), "--yes"])

    assert result.exit_code == 0
    assert calls == [(manifest, True)]
    assert "changed: title" in result.output


def test_yt_dubs_prompts_without_yes_and_confirms_with_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_dubs",
        lambda path: calls.append(("dubs", path)) or _ready(),
    )
    monkeypatch.setattr(
        "social_engine.cli.browser_ops.browser_confirm",
        lambda path, video_id, expect: calls.append(("confirm", path, video_id, expect))
        or _done(),
    )

    result = runner.invoke(app, ["yt", "dubs", str(manifest)], input="n\n")
    assert result.exit_code == 0
    assert calls == [("dubs", manifest)]

    calls.clear()
    result = runner.invoke(app, ["yt", "dubs", str(manifest), "--yes"])
    assert result.exit_code == 0
    assert calls == [
        ("dubs", manifest),
        ("confirm", manifest, "v1", "dubs"),
    ]


def test_login_needs_user_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def needs_user(_platform: str, _command: str, _payload: object) -> BrowserResult:
        raise BrowserNeedsUser("sign in", screenshot="/logs/login.png")

    monkeypatch.setattr("social_engine.cli.browser_call", needs_user)

    result = runner.invoke(app, ["login", "youtube", "mantra-shakti"])

    assert result.exit_code == 3
    assert "screenshot: /logs/login.png" in result.output


def test_auth_calls_authorize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "social_engine.cli.authorize",
        lambda account: calls.append(account),
    )

    result = runner.invoke(app, ["auth", "youtube", "mantra-shakti"])

    assert result.exit_code == 0
    assert calls == ["mantra-shakti"]
    assert "authorized mantra-shakti" in result.output


def test_init_drafts_publish_yaml(tmp_path: Path) -> None:
    folder = tmp_path / "episode"
    folder.mkdir()
    (folder / "final.mp4").write_bytes(b"not really a video")

    result = runner.invoke(
        app,
        ["init", str(folder), "--account", "mantra-shakti"],
    )

    assert result.exit_code == 0
    manifest = folder / "publish.yaml"
    assert manifest.is_file()
    assert "# TODO(owner)" in manifest.read_text(encoding="utf-8")
    assert "drafted" in result.output


def test_list_prints_the_channel_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("social_engine.cli.load_credentials", lambda account: object())
    monkeypatch.setattr("social_engine.cli.GoogleYouTubeApi", lambda credentials: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.list_videos",
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

    result = runner.invoke(app, ["list", "youtube", "mantra-shakti", "--limit", "5"])

    assert result.exit_code == 0
    assert "v1" in result.output
    assert "A title" in result.output
    assert "42" in result.output


def test_stats_prints_window_and_lifetime_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    log = PublishLog.load(manifest)
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
    monkeypatch.setattr("social_engine.cli.load_credentials", lambda account: object())
    monkeypatch.setattr("social_engine.cli.GoogleYouTubeApi", lambda credentials: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.stats",
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

    result = runner.invoke(app, ["stats", str(manifest), "--days", "7"])

    assert result.exit_code == 0
    assert "video: v1 (7 days)" in result.output
    assert "views: 1000" in result.output
    assert "lifetime comments: 20" in result.output
    assert "impressions CTR: read it in YouTube Studio" in result.output


def test_stats_without_a_log_entry_exits_2(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "episode")

    result = runner.invoke(app, ["stats", str(manifest)])

    assert result.exit_code == 2
    assert "not_published" in result.output


def test_yt_captions_calls_the_sync_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    monkeypatch.setattr("social_engine.cli._api_for", lambda loaded: object())
    monkeypatch.setattr(
        "social_engine.cli.youtube_ops.sync_captions",
        lambda path, api: OpResult(
            action="captions", video_id="v1", changed=["te"], warnings=[]
        ),
    )

    result = runner.invoke(app, ["yt", "captions", str(manifest)])

    assert result.exit_code == 0
    assert "action: captions" in result.output
    assert "changed: te" in result.output
