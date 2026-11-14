from __future__ import annotations

import html
import re

import pytest
from video_editor_core.designs import load_design
from video_editor_core.typographic_scenes import (
    SceneRenderError,
    render_typographic,
    scene_cues,
    silence_unspoken_typographic_cues,
)

DESIGN = load_design("cream_editorial")


def render(props: dict, *, duration: float = 4.0, words=None, scene_id: str = "scene_1"):
    return render_typographic(
        {"id": scene_id, "type": "typographic", "props": props},
        DESIGN,
        index=1,
        duration=duration,
        width=1080,
        words=words,
    )


def test_big_stat_renders_headline_sub_and_unit():
    result = render(
        {"layout": "big_stat", "headline": "$32B", "unit": "USD", "sub": "largest ever"}
    )
    assert "$32B" in result.markup
    assert "USD" in result.markup
    assert "largest ever" in result.markup
    assert "typo-headline" in result.css


def test_bar_compare_scales_bars_to_the_largest_value():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32, "unit": "B"},
                {"label": "Nest", "value": 3.2, "unit": "B"},
            ],
        }
    )
    widths = [
        float(w) for w in re.findall(r'class="typo-bar" style="width:([\d.]+)%"', result.markup)
    ]
    assert widths[0] == 100.0
    assert widths[1] == pytest.approx(10.0)
    assert "Nest" in result.markup


def test_bar_compare_rejects_one_or_five_bars():
    with pytest.raises(SceneRenderError, match="2-4"):
        render({"layout": "bar_compare", "bars": [{"label": "A", "value": 1}]})


def test_a_bar_can_cue_on_the_spoken_phrase_behind_its_label():
    scene = {
        "id": "scene_3",
        "props": {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32, "cue": "Google's biggest deal"},
                {"label": "Nest", "value": 3.2},
            ],
        },
    }
    assert scene_cues(scene) == {
        "item_0": "Google's biggest deal",
        "item_1": "Nest",
    }


def test_a_cued_bar_starts_when_its_phrase_is_spoken():
    words = [
        {"word": "Google's", "start": 0.4, "end": 0.6},
        {"word": "biggest", "start": 0.6, "end": 0.8},
        {"word": "deal", "start": 0.8, "end": 1.0},
    ]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32, "cue": "Google's biggest deal"},
                {"label": "Nest", "value": 3.2},
            ],
        },
        words=words,
    )
    delays = re.findall(r'class="typo-bar-row" style="animation-delay:([\d.]+)s"', result.markup)
    assert float(delays[0]) == pytest.approx(0.4)


def test_a_sequence_step_can_cue_on_the_spoken_phrase_behind_its_label():
    scene = {
        "id": "scene_6",
        "props": {
            "layout": "sequence",
            "steps": [
                "Everyone moved to the cloud.",
                {
                    "label": "Breaches start with a wrong key.",
                    "cue": "breach starts with one wrong key",
                },
                "Fix it",
            ],
        },
    }
    assert scene_cues(scene)["item_2"] == "breach starts with one wrong key"


def test_a_plain_sequence_step_still_cues_on_its_own_text():
    scene = {
        "id": "scene_6",
        "props": {"layout": "sequence", "steps": ["Find", "Watch", "Fix"]},
    }
    assert scene_cues(scene) == {"item_1": "Find", "item_2": "Watch", "item_3": "Fix"}


def test_definition_renders_term_and_gloss():
    result = render(
        {"layout": "definition", "term": "A digital X-ray", "gloss": "Seeing inside a cloud."}
    )
    assert "A digital X-ray" in result.markup
    assert "Seeing inside a cloud." in result.markup


def test_sequence_renders_steps_and_a_connector():
    result = render({"layout": "sequence", "steps": ["Find", "Watch", "Fix"]})
    assert result.markup.count("typo-step") == 3
    assert "typo-connector" in result.markup


def test_array_lights_the_requested_cells_in_order():
    result = render({"layout": "array", "cells": 12, "cols": 4, "highlight": [0, 3, 7]})
    assert result.markup.count("typo-cell") == 12
    assert result.markup.count("is-hot") == 3
    assert "nth-child(3)" in result.css


