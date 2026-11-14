"""SubRip sidecar generation (#316)."""

from __future__ import annotations

from video_editor_core.srt import srt_from_words


def test_words_become_numbered_cues_with_timestamps() -> None:
    text = srt_from_words(
        [
            {"text": "Hello", "start": 0.0, "end": 0.4},
            {"text": "world.", "start": 0.5, "end": 1.0},
            {"text": "Next", "start": 2.0, "end": 2.4},
        ]
    )
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,000\nHello world.\n")
    assert "2\n00:00:02,000 --> 00:00:02,400\nNext\n" in text


def test_long_lines_split_on_the_character_budget() -> None:
    words = [
        {"text": "word" * 5, "start": 0.0, "end": 0.5},
        {"text": "word" * 5, "start": 0.6, "end": 1.0},
        {"text": "word" * 5, "start": 1.1, "end": 1.5},
    ]
    text = srt_from_words(words, max_chars=22)
    assert text.count("-->") == 3


def test_display_spelling_wins_over_the_transcript() -> None:
    text = srt_from_words([{"text": "sah-joo", "display": "Saju", "start": 0.0, "end": 0.5}])
    assert "Saju" in text and "sah-joo" not in text
