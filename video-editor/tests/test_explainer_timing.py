from __future__ import annotations

import pytest
from video_editor_pipelines.explainer_timing import (
    FINAL_TAIL_SECONDS,
    LEAD_IN_SECONDS,
    align_words,
    derive_scene_timings,
    sentence_cuts_check,
    spread_pinned_tail,
)


def spoken(*pairs: tuple[str, float, float]) -> list[dict]:
    return [{"word": word, "start": start, "end": end} for word, start, end in pairs]


def test_align_words_times_the_display_spelling():
    words = align_words(
        ["Sah-joo is four pillars."],
        spoken(("saju", 0.2, 0.5), ("is", 0.5, 0.6), ("four", 0.6, 0.8), ("pillars", 0.8, 1.2)),
    )
    assert [word["word"] for word in words] == ["Sah-joo", "is", "four", "pillars."]
    assert words[0]["start"] == 0.2
    assert words[-1]["end"] == 1.2
    # A word holds until the next one starts, so karaoke has no gaps.
    assert words[1]["end"] == words[2]["start"]


def test_scene_starts_a_lead_in_before_the_first_word():
    narrations = ["One two three.", "Four five six."]
    words = align_words(
        narrations,
        spoken(
            ("one", 0.0, 0.3),
            ("two", 0.3, 0.6),
            ("three", 0.6, 0.9),
            ("four", 2.0, 2.3),
            ("five", 2.3, 2.6),
            ("six", 2.6, 2.9),
        ),
    )
    timings = derive_scene_timings(narrations, words)
    assert timings[1].start == pytest.approx(2.0 - LEAD_IN_SECONDS)
    assert timings[0].end == timings[1].start


def test_final_scene_holds_after_the_last_word():
    narrations = ["Alpha beta."]
    words = align_words(narrations, spoken(("alpha", 0.1, 0.4), ("beta", 0.4, 0.9)))
    timings = derive_scene_timings(narrations, words)
    assert timings[-1].end == pytest.approx(0.9 + FINAL_TAIL_SECONDS)


def test_a_single_word_scene_still_gets_a_minimum_length():
    narrations = ["Alpha beta gamma delta.", "Stop."]
    words = align_words(
        narrations,
        spoken(
            ("alpha", 0.0, 0.3),
            ("beta", 0.3, 0.6),
            ("gamma", 0.6, 0.9),
            ("delta", 0.9, 1.2),
            ("stop", 1.25, 1.3),
        ),
    )
    timings = derive_scene_timings(narrations, words)
    assert timings[1].duration >= 1.2
    # Scenes are contiguous: the first ends where the second begins.
    assert timings[0].end == timings[1].start


def test_a_scene_the_recogniser_dropped_falls_back_to_a_word_count_estimate():
    narrations = ["Alpha beta.", "Gamma delta epsilon zeta eta theta."]
    words = align_words(narrations, spoken(("alpha", 0.0, 0.3), ("beta", 0.3, 0.6)))
    timings = derive_scene_timings(narrations, words)
    assert timings[1].words == []
    assert timings[1].duration == pytest.approx(0.38 * 6)


def test_scene_ids_are_numbered_in_script_order():
    timings = derive_scene_timings(
        ["One.", "Two."],
        align_words(["One.", "Two."], spoken(("one", 0.0, 0.3), ("two", 1.0, 1.3))),
    )
    assert [timing.id for timing in timings] == ["scene_1", "scene_2"]


def test_spread_pinned_tail_gives_the_dropped_tail_real_times():
    words = [
        {"word": "alpha", "start": 0.0, "end": 0.4},
        {"word": "beta", "start": 0.4, "end": 0.8},
        {"word": "gamma", "start": 0.8, "end": 0.8},
        {"word": "delta", "start": 0.8, "end": 0.8},
    ]
    moved = spread_pinned_tail(words, audio_end=3.0)
    assert moved == 2
    assert words[-1]["end"] == pytest.approx(3.0)
    assert words[2]["start"] < words[3]["start"]


def test_align_words_spreads_words_in_an_interior_unmatched_gap():
    rec = spoken(
        ("Only", 5.24, 5.5),
        ("about", 5.52, 5.76),
        ("300", 5.78, 6.28),
        ("kilometers", 6.3, 6.76),
        ("long,", 6.78, 7.0),
    )
    words = align_words(["Only about three hundred kilometres long,"], rec)
    assert [w["word"] for w in words] == [
        "Only",
        "about",
        "three",
        "hundred",
        "kilometres",
        "long,",
    ]
    about = next(w for w in words if w["word"] == "about")
    three = next(w for w in words if w["word"] == "three")
    hundred = next(w for w in words if w["word"] == "hundred")
    assert about["end"] == pytest.approx(5.78)
    assert three["start"] >= 5.78
    assert hundred["start"] > three["start"]
    assert hundred["end"] == pytest.approx(6.30)
    assert all(w["start"] < w["end"] for w in words)