def test_text_is_escaped():
    result = render({"layout": "definition", "term": "<script>", "gloss": "a & b"})
    assert "<script>" not in result.markup
    assert "&lt;script&gt;" in result.markup
    assert "a &amp; b" in result.markup


def test_tokens_reach_the_html():
    dark = load_design("ink_noir")
    result = render_typographic(
        {"id": "s", "type": "typographic", "props": {"layout": "big_stat", "headline": "X"}},
        dark,
        index=2,
        duration=3.0,
        width=1920,
    )
    assert dark.palette.bg in result.css
    assert dark.palette.accent in result.css
    assert "Montserrat" in result.css


def test_a_definition_term_can_opt_out_of_its_default_cue():
    assert (
        scene_cues(
            {
                "props": {
                    "layout": "definition",
                    "term": "Strait",
                    "gloss": "A narrow gate",
                    "term_cue": False,
                }
            }
        )
        == {}
    )


def test_a_step_or_bar_can_opt_out_of_its_default_cue():
    assert (
        scene_cues(
            {
                "props": {
                    "layout": "sequence",
                    "steps": [
                        {"label": "Sultanate", "cue": False},
                        {"label": "Empire", "cue": False},
                        {"label": "Colony", "cue": False},
                    ],
                }
            }
        )
        == {}
    )
    assert (
        scene_cues(
            {
                "props": {
                    "layout": "bar_compare",
                    "bars": [
                        {"label": "The Rack", "value": 6, "cue": False},
                        {"label": "The Stone", "value": 3, "cue": False},
                    ],
                }
            }
        )
        == {}
    )


def test_silencing_turns_off_only_the_defaults_the_narration_never_says():
    narration = "Sultanate then empire"
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_typographic_cues(
        {"layout": "sequence", "steps": ["Sultanate", "Empire", "Colony"]}, words
    )
    assert out["steps"] == ["Sultanate", "Empire", {"label": "Colony", "cue": False}]


def test_a_spoken_definition_term_keeps_its_default_cue():
    narration = "A strait is a gate"
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_typographic_cues(
        {"layout": "definition", "term": "Strait", "gloss": "A narrow gate"}, words
    )
    assert "term_cue" not in out


def test_unknown_layout_is_rejected():
    with pytest.raises(SceneRenderError, match="unknown layout"):
        render({"layout": "pie_chart"})


def test_scene_cues_returns_the_layout_cue_map_without_a_design():
    bars = {
        "layout": "bar_compare",
        "bars": [{"label": "Wiz", "value": 32}, {"label": "Nest", "value": 3.2}],
    }
    assert scene_cues({"id": "s", "props": bars}) == {"item_0": "Wiz", "item_1": "Nest"}
    definition = {"layout": "definition", "term": "Cloud security", "gloss": "g"}
    assert scene_cues({"id": "s", "props": definition}) == {"item_1": "Cloud security"}
    # ``array`` has no cued item; entry=0.0 is safe because it only feeds CSS.
    assert scene_cues({"id": "s", "props": {"layout": "array", "cells": 4, "cols": 2}}) == {}


def test_scene_cues_rejects_an_unknown_layout():
    with pytest.raises(SceneRenderError, match="unknown layout"):
        scene_cues({"id": "s", "props": {"layout": "pie_chart"}})


def test_word_cued_reveal_precedes_the_stagger_fallback():
    words = [{"word": "Nest", "start": 2.4, "end": 2.6}]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32},
                {"label": "Nest", "value": 3},
            ],
        },
        words=words,
    )
    delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert delays[1] == pytest.approx(2.4)
    assert delays[0] < delays[1]


def test_reveal_never_schedules_past_the_scene():
    words = [{"word": "Nest", "start": 9.5, "end": 9.7}]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32},
                {"label": "Nest", "value": 3},
            ],
        },
        duration=3.0,
        words=words,
    )
    delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert max(delays) <= 3.0


@pytest.mark.parametrize("cells", [32, 40, 60])
def test_last_array_cell_is_visible_before_the_scene_ends(cells):
    props = {"layout": "array", "cells": cells, "cols": 8, "highlight": [cells - 1]}
    result = render(props, duration=4.0)
    delays = [float(value) for value in re.findall(r"animation-delay:\s*([0-9.]+)s", result.css)]
    assert delays and max(delays) < 4.0


