"""Tests for the Python browser bridge and the YouTube browser operations."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from social_engine.core.accounts import load_account
from social_engine.core.browser import BrowserResult, browser_call, driver_path
from social_engine.core.errors import (
    AlreadyPublished,
    BrowserNeedsUser,
    BrowserUnexpectedScreen,
    SocialError,
)
from social_engine.core.publish_log import PublishLog
from social_engine.platforms.youtube.browser_ops import (
    browser_confirm,
    browser_dubs,
    browser_upload,
)


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("SOCIAL_ENGINE_HOME", str(home))
    monkeypatch.setattr("social_engine.core.accounts._port_in_use", lambda _port: False)
    return home


def _completed(
    stdout: str, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["node"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
    captured: dict[str, object] | None = None,
) -> dict[str, object]:
    if captured is None:
        captured = {}

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        captured["payload"] = json.loads(
            Path(args[-1]).read_text(encoding="utf-8")
        )
        return _completed(stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr("social_engine.core.browser.subprocess.run", run)
    return captured


def _payload(
    status: str,
    *,
    summary: dict[str, object] | None = None,
    screenshot: str | None = None,
    message: str = "",
    video_id: str | None = None,
) -> str:
    return json.dumps(
        {
            "status": status,
            "summary": summary or {},
            "id": video_id,
            "screenshot": screenshot,
            "message": message,
        }
    )


def test_browser_call_adds_the_account_and_parses_done(
    state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = load_account("youtube", "mantra-shakti")
    captured = _fake_run(
        monkeypatch,
        _payload("done", summary={"signed_in": True}, video_id="v1", message="ok"),
    )

    result = browser_call(
        "youtube",
        "status",
        {"account": "mantra-shakti"},
        timeout=30,
    )

    assert result == BrowserResult(
        status="done",
        summary={"signed_in": True},
        id="v1",
        screenshot=None,
        message="ok",
    )
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["account"] == "mantra-shakti"
    assert payload["cdp_port"] == account.cdp_port
    assert payload["chrome_profile_dir"] == str(
        state_home / "browser" / "youtube-mantra-shakti"
    )
    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "node"
    assert args[1] == str(driver_path())
    assert args[2:5] == ["youtube", "status", "--payload"]
    assert captured["kwargs"]["timeout"] == 30
    assert not Path(args[-1]).exists()


@pytest.mark.parametrize("status", ["ready_to_confirm", "done"])
def test_browser_call_returns_actionable_statuses(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, _payload(status, summary={"step": "details"}, video_id="v1"))

    result = browser_call("youtube", "upload", {})

    assert result.status == status
    assert result.summary == {"step": "details"}
    assert result.id == "v1"


def test_browser_call_maps_needs_user_to_an_error_with_the_screenshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(
        monkeypatch,
        _payload("needs_user", screenshot="/logs/shot.png", message="sign in"),
    )

    with pytest.raises(BrowserNeedsUser, match="sign in") as caught:
        browser_call("youtube", "login", {})

    assert caught.value.screenshot == "/logs/shot.png"
    assert "Chrome" in caught.value.next_step


def test_browser_call_maps_error_to_an_unexpected_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(
        monkeypatch,
        _payload("error", screenshot="/logs/bad.png", message="unknown screen"),
    )

    with pytest.raises(BrowserUnexpectedScreen, match="unknown screen") as caught:
        browser_call("youtube", "upload", {})

    assert caught.value.screenshot == "/logs/bad.png"


def test_browser_call_rejects_non_json_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, "not json at all", stderr="Traceback: boom")

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_bad_output"
    assert "Traceback: boom" in str(caught.value)


def test_browser_call_rejects_json_that_is_not_an_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(monkeypatch, "[]")

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_bad_output"


def test_browser_call_rejects_an_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_run(monkeypatch, _payload("maybe"))

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_bad_output"
    assert "maybe" in str(caught.value)


def test_browser_call_reports_a_nonzero_exit_with_the_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_run(monkeypatch, "", returncode=2, stderr="Error: first\nError: boom")

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_driver_failed"
    assert "Error: boom" in str(caught.value)


def test_browser_call_reports_missing_node(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("node")

    monkeypatch.setattr("social_engine.core.browser.subprocess.run", run)

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_driver_missing"


def test_browser_call_reports_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["node"], 1)

    monkeypatch.setattr("social_engine.core.browser.subprocess.run", run)

    with pytest.raises(SocialError) as caught:
        browser_call("youtube", "status", {})

    assert caught.value.code == "browser_timeout"


def _manifest(folder: Path, *, dubs: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "final.mp4").write_bytes(b"video")
    (folder / "thumbnail.jpg").write_bytes(b"image")
    (folder / "chapters.json").write_text(
        json.dumps({"chapters": [{"start": 0, "title": "Intro"}]}),
        encoding="utf-8",
    )
    lines = [
        "video: final.mp4",
        "title: Shared title",
        "description: Shared description",
        "language: te",
        "thumbnail: thumbnail.jpg",
        "youtube:",
        "  account: mantra-shakti",
        "  kind: long",
        "  title: Override title",
        "  description: Override description",
        "  chapters: chapters.json",
        "  tags: [a, b]",
        "  category: 24",
        "  made_for_kids: false",
        "  privacy: unlisted",
        "  playlists: [Mantra Rahasyam]",
    ]
    if dubs:
        (folder / "audio").mkdir()
        (folder / "audio" / "hi.m4a").write_bytes(b"audio")
        lines += ["  dubs:", "    hi: audio/hi.m4a"]
    path = folder / "publish.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_browser_upload_sends_resolved_fields_and_absolute_paths(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = load_account("youtube", "mantra-shakti")
    manifest = _manifest(tmp_path / "episode")
    captured = _fake_run(
        monkeypatch,
        _payload("ready_to_confirm", summary={"title": "Override title"}, video_id="b1"),
    )

    result = browser_upload(manifest)

    assert result.status == "ready_to_confirm"
    assert result.id == "b1"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["title"] == "Override title"
    assert payload["description"] == "Override description\n\n0:00 Intro"
    assert payload["video"] == str(manifest.parent / "final.mp4")
    assert payload["thumbnail"] == str(manifest.parent / "thumbnail.jpg")
    assert payload["playlists"] == ["Mantra Rahasyam"]
    assert payload["made_for_kids"] is False
    assert payload["synthetic_media"] is True
    assert payload["language"] == "te"
    assert payload["privacy"] == "unlisted"
    assert payload["publish_at"] is None
    assert payload["kind"] == "long"
    assert payload["cdp_port"] == account.cdp_port
    assert payload["chrome_profile_dir"] == str(
        state_home / "browser" / "youtube-mantra-shakti"
    )
    assert PublishLog.load(manifest).entry("youtube") is None


def test_browser_upload_refuses_an_already_published_manifest(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    log = PublishLog.load(manifest)
    log.record(
        "youtube",
        account="mantra-shakti",
        id="existing",
        url="https://youtu.be/existing",
        via="browser",
        action="publish",
        changed=[],
        warnings=[],
    )
    log.save()
    captured: dict[str, object] = {}
    _fake_run(monkeypatch, _payload("ready_to_confirm"), captured=captured)

    with pytest.raises(AlreadyPublished):
        browser_upload(manifest)

    assert "args" not in captured


def test_browser_upload_aborts_on_validation_errors(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    text = manifest.read_text(encoding="utf-8").replace(
        "title: Shared title", 'title: ""'
    )
    manifest.write_text(text, encoding="utf-8")
    captured: dict[str, object] = {}
    _fake_run(monkeypatch, _payload("ready_to_confirm"), captured=captured)

    with pytest.raises(SocialError) as caught:
        browser_upload(manifest)

    assert caught.value.code == "validation_failed"
    assert "args" not in captured


def test_browser_confirm_writes_the_log_only_after_done(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    captured = _fake_run(
        monkeypatch,
        _payload("done", summary={"button": "Publish"}, video_id="browser-video-1"),
    )

    result = browser_confirm(manifest, "browser-video-1", "publish")

    assert result.status == "done"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["id"] == "browser-video-1"
    assert payload["expect"] == "publish"
    entry = PublishLog.load(manifest).entry("youtube")
    assert entry is not None
    assert entry.account == "mantra-shakti"
    assert entry.id == "browser-video-1"
    assert entry.url == "https://youtu.be/browser-video-1"
    assert entry.via == "browser"
    assert entry.history[-1]["action"] == "publish"
    assert entry.history[-1]["changed"] == ["publish"]


def test_browser_confirm_does_not_write_the_log_before_done(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    _fake_run(monkeypatch, _payload("ready_to_confirm", video_id="browser-video-1"))

    result = browser_confirm(manifest, "browser-video-1", "publish")

    assert result.status == "ready_to_confirm"
    assert PublishLog.load(manifest).entry("youtube") is None


def test_browser_confirm_does_not_write_the_log_on_a_needs_user(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode")
    _fake_run(monkeypatch, _payload("needs_user", screenshot="/logs/shot.png"))

    with pytest.raises(BrowserNeedsUser):
        browser_confirm(manifest, "browser-video-1", "publish")

    assert PublishLog.load(manifest).entry("youtube") is None


def test_browser_confirm_rejects_an_unknown_expectation(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "episode")
    with pytest.raises(ValueError, match="expect"):
        browser_confirm(manifest, "browser-video-1", "something")


def test_browser_dubs_sends_the_entry_id_and_resolved_audio(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode", dubs=True)
    log = PublishLog.load(manifest)
    log.record(
        "youtube",
        account="mantra-shakti",
        id="browser-video-1",
        url="https://youtu.be/browser-video-1",
        via="browser",
        action="publish",
        changed=["publish"],
        warnings=[],
    )
    log.save()
    captured = _fake_run(
        monkeypatch,
        _payload("ready_to_confirm", summary={"dubs": ["hi"]}, video_id="browser-video-1"),
    )

    result = browser_dubs(manifest)

    assert result.status == "ready_to_confirm"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["id"] == "browser-video-1"
    assert payload["dubs"] == {"hi": str(manifest.parent / "audio" / "hi.m4a")}
    entry = PublishLog.load(manifest).entry("youtube")
    assert entry is not None
    assert [item["action"] for item in entry.history] == ["publish"]

    _fake_run(
        monkeypatch,
        _payload("done", summary={"dubs": ["hi"]}, video_id="browser-video-1"),
    )
    done = browser_confirm(manifest, "browser-video-1", "dubs")

    assert done.status == "done"
    entry = PublishLog.load(manifest).entry("youtube")
    assert entry is not None
    assert entry.via == "browser"
    assert [item["action"] for item in entry.history] == ["publish", "dubs"]
    assert entry.history[-1]["changed"] == ["hi"]


def test_browser_dubs_requires_a_publish_log_entry(
    state_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "episode", dubs=True)
    captured: dict[str, object] = {}
    _fake_run(monkeypatch, _payload("ready_to_confirm"), captured=captured)

    with pytest.raises(SocialError) as caught:
        browser_dubs(manifest)

    assert caught.value.code == "not_published"
    assert "args" not in captured
