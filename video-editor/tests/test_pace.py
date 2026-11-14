"""Tests for the speech-pace analysis module (#105 / A54)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from video_editor_engine.analysis.pace import analyze_pace


def _write_transcript(path: Path, words: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"words": words}, indent=2),
        encoding="utf-8",
    )


def test_analyze_pace_empty_transcript(tmp_path: Path) -> None:
    """An empty word list produces zero WPM and an empty window list."""
    transcript = tmp_path / "empty.json"
    _write_transcript(transcript, [])
    output = tmp_path / "pace.json"

    result = analyze_pace(transcript, output)

    assert result["source"] == str(transcript)
    assert result["windows"] == []
    assert result["overall_wpm"] == 0.0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["overall_wpm"] == 0.0


def test_analyze_pace_sliding_windows(tmp_path: Path) -> None:
    """Words are bucketed into non-overlapping 10s windows by default."""
    words = [
        {"word": "one", "start": 0.0, "end": 0.5},
        {"word": "two", "start": 2.0, "end": 2.5},
        {"word": "three", "start": 11.0, "end": 11.5},
    ]
    transcript = tmp_path / "words.json"
    _write_transcript(transcript, words)
    output = tmp_path / "pace.json"

    result = analyze_pace(transcript, output, window_seconds=10.0)

    assert result["window_seconds"] == 10.0
    assert len(result["windows"]) == 2
    assert result["windows"][0]["word_count"] == 2
    assert result["windows"][1]["word_count"] == 1
    # 2 words in 10s = 12 WPM for the first window.
    assert result["windows"][0]["wpm"] == 12.0
    # Overall: 3 words / 11.5s ≈ 15.65 WPM.
    assert result["overall_wpm"] == pytest.approx(15.65, rel=0.01)


def test_analyze_pace_custom_window(tmp_path: Path) -> None:
    """A smaller window produces more buckets."""
    words = [{"word": str(i), "start": float(i), "end": float(i) + 0.1} for i in range(5)]
    transcript = tmp_path / "words.json"
    _write_transcript(transcript, words)
    output = tmp_path / "pace.json"

    result = analyze_pace(transcript, output, window_seconds=2.0)

    assert result["window_seconds"] == 2.0
    assert len(result["windows"]) == 3