def test_low_tempo_keeps_cued_delays_inside_the_scene():
    design = DESIGN.model_copy(deep=True)
    design.motion.tempo = 0.25
    entry = 0.35 / 0.25
    words = [{"word": "Revenue", "start": 1.65, "end": 1.85}]
    result = render_typographic(
        {
            "id": "s",
            "type": "typographic",
            "props": {
                "layout": "big_stat",
                "headline": "$32B",
                "cue": "Revenue",
                "sub": "largest ever",
            },
        },
        design,
        index=3,
        duration=2.0,
        words=words,
    )
    delays = [
        float(value)
        for value in re.findall(r"animation-delay:\s*([0-9.]+)s", result.markup + result.css)
    ]
    assert delays and max(delays) <= 2.0 - entry + 1e-9


def test_bar_grow_waits_for_its_row_to_reveal():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Wiz", "value": 32},
                {"label": "Nest", "value": 3},
            ],
        }
    )
    row_delays = [float(d) for d in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    bar_delays = {
        int(position): float(delay)
        for position, delay in re.findall(
            r"nth-child\((\d+)\)\s*\.typo-bar\s*\{[^}]*?animation-delay:\s*([\d.]+)s",
            result.css,
        )
    }
    assert bar_delays.get(2, 0.0) > 0.0
    assert bar_delays[2] == pytest.approx(row_delays[1], abs=0.05)


def test_non_integer_index_is_rejected_before_markup():
    with pytest.raises((TypeError, ValueError)):
        render_typographic(
            {
                "id": "s",
                "type": "typographic",
                "props": {"layout": "big_stat", "headline": "X"},
            },
            DESIGN,
            index="3}body{display:none}",
            duration=3.0,
        )


def test_a_step_cued_near_the_cut_is_pulled_back_until_it_is_readable():
    """A reveal may not start so late that its hold runs past the scene end.

    The craft check gives a six-word line 1.725 s on screen; a cue 0.2 s
    before the cut would flash it for a fraction of that.
    """
    words = [
        {"word": "one", "start": 0.4, "end": 0.6},
        {"word": "two", "start": 0.7, "end": 0.9},
        {"word": "three", "start": 1.0, "end": 1.2},
        {"word": "sheltered", "start": 3.8, "end": 3.9},
        {"word": "shorter", "start": 3.9, "end": 4.0},
    ]
    result = render(
        {
            "layout": "sequence",
            "steps": [
                {"label": "Up the coast", "cue": "one"},
                {"label": "Through the strait: sheltered, shorter", "cue": "sheltered shorter"},
                {"label": "Across the bay", "cue": "three"},
            ],
        },
        duration=4.0,
        words=words,
    )
    steps = re.findall(r'class="typo-step" style="animation-delay:([\d.]+)s"', result.markup)
    # six words, so the entry (0.35 s) and the 1.725 s floor must fit first.
    assert float(steps[1]) == pytest.approx(1.925)


def _count_steps(markup: str) -> list[str]:
    return re.findall(r'<span class="typo-count-step"[^>]*>([^<]*)</span>', markup)


def _count_delays(markup: str) -> list[float]:
    return [
        float(delay)
        for delay in re.findall(
            r'class="typo-count-step"[^>]*animation-delay:\s*([0-9.]+)s', markup
        )
    ]


def test_big_stat_counts_up_with_separators_and_the_original_suffix():
    result = render({"layout": "big_stat", "headline": "2,400 km"})
    steps = _count_steps(result.markup)
    assert len(steps) >= 12
    assert steps[0] == "0"
    assert steps[-1] == "2,400"
    assert any(re.fullmatch(r"\d{1,3}(?:,\d{3})+", value) for value in steps[:-1])
    assert "km" in result.markup
    assert "display: inline-grid" in result.css
    assert "grid-area: 1 / 1" in result.css


def test_big_stat_count_up_keeps_one_decimal_and_the_suffix():
    result = render({"layout": "big_stat", "headline": "3.3 million km²"})
    steps = _count_steps(result.markup)
    assert steps[-1] == "3.3"
    assert all(re.fullmatch(r"\d+\.\d", value) for value in steps)
    assert "million km²" in result.markup


def test_big_stat_only_counts_the_first_number():
    result = render({"layout": "big_stat", "headline": "Top 10 of 2024"})
    assert _count_steps(result.markup)[-1] == "10"
    assert "of 2024" in result.markup


def test_a_headline_without_a_number_has_no_count_markup():
    result = render({"layout": "big_stat", "headline": "growth at any cost"})
    assert "typo-count" not in result.markup
    assert "typo-count" not in result.css
    assert "growth at any cost" in result.markup


def test_count_false_leaves_the_headline_alone():
    result = render({"layout": "big_stat", "headline": "2,400 km", "count": False})
    assert "typo-count" not in result.markup
    assert "2,400 km" in result.markup


def test_count_steps_start_with_the_headline_and_hold_the_final_value():
    result = render({"layout": "big_stat", "headline": "2,400 km"})
    headline_delay = float(
        re.search(r'class="typo-line" style="animation-delay:([\d.]+)s"', result.markup).group(1)
    )
    delays = _count_delays(result.markup)
    assert delays and delays[0] == pytest.approx(headline_delay)
    durations = [
        float(duration)
        for duration in re.findall(
            r'class="typo-count-step"[^>]*animation-duration:\s*([0-9.]+)s', result.markup
        )
    ]
    assert durations and all(duration == pytest.approx(1.4) for duration in durations)
    assert "animation-fill-mode: both" in result.markup
    final = re.search(r"@keyframes tp_count_1_\d+ \{.*?100% \{ opacity: 1; \}", result.css, re.S)
    assert final is not None


def test_definition_gloss_reveals_word_by_word():
    gloss = "Seeing inside a cloud now."
    result = render({"layout": "definition", "term": "A digital X-ray", "gloss": gloss})
    words = re.findall(r'<span class="typo-word"[^>]*>([^<]*)</span>', result.markup)
    assert words == ["Seeing", "inside", "a", "cloud", "now."]
    delays = [
        float(delay)
        for delay in re.findall(
            r'class="typo-word"[^>]*animation-delay:\s*([0-9.]+)s', result.markup
        )
    ]
    assert len(delays) == 5
    assert all(earlier < later for earlier, later in zip(delays, delays[1:], strict=False))
    assert 1.2 <= delays[-1] - delays[0] <= 2.0
    gloss_markup = re.search(
        r'<div class="typo-line typo-gloss"[^>]*>(.*?)</div>', result.markup, re.S
    ).group(1)
    assert html.unescape(re.sub(r"<[^>]+>", "", gloss_markup)) == gloss


@pytest.mark.parametrize(
    "props",
    [
        {"layout": "big_stat", "headline": "2,400 km"},
        {"layout": "definition", "term": "Strait", "gloss": "A narrow gate"},
    ],
)
def test_the_accent_rule_grows_for_the_whole_scene(props):
    result = render(props, duration=6.0)
    assert 'class="typo-rule"' in result.markup
    assert re.search(
        r"\.typo-1 \.typo-rule \{[^}]*animation: tp_rule_1 6\.000s linear both", result.css
    )
    assert DESIGN.palette.accent in result.css


def test_count_steps_keep_their_width_so_the_unit_does_not_jump():
    # Proportional digits made narrow steps sit left in the cell, opening a gap
    # before the unit mid-count; tabular figures, right-aligned, keep it fixed.
    css = render({"layout": "big_stat", "headline": "2,400 km"}).css
    rule = css.split(".typo-1 .typo-count {", 1)[1].split("}", 1)[0]
    assert "tabular-nums" in rule and "justify-items: end" in rule


def test_a_year_counts_up_without_gaining_a_separator():
    steps = _count_steps(render({"layout": "big_stat", "headline": "Since 1947"}).markup)
    assert steps[-1] == "1947"
    assert all("," not in step for step in steps)


def test_a_number_the_count_cannot_reproduce_stays_as_written():
    for headline in ("Mission 007", "Uptime 99,9%", "1,00,000 villages"):
        markup = render({"layout": "big_stat", "headline": headline}).markup
        assert "typo-count-step" not in markup, headline
        assert html.escape(headline) in markup or headline in markup
