"""Tests for the shared lyrics parser."""

from __future__ import annotations

from video_editor_core.lyrics import FOLDABLE_HEADERS, parse_lyrics

_SURYASTAKAM = """\
फलशृति
दिवाकर ... नमोऽस्तुते ॥ 1 ॥

॥ 2 ॥
रविमण्डल ...

दिवाकर ... नमोऽस्तुते ॥ 3 ॥
"""


def test_one_caption_per_non_empty_line():
    assert parse_lyrics("one\ntwo\n\nthree\n") == ["one", "two", "three"]


def test_a_foldable_header_prefixes_the_next_line():
    assert parse_lyrics("फलशृति\nline one\nline two\n") == ["फलशृति: line one", "line two"]


def test_pure_verse_markers_are_skipped():
    assert parse_lyrics("॥ 1 ॥\nline\n") == ["line"]


def test_an_attached_verse_marker_is_preserved():
    assert parse_lyrics("दिवाकर ॥ 1 ॥\n") == ["दिवाकर ॥ 1 ॥"]


def test_a_trailing_header_stands_alone():
    assert parse_lyrics("\n\nफलशृति\n") == ["फलशृति"]


def test_the_suryastakam_header_is_the_only_foldable_one():
    assert frozenset({"फलशृति"}) == FOLDABLE_HEADERS
    assert parse_lyrics(_SURYASTAKAM) == [
        "फलशृति: दिवाकर ... नमोऽस्तुते ॥ 1 ॥",
        "रविमण्डल ...",
        "दिवाकर ... नमोऽस्तुते ॥ 3 ॥",
    ]