def test_align_words_keeps_starts_ordered_when_a_merged_span_covers_the_gap():
    words = align_words(
        ["Only about three hundred kilometres long,"],
        spoken(
            ("Only", 5.24, 5.5),
            ("about", 5.52, 5.76),
            ("300kilometers", 5.78, 6.76),
            ("long,", 6.78, 7.0),
        ),
    )
    starts = [word["start"] for word in words]
    assert starts == sorted(starts)
    assert all(word["start"] <= word["end"] for word in words)


def test_align_words_starts_the_gap_inside_a_span_that_straddles_it():
    words = align_words(
        ["Only about three hundred kilometres long,"],
        spoken(
            ("Only", 5.24, 5.5),
            ("about300", 5.52, 6.28),
            ("kilometers", 6.3, 6.76),
            ("long,", 6.78, 7.0),
        ),
    )
    about = next(word for word in words if word["word"] == "about")
    three = next(word for word in words if word["word"] == "three")
    hundred = next(word for word in words if word["word"] == "hundred")
    assert about["start"] < about["end"]
    assert three["start"] >= 5.52
    assert three["end"] <= 6.3
    assert hundred["start"] > three["start"]
    assert hundred["end"] <= 6.3
    assert all(word["start"] < word["end"] for word in words)


def test_align_words_interpolates_inside_a_span_that_holds_the_next_word():
    words = align_words(
        ["Only about three hundred kilometres long,"],
        spoken(
            ("Only", 5.24, 5.5),
            ("about300kilometers", 5.52, 6.76),
            ("long,", 6.78, 7.0),
        ),
    )
    starts = [word["start"] for word in words]
    assert all(start < next_start for start, next_start in zip(starts, starts[1:], strict=False))
    for word_text in ("about", "three", "hundred", "kilometres"):
        word = next(word for word in words if word["word"] == word_text)
        assert word["start"] < word["end"]


def test_align_words_spells_digit_script_over_a_recognised_word_gap():
    words = align_words(
        ["in 2024 we saw"],
        spoken(
            ("in", 1.0, 1.2),
            ("twenty", 1.3, 1.6),
            ("twenty", 1.7, 2.0),
            ("four", 2.1, 2.4),
            ("we", 2.5, 2.7),
            ("saw", 2.8, 3.0),
        ),
    )
    assert [w["word"] for w in words] == ["in", "2024", "we", "saw"]
    in_w, digit, we, saw = words
    assert in_w["end"] == pytest.approx(digit["start"])
    assert digit["start"] >= 1.3
    assert digit["end"] == pytest.approx(we["start"])
    assert digit["start"] < digit["end"]
    assert we["start"] == pytest.approx(2.5)
    assert saw["start"] == pytest.approx(2.8)


def test_align_words_is_unchanged_when_every_word_matches():
    words = align_words(
        ["Sah-joo is four pillars."],
        spoken(
            ("saju", 0.2, 0.5),
            ("is", 0.5, 0.6),
            ("four", 0.6, 0.8),
            ("pillars", 0.8, 1.2),
        ),
    )
    assert [w["word"] for w in words] == ["Sah-joo", "is", "four", "pillars."]
    assert words[0]["start"] == 0.2
    assert words[-1]["end"] == 1.2
    assert words[1]["end"] == words[2]["start"]


def test_align_words_keeps_zero_length_when_recogniser_heard_nothing_in_gap():
    words = align_words(
        ["Only about three hundred kilometres long,"],
        spoken(
            ("Only", 5.24, 5.5),
            ("about", 5.52, 5.76),
            ("kilometers", 6.3, 6.76),
            ("long,", 6.78, 7.0),
        ),
    )
    three = next(w for w in words if w["word"] == "three")
    hundred = next(w for w in words if w["word"] == "hundred")
    assert three["start"] == three["end"]
    assert hundred["start"] == hundred["end"]


def test_align_words_keeps_a_standalone_dash_on_the_previous_word():
    # #339: "—" has no letters, so it was dropped and captions read
    # "Earth the Sundarbans." instead of "Earth — the Sundarbans."
    words = align_words(
        ["the Earth — the Sundarbans."],
        spoken(("the", 0.0, 0.2), ("earth", 0.2, 0.6), ("the", 0.8, 0.9), ("sundarbans", 0.9, 1.5)),
    )
    assert [word["word"] for word in words] == ["the", "Earth —", "the", "Sundarbans."]
    assert words[1]["start"] == 0.2
    assert words[2]["start"] == 0.8


def test_align_words_keeps_a_leading_dash_on_the_next_word():
    words = align_words(["— and then"], spoken(("and", 0.1, 0.3), ("then", 0.3, 0.6)))
    assert [word["word"] for word in words] == ["— and", "then"]


