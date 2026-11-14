"""Tests for the shared ``publish.yaml`` manifest and the YouTube rules."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import social_engine.core.manifest as manifest_module
from social_engine.core.manifest import (
    Manifest,
    load_manifest,
    validate,
)
from social_engine.platforms.youtube.costs import QUOTA_UNITS, estimate
from social_engine.platforms.youtube.rules import render_description


def _write(folder: Path, text: str) -> Path:
    path = folder / "publish.yaml"
    path.write_text(text, encoding="utf-8")
    return path


BASE = """\
video: final.mp4
title: "A title"
description: "A description"
language: te
youtube:
  account: mantra-shakti
  kind: long
  privacy: private
"""


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "final.mp4").write_bytes(b"not really a video")
    return tmp_path


def _fields(issues: list[object]) -> set[str]:
    return {issue.field for issue in issues}  # type: ignore[attr-defined]


def test_a_valid_manifest_loads(folder: Path) -> None:
    loaded = load_manifest(_write(folder, BASE))

    assert loaded.manifest.video == "final.mp4"
    assert loaded.manifest.language == "te"
    assert loaded.manifest.youtube is not None
    assert loaded.manifest.youtube.account == "mantra-shakti"
    assert loaded.manifest.youtube.kind == "long"
    assert loaded.base_dir == folder
    assert loaded.video_path == folder / "final.mp4"
    assert validate(loaded) == []


def test_a_directory_argument_reads_publish_yaml(folder: Path) -> None:
    path = _write(folder, BASE)
    assert load_manifest(folder).path == path


def test_a_manifest_without_a_platform_section_is_shared_only(folder: Path) -> None:
    loaded = load_manifest(_write(folder, "video: final.mp4\ntitle: A title\n"))

    assert loaded.manifest.youtube is None
    assert validate(loaded) == []


def test_missing_required_fields_are_reported(folder: Path) -> None:
    loaded = load_manifest(_write(folder, 'language: ""\nyoutube:\n  account: mantra-shakti\n'))

    assert {"video", "title", "language"} <= _fields(validate(loaded))


@pytest.mark.parametrize(
    ("title", "message"),
    [
        ("x" * 101, "100"),
        ("bad <title>", "<"),
    ],
)
def test_title_rules(folder: Path, title: str, message: str) -> None:
    loaded = load_manifest(_write(folder, BASE.replace('"A title"', json.dumps(title))))
    issues = [issue for issue in validate(loaded) if issue.field == "title"]
    assert issues and message in issues[0].message


def test_the_youtube_title_override_is_validated(folder: Path) -> None:
    loaded = load_manifest(
        _write(folder, BASE + f"  title: {json.dumps('x' * 101)}\n")
    )
    issues = [issue for issue in validate(loaded) if issue.field == "title"]
    assert issues and "100" in issues[0].message


@pytest.mark.parametrize(
    ("description", "message"),
    [
        ("x" * 5001, "limit"),
        ("ok <script>", "<"),
    ],
)
def test_description_rules(folder: Path, description: str, message: str) -> None:
    text = BASE.replace('"A description"', json.dumps(description))
    loaded = load_manifest(_write(folder, text))
    issues = [issue for issue in validate(loaded) if issue.field == "description"]
    assert issues and message in issues[0].message


def test_tags_limit_counts_quotes_and_commas(folder: Path) -> None:
    tags = ["x" * 250, "y" * 250]
    text = BASE + "  tags:\n" + "".join(f"    - {tag}\n" for tag in tags)
    loaded = load_manifest(_write(folder, text))
    issues = [issue for issue in validate(loaded) if issue.field == "youtube.tags"]
    assert issues and "501" in issues[0].message


@pytest.mark.parametrize(
    ("info", "message"),
    [
        (SimpleNamespace(width=1920, height=1080, duration=60.0), "vertical"),
        (SimpleNamespace(width=1080, height=1920, duration=200.0), "180"),
    ],
)
def test_shorts_must_be_vertical_and_at_most_three_minutes(
    folder: Path, monkeypatch: pytest.MonkeyPatch, info: object, message: str
) -> None:
    monkeypatch.setattr("social_engine.platforms.youtube.rules.probe_media", lambda _path: info)
    loaded = load_manifest(_write(folder, BASE.replace("kind: long", "kind: short")))
    issues = [issue for issue in validate(loaded) if issue.field == "youtube.kind"]
    assert issues and message in issues[0].message


def test_a_vertical_short_under_three_minutes_passes(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = SimpleNamespace(width=1080, height=1920, duration=59.0)
    monkeypatch.setattr("social_engine.platforms.youtube.rules.probe_media", lambda _path: info)
    loaded = load_manifest(_write(folder, BASE.replace("kind: long", "kind: short")))
    assert validate(loaded) == []


@pytest.mark.parametrize("kind", ["episode", ""])
def test_an_unknown_kind_is_reported(folder: Path, kind: str) -> None:
    loaded = load_manifest(_write(folder, BASE.replace("kind: long", f'kind: "{kind}"')))
    assert "youtube.kind" in _fields(validate(loaded))


def test_thumbnail_extension_and_size(folder: Path) -> None:
    (folder / "thumb.gif").write_bytes(b"gif")
    loaded = load_manifest(_write(folder, BASE + "thumbnail: thumb.gif\n"))
    assert "thumbnail" in _fields(validate(loaded))

    (folder / "big.png").write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    loaded = load_manifest(_write(folder, BASE + "thumbnail: big.png\n"))
    issues = [issue for issue in validate(loaded) if issue.field == "thumbnail"]
    assert issues and "2 MB" in issues[0].message


def test_missing_referenced_files_are_reported(folder: Path) -> None:
    text = (
        BASE
        + "  dubs:\n    hi: audio/hi.m4a\n"
        + "  chapters: chapters.json\n"
        + "thumbnail: missing.jpg\n"
        + "captions:\n  te: subs/te.srt\n"
    )
    loaded = load_manifest(_write(folder, text))
    fields = _fields(validate(loaded))
    assert {"thumbnail", "captions.te", "youtube.dubs.hi", "youtube.chapters"} <= fields


def test_language_codes_must_be_bcp47_like(folder: Path) -> None:
    (folder / "subs").mkdir()
    (folder / "subs" / "te.srt").write_text("", encoding="utf-8")
    text = (
        BASE.replace("language: te", "language: telugu!")
        + "  localizations:\n    telugu!: {title: x}\n"
        + "  dubs:\n    telugu!: audio/te.m4a\n"
        + "captions:\n  telugu!: subs/te.srt\n"
    )
    loaded = load_manifest(_write(folder, text))
    fields = _fields(validate(loaded))
    assert {"language", "captions", "youtube.localizations", "youtube.dubs"} <= fields


def test_publish_at_must_be_in_the_future_and_private(folder: Path) -> None:
    past = BASE + "  publish_at: 2020-01-01T00:00:00Z\n"
    loaded = load_manifest(_write(folder, past))
    assert "youtube.publish_at" in _fields(validate(loaded))

    future_unlisted = (
        BASE.replace("privacy: private", "privacy: unlisted")
        + "  publish_at: 2099-01-01T00:00:00Z\n"
    )
    loaded = load_manifest(_write(folder, future_unlisted))
    assert "youtube.publish_at" in _fields(validate(loaded))

    future_private = BASE + "  publish_at: 2099-01-01T00:00:00Z\n"
    loaded = load_manifest(_write(folder, future_private))
    assert "youtube.publish_at" not in _fields(validate(loaded))


def test_invalid_privacy_is_reported(folder: Path) -> None:
    loaded = load_manifest(_write(folder, BASE.replace("privacy: private", "privacy: hidden")))
    assert "youtube.privacy" in _fields(validate(loaded))


def test_the_account_is_required_when_a_youtube_section_exists(folder: Path) -> None:
    loaded = load_manifest(_write(folder, BASE.replace("account: mantra-shakti", 'account: ""')))
    assert "youtube.account" in _fields(validate(loaded))


def _chapters(folder: Path, entries: list[dict[str, object]]) -> None:
    (folder / "chapters.json").write_text(
        json.dumps({"count": len(entries), "chapters": entries}), encoding="utf-8"
    )


def test_chapters_under_one_hour_use_mm_ss(folder: Path) -> None:
    _chapters(folder, [{"start": 0, "title": "Intro"}, {"start": 65, "title": "Part"}])
    loaded = load_manifest(_write(folder, BASE + "  chapters: chapters.json\n"))
    assert render_description(loaded) == "A description\n\n0:00 Intro\n1:05 Part"


def test_chapters_over_one_hour_use_h_mm_ss_and_start_at_zero(folder: Path) -> None:
    _chapters(
        folder,
        [
            {"start": 12, "title": "Intro"},
            {"start": 125, "title": "Part"},
            {"start": 3725, "title": "End"},
        ],
    )
    loaded = load_manifest(_write(folder, BASE + "  chapters: chapters.json\n"))
    assert render_description(loaded) == "A description\n\n0:00 Intro\n2:05 Part\n1:02:05 End"


def test_the_youtube_description_override_is_rendered(folder: Path) -> None:
    _chapters(folder, [{"start": 0, "title": "Intro"}])
    text = BASE + '  description: "Override"\n  chapters: chapters.json\n'
    loaded = load_manifest(_write(folder, text))
    assert render_description(loaded) == "Override\n\n0:00 Intro"


def test_no_write_back_or_channel_key_survives() -> None:
    assert not hasattr(manifest_module, "write_back")
    assert "channel" not in Manifest.model_fields
    assert "video_id" not in Manifest.model_fields
    assert "uploaded_via" not in Manifest.model_fields
    assert "uploaded_at" not in Manifest.model_fields


def test_quota_estimate_uses_the_costs_table(folder: Path) -> None:
    loaded = load_manifest(_write(folder, BASE))
    assert estimate(loaded) == QUOTA_UNITS["videos.insert"] + QUOTA_UNITS["videos.list"]
