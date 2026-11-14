"""Tests for drafting ``publish.yaml`` from a finished video folder."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from social_engine.core.draft import DraftError, draft_manifest
from social_engine.core.manifest import load_manifest, validate


def _touch(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture(autouse=True)
def _probe(monkeypatch: pytest.MonkeyPatch) -> None:
    info = SimpleNamespace(width=1920, height=1080, duration=300.0)
    monkeypatch.setattr("social_engine.core.draft.probe_media", lambda _path: info)


def test_detects_the_finished_folder(tmp_path: Path) -> None:
    _touch(tmp_path / "final.mp4")
    _touch(tmp_path / "thumbnail.jpg")
    _touch(tmp_path / "chapters.json", b"{}")
    _touch(tmp_path / "subs" / "te.srt", b"")
    _touch(tmp_path / "subs" / "ep1.en.srt", b"")
    _touch(tmp_path / "subs" / "hi.srt", b"")
    _touch(tmp_path / "audio" / "hi.m4a", b"")
    _touch(tmp_path / "audio" / "ta.m4a", b"")

    path = draft_manifest(tmp_path, account="mantra-shakti")

    assert path == tmp_path / "publish.yaml"
    loaded = load_manifest(path)
    manifest = loaded.manifest
    youtube = manifest.youtube
    assert youtube is not None
    assert youtube.account == "mantra-shakti"
    assert manifest.video == "final.mp4"
    assert youtube.kind == "long"
    assert manifest.thumbnail == "thumbnail.jpg"
    assert youtube.chapters == "chapters.json"
    assert manifest.captions == {
        "en": "subs/ep1.en.srt",
        "hi": "subs/hi.srt",
        "te": "subs/te.srt",
    }
    assert youtube.dubs == {"hi": "audio/hi.m4a", "ta": "audio/ta.m4a"}
    assert manifest.language == "en"
    assert youtube.privacy == "private"
    assert manifest.synthetic_media is True
    assert youtube.made_for_kids is False
    assert manifest.title == ""
    assert manifest.description == ""
    assert youtube.tags == []
    text = path.read_text(encoding="utf-8")
    assert "# TODO(owner)" in text
    assert "title" in {issue.field for issue in validate(loaded)}
    assert "channel:" not in text
    assert "video_id:" not in text


def test_vertical_video_under_three_minutes_is_a_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _touch(tmp_path / "final.mp4")
    info = SimpleNamespace(width=1080, height=1920, duration=120.0)
    monkeypatch.setattr("social_engine.core.draft.probe_media", lambda _path: info)

    path = draft_manifest(tmp_path, account="cosmicsaju")

    loaded = load_manifest(path)
    assert loaded.manifest.youtube is not None
    assert loaded.manifest.youtube.kind == "short"


def test_an_explicit_kind_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch(tmp_path / "final.mp4")
    info = SimpleNamespace(width=1080, height=1920, duration=120.0)
    monkeypatch.setattr("social_engine.core.draft.probe_media", lambda _path: info)

    path = draft_manifest(tmp_path, account="cosmicsaju", kind="long")

    loaded = load_manifest(path)
    assert loaded.manifest.youtube is not None
    assert loaded.manifest.youtube.kind == "long"


def test_largest_mp4_is_used_when_there_is_no_final_cut(tmp_path: Path) -> None:
    _touch(tmp_path / "draft.mp4", b"x")
    _touch(tmp_path / "rendered.mp4", b"x" * 100)

    path = draft_manifest(tmp_path, account="mantra-shakti")

    assert load_manifest(path).manifest.video == "rendered.mp4"


def test_two_final_cuts_are_ambiguous(tmp_path: Path) -> None:
    _touch(tmp_path / "final.mp4")
    _touch(tmp_path / "final-v2.mp4")

    with pytest.raises(DraftError, match="final"):
        draft_manifest(tmp_path, account="mantra-shakti")


def test_refuses_to_overwrite_an_existing_manifest(tmp_path: Path) -> None:
    _touch(tmp_path / "final.mp4")
    (tmp_path / "publish.yaml").write_text("video: keep-me\n", encoding="utf-8")

    with pytest.raises(FileExistsError):
        draft_manifest(tmp_path, account="mantra-shakti")


def test_no_caption_files_defaults_to_english(tmp_path: Path) -> None:
    _touch(tmp_path / "final.mp4")

    path = draft_manifest(tmp_path, account="mantra-shakti")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["language"] == "en"
    assert data["captions"] == {}
    assert data["youtube"]["dubs"] == {}
