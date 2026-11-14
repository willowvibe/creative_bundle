"""Tests for the strict cue resolver."""

from __future__ import annotations

import pytest
from video_editor_core.cues import CueMismatchError, declared_cue, resolve_cues

WORDS = [
    {"word": "the", "start": 0.0, "end": 0.2},
    {"word": "plum", "start": 0.2, "end": 0.5},
    {"word": "blossom", "start": 0.5, "end": 0.9},
]


def test_a_matching_cue_resolves():
    resolved = resolve_cues(WORDS, {"bloom": "plum blossom"}, lead=0.0)
    assert resolved == {"bloom": pytest.approx(0.2)}


def test_strict_mode_raises_on_a_missing_cue():
    with pytest.raises(CueMismatchError) as exc:
        resolve_cues(WORDS, {"bloom": "bamboo grove"}, strict=True)
    assert exc.value.missing == ["bloom"]


def test_non_strict_mode_still_drops_the_missing_cue_silently():
    assert resolve_cues(WORDS, {"bloom": "bamboo grove"}) == {}


def test_a_declared_cue_falls_back_to_the_display_text():
    assert declared_cue(None, "The Rack") == "The Rack"
    assert declared_cue("  plum blossom  ", "The Rack") == "plum blossom"


def test_a_false_declared_cue_silences_the_item():
    """YAML ``cue: false`` is display copy the voice never says."""
    assert declared_cue(False, "The Rack") == ""
