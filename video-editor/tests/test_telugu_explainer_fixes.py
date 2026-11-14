"""Fixes from the first Telugu pilot (chai-stall-pilot-1)."""

from __future__ import annotations

from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_pipelines.explainer_pipeline import result_cost, transcription_language
from video_editor_pipelines.explainer_timing import align_words
from video_editor_pipelines.pricing import PricingRegistry


def test_telugu_words_get_real_timings() -> None:
    narrations = ["ఒక కప్పు చాయ్ ధర.", "మన బండి కాలేజీ దగ్గర ఉంది."]
    recognised = [
        {"word": "ఒక", "start": 0.0, "end": 0.3},
        {"word": "కప్పు", "start": 0.3, "end": 0.7},
        {"word": "చాయ", "start": 0.7, "end": 1.0},
        {"word": "ధర", "start": 1.0, "end": 1.4},
        {"word": "మన", "start": 1.8, "end": 2.0},
        {"word": "బండి", "start": 2.0, "end": 2.4},
        {"word": "కాలేజీ", "start": 2.4, "end": 2.9},
        {"word": "దగ్గర", "start": 2.9, "end": 3.3},
        {"word": "ఉంది", "start": 3.3, "end": 3.7},
    ]

    words = align_words(narrations, recognised)

    assert len(words) == 9
    assert words[4]["start"] >= 1.7  # "మన" opens the second scene at its own time
    assert words[-1]["end"] > 3.0


def test_numbers_spoken_as_words_still_time_the_display_digits() -> None:
    # The display says ₹15; the voice said "పదిహేను రూపాయలు".
    narrations = ["కప్పు ₹15. రోజుకి నాలుగు వందల కప్పులు."]
    recognised = [
        {"word": "కప్పు", "start": 0.0, "end": 0.4},
        {"word": "పదిహేను", "start": 0.4, "end": 0.9},
        {"word": "రూపాయలు", "start": 0.9, "end": 1.4},
        {"word": "రోజుకి", "start": 1.6, "end": 2.0},
        {"word": "నాలుగు", "start": 2.0, "end": 2.4},
        {"word": "వందల", "start": 2.4, "end": 2.8},
        {"word": "కప్పులు", "start": 2.8, "end": 3.3},
    ]

    words = align_words(narrations, recognised)
    rupees = next(w for w in words if "₹15" in w["word"])

    assert 0.3 <= rupees["start"] <= 1.0
    assert rupees["end"] <= 1.7


def test_the_transcriber_listens_in_the_script_language() -> None:
    assert transcription_language("te", "en") == "te"
    assert transcription_language("te-IN", "en") == "te"
    assert transcription_language("", "en") == "en"
    assert transcription_language("", "") is None


def test_a_free_sfx_without_provider_metadata_is_priced_from_its_request() -> None:
    request = AssetRequest(
        asset_id="scene_1_sfx",
        job_id="j",
        scene_id="scene_1",
        asset_type=AssetType.SFX,
        prompt="soft whoosh",
        provider="freesound_sfx",
        output_ext="mp3",
    )
    result = AssetResult(
        asset_id="scene_1_sfx",
        asset_type=AssetType.SFX,
        scene_id="scene_1",
        local_path="/tmp/x.mp3",
        duration_ms=900,
        metadata={"title": "whoosh"},
    )

    assert result_cost(result, request, PricingRegistry.load()) == 0.0


def test_the_tally_chip_uses_the_engine_font_stack() -> None:
    from video_editor_core.fonts import font_stack
    from video_editor_core.tally_chip import Tally, render_tally

    _markup, css = render_tally(Tally("గల్లా పెట్టె", 100), None, index=0, width=1920)

    assert f"font-family: {font_stack(None)};" in css


def test_a_pixabay_clip_reporting_duration_in_seconds_is_priced() -> None:
    request = AssetRequest(
        asset_id="scene_5_footage",
        job_id="j",
        scene_id="scene_5",
        asset_type=AssetType.VIDEO,
        prompt="gas stove flame",
        provider="stock",
        output_ext="mp4",
    )
    result = AssetResult(
        asset_id="scene_5_footage",
        asset_type=AssetType.VIDEO,
        scene_id="scene_5",
        local_path="/tmp/x.mp4",
        metadata={"provider": "pixabay", "duration": 12},
    )

    assert result_cost(result, request, PricingRegistry.load()) == 0.0


def test_telugu_caption_frames_are_not_mistaken_for_lone_numbers() -> None:
    from video_editor_pipelines.caption_qa import caption_tokens_check

    script = "ఒక కప్పుకి 60 ml, అంటే రోజుకి 24 లీటర్లు."
    lines = ["ఒక కప్పుకి 60 ml,", "అంటే రోజుకి 24 లీటర్లు."]

    assert caption_tokens_check(lines, script).ok


def test_a_telugu_caption_word_missing_from_the_script_is_caught() -> None:
    from video_editor_pipelines.caption_qa import caption_tokens_check

    check = caption_tokens_check(["అంటే రోజుకి లీటర్లుఒక"], "అంటే రోజుకి లీటర్లు ఒక")

    assert not check.ok
    assert "not in script" in check.detail


def test_per_character_tts_is_priced_by_its_characters_not_whole_blocks() -> None:
    from video_editor_asset_ingestion.pricing import estimate_tts_cost

    registry = PricingRegistry.load()
    # ElevenLabs bills credits per character: 177 chars at $0.08/1k is ~$0.014, not $0.08.
    assert estimate_tts_cost("elevenlabs_tts", "eleven_v4", 177, registry) == 0.0142
    # Sarvam really does bill each call in whole 1,000-character blocks.
    assert (
        estimate_tts_cost("sarvam_tts", "bulbul:v4-flash", 300, registry, char_counts=[177, 123])
        == 0.046
    )


def test_landscape_captions_sit_near_ninety_percent_of_the_height() -> None:
    from video_editor_pipelines.explainer_pipeline import caption_band

    band = caption_band(1920, 1080)
    centre = (band.y + band.height / 2) / 1080
    assert 0.86 <= centre <= 0.91
    assert band.y + band.height <= 0.95 * 1080  # inside the 5% action-safe edge


def test_portrait_captions_keep_their_place_above_the_shorts_ui() -> None:
    from video_editor_pipelines.explainer_pipeline import caption_band

    band = caption_band(1080, 1920)
    assert band.y == int(1920 * 0.72)
