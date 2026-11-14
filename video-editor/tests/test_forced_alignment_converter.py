"""Tests for the forced-alignment response -> WhisperX transcript converter."""

from __future__ import annotations

from video_editor_asset_ingestion.scene_assets import forced_alignment_response_to_transcript


def test_forced_alignment_response_to_transcript_basic() -> None:
    """The converter produces a WhisperX-shaped transcript from an ElevenLabs response."""
    response = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5, "alignment": {}},
            {"text": "world", "start": 0.6, "end": 1.0, "alignment": {}},
        ],
        "characters": [],
        "transcript": "hello world",
        "language": "en",
    }
    transcript = forced_alignment_response_to_transcript(response, source_audio="/tmp/audio.mp3")

    # Top-level WhisperX keys are present.
    assert transcript["source_audio"] == "/tmp/audio.mp3"
    assert transcript["language"] == "en"
    assert transcript["language_probability"] == 1.0
    assert transcript["word_timestamps"] is True
    assert isinstance(transcript["segments"], list)
    assert isinstance(transcript["words"], list)

    # Words are renamed ``text`` -> ``word`` and given a default
    # probability of 1.0 (ElevenLabs doesn't expose per-word confidence).
    assert len(transcript["words"]) == 2
    assert transcript["words"][0]["word"] == "hello"
    assert transcript["words"][0]["start"] == 0.0
    assert transcript["words"][0]["end"] == 0.5
    assert transcript["words"][0]["probability"] == 1.0
    assert transcript["words"][1]["word"] == "world"


def test_forced_alignment_response_to_transcript_preserves_word_keys() -> None:
    """If a word already uses the ``word`` key, the converter is tolerant."""
    response = {
        "words": [
            {"word": "fallback", "start": 0.0, "end": 0.3},  # already a 'word' key
        ],
        "language": "en",
    }
    transcript = forced_alignment_response_to_transcript(response)
    assert transcript["words"][0]["word"] == "fallback"


def test_forced_alignment_response_to_transcript_filters_empty_words() -> None:
    """Empty / whitespace-only words are dropped."""
    response = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "   ", "start": 0.5, "end": 0.6},  # whitespace-only
            {"text": "", "start": 0.6, "end": 0.7},  # empty
            {"text": "world", "start": 0.7, "end": 1.0},
        ],
    }
    transcript = forced_alignment_response_to_transcript(response)
    assert len(transcript["words"]) == 2
    assert [w["word"] for w in transcript["words"]] == ["hello", "world"]


def test_forced_alignment_response_to_transcript_chunks_into_segments() -> None:
    """Words are chunked into segments of 30 (the WhisperX default)."""
    response = {
        "words": [{"text": f"w{i}", "start": float(i), "end": float(i + 1)} for i in range(45)],
    }
    transcript = forced_alignment_response_to_transcript(response)
    # 45 words / 30 = 2 segments (30 + 15)
    assert len(transcript["segments"]) == 2
    assert len(transcript["segments"][0]["words"]) == 30
    assert len(transcript["segments"][1]["words"]) == 15


def test_forced_alignment_response_to_transcript_empty_response() -> None:
    """An empty words array produces an empty transcript (not an error)."""
    response = {"words": [], "language": "en"}
    transcript = forced_alignment_response_to_transcript(response)
    assert transcript["words"] == []
    assert transcript["segments"] == []


def test_forced_alignment_response_to_transcript_language_override() -> None:
    """The ``language`` parameter overrides whatever the response set."""
    response = {
        "words": [{"text": "hola", "start": 0.0, "end": 0.3}],
        "language": "en",
    }
    transcript = forced_alignment_response_to_transcript(response, language="es")
    assert transcript["language"] == "es"


def test_forced_alignment_response_skips_words_with_none_timestamps() -> None:
    """C16 regression: words with ``start`` or ``end`` set to None must be
    skipped rather than causing ``float(None)`` to raise TypeError."""
    response = {
        "words": [
            {"text": "hello", "start": 0.0, "end": 0.5},
            {"text": "ignored", "start": None, "end": 0.6},
            {"text": "also_ignored", "start": 0.7, "end": None},
            {"text": "world", "start": 0.8, "end": 1.0},
        ],
    }
    transcript = forced_alignment_response_to_transcript(response)
    words = transcript["words"]
    assert len(words) == 2
    assert [w["word"] for w in words] == ["hello", "world"]


def test_sfx_cache_key_is_per_scene() -> None:
    """C17 regression: two scenes with identical visual descriptions must
    still get distinct SFX cache keys because the key includes scene_id."""
    from video_editor_asset_ingestion.scene_assets import sfx_requests_for_scene
    from video_editor_core.models.scene_plan import Scene

    scene_a = Scene(
        id="scene_a",
        type="narration",
        duration_seconds=5.0,
        visual_description="sparkle",
        narration="",
    )
    scene_b = Scene(
        id="scene_b",
        type="narration",
        duration_seconds=5.0,
        visual_description="sparkle",
        narration="",
    )
    req_a = sfx_requests_for_scene(scene_a, job_id="job", sfx_provider="elevenlabs_sfx")[0]
    req_b = sfx_requests_for_scene(scene_b, job_id="job", sfx_provider="elevenlabs_sfx")[0]
    assert req_a.cache_key() != req_b.cache_key()