def test_align_words_keeps_the_spoken_end_of_each_word():
    words = align_words(
        ["on the east. The north"],
        spoken(
            ("on", 11.6, 11.8),
            ("the", 11.8, 12.0),
            ("east", 12.02, 12.30),
            ("the", 12.52, 12.6),
            ("north", 12.6, 13.0),
        ),
    )
    east = words[2]
    assert east["end"] == 12.52  # karaoke: current until the next word starts
    assert east["spoken_end"] == 12.30


def test_a_cut_after_a_short_pause_leaves_at_least_the_minimum_tail():
    # #345: "east." ends 12.30, the next scene starts speaking 12.52. The old
    # cut sat at the next lead-in (12.40), only 0.10 s after the last word.
    from video_editor_pipelines.explainer_timing import MIN_CUT_TAIL_SECONDS

    words = align_words(
        ["Delhi on the east.", "The north rises."],
        spoken(
            ("delhi", 10.0, 10.5),
            ("on", 11.6, 11.8),
            ("the", 11.8, 12.0),
            ("east", 12.02, 12.30),
            ("the", 12.52, 12.6),
            ("north", 12.6, 13.0),
            ("rises", 13.0, 13.5),
        ),
    )
    timings = derive_scene_timings(["Delhi on the east.", "The north rises."], words)
    cut = timings[0].end
    assert cut - 12.30 >= MIN_CUT_TAIL_SECONDS - 1e-9
    assert cut < 12.52
    assert timings[1].start == cut


def test_a_long_pause_still_cuts_at_the_next_lead_in():
    words = align_words(
        ["One two.", "Three four."],
        spoken(("one", 0.0, 0.4), ("two", 0.4, 0.8), ("three", 2.0, 2.4), ("four", 2.4, 2.8)),
    )
    timings = derive_scene_timings(["One two.", "Three four."], words)
    assert timings[0].end == pytest.approx(2.0 - LEAD_IN_SECONDS)


def test_sentence_cut_check_fails_a_cut_on_a_word_tail():
    from video_editor_pipelines.explainer_timing import SceneTiming, sentence_cuts_check

    tight = [
        SceneTiming(
            id="a",
            start=0.0,
            end=12.40,
            words=[{"word": "east.", "start": 12.02, "end": 12.52, "spoken_end": 12.30}],
        ),
        SceneTiming(
            id="b",
            start=12.40,
            end=14.0,
            words=[{"word": "The", "start": 12.52, "end": 12.6, "spoken_end": 12.6}],
        ),
    ]
    roomy = [
        SceneTiming(id="a", start=0.0, end=12.45, words=tight[0].words),
        SceneTiming(id="b", start=12.45, end=14.0, words=tight[1].words),
    ]
    check = sentence_cuts_check(tight)
    assert check.name == "sentence_cuts" and not check.ok and "a" in check.detail
    assert sentence_cuts_check(roomy).ok


def test_sentence_cut_check_reports_but_passes_sentences_spoken_without_a_pause():
    # Whisper often ends a word exactly where the next begins when the narrator
    # runs sentences together; there is no silence to cut into, so the latest
    # cut (just before the next word) is the best available, not a failure.
    from video_editor_pipelines.explainer_timing import SceneTiming, sentence_cuts_check

    touching = [
        SceneTiming(
            id="scene_9",
            start=49.05,
            end=54.60,
            words=[{"word": "rain.", "start": 54.32, "end": 54.64, "spoken_end": 54.64}],
        ),
        SceneTiming(
            id="scene_10",
            start=54.60,
            end=60.0,
            words=[{"word": "The", "start": 54.64, "end": 55.2, "spoken_end": 55.2}],
        ),
    ]
    check = sentence_cuts_check(touching)
    assert check.ok
    assert "pause too short" in check.detail and "scene_9" in check.detail


def test_a_very_short_pause_never_cuts_before_the_last_word_ends():
    words = align_words(
        ["Look on the east.", "The north."],
        spoken(
            ("look", 10.0, 10.5),
            ("on", 11.6, 11.8),
            ("the", 11.8, 12.0),
            ("east", 12.02, 12.30),
            ("the", 12.32, 12.6),
            ("north", 12.6, 13.0),
        ),
    )
    timings = derive_scene_timings(["Look on the east.", "The north."], words)
    assert 12.30 <= timings[0].end <= 12.32


def test_sentence_cut_check_passes_the_best_cut_a_short_pause_allows():
    # A 0.10 s pause cannot hold a 0.15 s tail plus a lead-in: the latest cut
    # before the next word is the best available, so the gate reports it.
    words = align_words(
        ["Look on the east.", "The north."],
        spoken(
            ("look", 10.0, 10.5),
            ("on", 11.6, 11.8),
            ("the", 11.8, 12.0),
            ("east", 12.02, 12.30),
            ("the", 12.40, 12.6),
            ("north", 12.6, 13.0),
        ),
    )
    timings = derive_scene_timings(["Look on the east.", "The north."], words)
    check = sentence_cuts_check(timings)
    assert check.ok and "scene_1" in check.detail
