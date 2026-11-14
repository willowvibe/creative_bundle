"""Tests for ``publish-log.json`` beside a manifest."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from social_engine.core.errors import AlreadyPublished
from social_engine.core.publish_log import PublishLog


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "publish.yaml"
    path.write_text("video: final.mp4\n", encoding="utf-8")
    return path


def test_a_missing_log_is_empty(tmp_path: Path) -> None:
    log = PublishLog.load(_manifest(tmp_path))

    assert log.path == tmp_path / "publish-log.json"
    assert log.entry("youtube") is None


def test_a_directory_argument_reads_the_log_beside_publish_yaml(tmp_path: Path) -> None:
    _manifest(tmp_path)
    assert PublishLog.load(tmp_path).path == tmp_path / "publish-log.json"


def test_record_creates_an_entry_and_save_round_trips(tmp_path: Path) -> None:
    log = PublishLog.load(_manifest(tmp_path))
    log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="api",
        action="upload",
        changed=["video", "thumbnail"],
        warnings=[],
    )
    log.save()

    entry = PublishLog.load(tmp_path / "publish.yaml").entry("youtube")
    assert entry is not None
    assert entry.account == "mantra-shakti"
    assert entry.id == "abc123"
    assert entry.url == "https://youtu.be/abc123"
    assert entry.via == "api"
    assert entry.published_at
    assert entry.history[0]["action"] == "upload"
    assert entry.history[0]["changed"] == ["video", "thumbnail"]
    assert entry.caption_hashes == {}


def test_record_appends_history_and_updates_the_current_fields(tmp_path: Path) -> None:
    log = PublishLog.load(_manifest(tmp_path))
    log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="api",
        action="upload",
        changed=["video"],
        warnings=["private locked"],
    )
    entry = log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="browser",
        action="update",
        changed=["privacy"],
        warnings=[],
    )

    assert entry.via == "browser"
    assert [item["action"] for item in entry.history] == ["upload", "update"]
    assert entry.history[0]["warnings"] == ["private locked"]


def test_caption_hashes_persist(tmp_path: Path) -> None:
    log = PublishLog.load(_manifest(tmp_path))
    log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="api",
        action="upload",
        changed=[],
        warnings=[],
    )
    log.set_caption_hashes("youtube", {"te": "aaa", "en": "bbb"})
    log.save()

    entry = PublishLog.load(_manifest(tmp_path)).entry("youtube")
    assert entry is not None
    assert entry.caption_hashes == {"en": "bbb", "te": "aaa"}


def test_an_existing_entry_blocks_a_second_upload(tmp_path: Path) -> None:
    log = PublishLog.load(_manifest(tmp_path))
    log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="api",
        action="upload",
        changed=[],
        warnings=[],
    )

    with pytest.raises(AlreadyPublished, match="already") as caught:
        log.ensure_unpublished("youtube")
    assert "social update" in caught.value.next_step
    assert "abc123" in str(caught.value)


def test_save_writes_through_a_temp_file_and_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = PublishLog.load(_manifest(tmp_path))
    log.record(
        "youtube",
        account="mantra-shakti",
        id="abc123",
        url="https://youtu.be/abc123",
        via="api",
        action="upload",
        changed=[],
        warnings=[],
    )
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def fake_replace(source: str, destination: str) -> None:
        calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr("social_engine.core.publish_log.os.replace", fake_replace)
    log.save()

    assert len(calls) == 1
    source, destination = calls[0]
    assert Path(source).parent == tmp_path
    assert Path(destination) == log.path
    assert log.path.is_file()
    assert not list(tmp_path.glob(".publish-log.json.*.tmp"))
