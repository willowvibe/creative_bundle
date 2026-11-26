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


# --- icons -----------------------------------------------------------------


def test_big_stat_renders_a_vendored_icon_above_the_headline():
    result = render({"layout": "big_stat", "headline": "₹24", "icon": "wheat"})
    assert result.markup.index("typo-icon") < result.markup.index("typo-headline")
    assert "<svg" in result.markup and 'stroke="currentColor"' in result.markup
    assert "<!--" not in result.markup  # the licence comment stays on disk
    assert "tp_icon_1" in result.css


def test_icon_pops_in_with_the_headline_cue():
    # A lone item cued after 0.5 s is pulled to 0.4 s (no blank opening).
    words = [{"word": "gas", "start": 1.5, "end": 1.9}]
    result = render(
        {"layout": "big_stat", "headline": "₹24", "icon": "flame", "cue": "gas"},
        words=words,
    )
    assert 'style="animation-delay:0.400s;--d:0.400s"' in result.markup


def test_first_revealed_item_moves_to_0_4s_and_later_items_keep_their_cue():
    words = [
        {"word": "gas", "start": 2.0, "end": 2.3},
        {"word": "staff", "start": 3.0, "end": 3.3},
    ]
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Gas", "value": 5, "cue": "gas"},
                {"label": "Staff", "value": 3, "cue": "staff"},
            ],
        },
        words=words,
        duration=6.0,
    )
    assert 'class="typo-bar-row" style="animation-delay:0.400s"' in result.markup
    assert 'class="typo-bar-row" style="animation-delay:3.000s"' in result.markup
    assert "2.000s" not in result.markup


def test_big_stat_headline_opens_at_0_4s_when_its_cue_is_late():
    words = [
        {"word": "gas", "start": 2.0, "end": 2.3},
        {"word": "staff", "start": 3.0, "end": 3.3},
    ]
    result = render(
        {"layout": "big_stat", "headline": "₹24", "cue": "gas", "sub": "largest ever"},
        words=words,
        duration=8.0,
    )
    headline = float(
        re.search(r'class="typo-line" style="animation-delay:([\d.]+)s', result.markup).group(1)
    )
    assert headline == pytest.approx(0.4)
    assert _count_delays(result.markup) and all(
        delay == pytest.approx(0.4) for delay in _count_delays(result.markup)
    )
    # Only the opening headline moves; its uncued sub keeps the normal stagger.
    sub = float(
        re.search(
            r'class="typo-line typo-sub" style="animation-delay:([\d.]+)s', result.markup
        ).group(1)
    )
    assert sub < 2.0


def test_sequence_first_step_opens_at_0_4s_and_a_later_cue_is_unchanged():
    words = [
        {"word": "find", "start": 2.0, "end": 2.3},
        {"word": "watch", "start": 3.0, "end": 3.3},
    ]
    result = render(
        {"layout": "sequence", "steps": ["Find", "Watch", "Fix"]},
        words=words,
        duration=8.0,
    )
    delays = [
        float(delay)
        for delay in re.findall(r'class="typo-step" style="animation-delay:([\d.]+)s', result.markup)
    ]
    assert delays[0] == pytest.approx(0.4)
    assert delays[1] == pytest.approx(3.0)
    assert "2.000s" not in result.markup


def test_donut_first_slice_opens_at_0_4s_and_a_later_cue_is_unchanged():
    words = [
        {"word": "rent", "start": 2.0, "end": 2.3},
        {"word": "staff", "start": 3.0, "end": 3.3},
    ]
    result = render(DONUT, words=words, duration=8.0)
    delays = [
        float(delay)
        for delay in re.findall(r'typo-chart-dn" style="animation-delay:([\d.]+)s', result.markup)
    ]
    assert delays[0] == pytest.approx(0.4)  # ring and centre value
    assert delays[1] == pytest.approx(0.4)  # first slice and legend row
    assert delays[2] == pytest.approx(3.0)  # later slice keeps its cue
    assert "2.000s" not in result.markup


def test_chart_container_is_sized_to_76cqh():
    css = render(WATERFALL).css
    assert "76cqh" in css and "62cqh" not in css


def test_donut_container_uses_the_larger_76cqh_rule():
    css = render(DONUT).css
    rule = css.split(".typo-1 .typo-chart-dn {", 1)[1].split("}", 1)[0]
    assert "76cqh" in rule and "78cqh" not in rule


def test_bar_value_unit_template_substitutes_the_value():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "A", "value": 40, "unit": "₹{v}/రోజు"},
                {"label": "B", "value": 20, "unit": "₹{v}/రోజు"},
            ],
        }
    )
    assert ">₹40/రోజు<" in result.markup and ">₹20/రోజు<" in result.markup
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "A", "value": 1.13, "unit": "₹{v} L"},
                {"label": "B", "value": 2, "unit": "₹{v} L"},
            ],
        }
    )
    assert ">₹1.13 L<" in result.markup
    assert "white-space: nowrap" in result.css.split(".typo-bar-value {", 1)[1].split("}", 1)[0]


def test_bar_value_without_template_appends_the_unit():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "A", "value": 40, "unit": "%"},
                {"label": "B", "value": 20, "unit": "%"},
            ],
        }
    )
    assert ">40%<" in result.markup


def test_bar_compare_bar_can_carry_an_icon():
    result = render(
        {
            "layout": "bar_compare",
            "bars": [
                {"label": "Gas", "value": 5, "icon": "flame"},
                {"label": "Staff", "value": 3},
            ],
        }
    )
    assert result.markup.count('class="typo-icon typo-icon-bar"') == 1


def test_unknown_icon_lists_the_available_names():
    with pytest.raises(SceneRenderError, match="available icons:.*wheat"):
        render({"layout": "big_stat", "headline": "1", "icon": "rocket"})
    with pytest.raises(SceneRenderError, match="unknown icon"):
        render(
            {
                "layout": "bar_compare",
                "bars": [{"label": "A", "value": 1, "icon": "../x"}, {"label": "B", "value": 2}],
            }
        )


def test_every_vendored_icon_loads_and_ships_with_its_licence():
    from video_editor_core.icons import ICON_DIR, icon_names, icon_svg

    names = icon_names()
    assert {"wheat", "flame", "store", "users", "package", "zap", "file-badge", "clock"} <= set(
        names
    )
    assert (ICON_DIR / "LICENSE").is_file()
    assert all(icon_svg(name).startswith("<svg") for name in names)


# --- waterfall ---------------------------------------------------------------

WATERFALL = {
    "layout": "waterfall",
    "unit": "₹",
    "start": {"label": "Sales", "value": 152000},
    "steps": [
        {"label": "Rent", "value": -40000},
        {"label": "Staff", "value": -52000},
        {"label": "Tips", "value": 5000},
    ],
    "end": {"label": "Profit"},
}


def test_waterfall_draws_one_layer_per_bar_with_indian_grouping():
    result = render(WATERFALL)
    assert result.markup.count("typo-wf-bar ") == 5
    assert "₹1,52,000" in result.markup
    assert "−₹40,000" in result.markup
    assert "+₹5,000" in result.markup
    assert "₹65,000" in result.markup  # start + steps, computed
    assert result.markup.count("typo-wf-conn") == 4
    assert result.markup.count("is-down") == 2
    assert "tp_wfgrow_1" in result.css and "container-type: size" in result.css


def test_waterfall_escapes_and_renders_telugu_labels():
    props = {
        **WATERFALL,
        "start": {"label": "అమ్మకాలు <b>", "value": 100},
        "steps": [{"label": "అద్దె", "value": -30}, {"label": "జీతాలు", "value": 10}],
    }
    markup = render(props).markup
    assert "అద్దె" in markup and "&lt;b&gt;" in markup


def test_waterfall_step_cues_default_to_the_label_and_can_be_switched_off():
    props = {
        **WATERFALL,
        "steps": [
            {"label": "Rent", "value": -40000, "cue": "the rent"},
            {"label": "Staff", "value": -52000, "cue": False},
            {"label": "Tips", "value": 5000},
        ],
    }
    cues = scene_cues({"id": "s", "props": props})
    assert cues == {"item_1": "the rent", "item_3": "Tips"}


def test_waterfall_step_grows_when_its_label_is_spoken():
    words = [
        {"word": "rent", "start": 1.2, "end": 1.5},
        {"word": "staff", "start": 2.2, "end": 2.5},
    ]
    markup = render(WATERFALL, words=words, duration=6.0).markup
    delays = re.findall(
        r'class="typo-chart typo-chart-wf" style="animation-delay:([\d.]+)s;--d', markup
    )
    assert float(delays[1]) == pytest.approx(1.2)
    assert float(delays[2]) == pytest.approx(2.2)


def test_waterfall_negative_bar_hangs_down_from_the_running_level():
    markup = render(WATERFALL).markup
    bars = re.findall(
        r'typo-wf-bar (is-\w+)" style="[^"]*top:([\d.]+)%;height:([\d.]+)%;'
        r"transform-origin:50% (\d+%)",
        markup,
    )
    kinds = {kind: origin for kind, _, _, origin in bars}
    assert kinds["is-down"] == "0%"  # grows downward from the level above
    assert kinds["is-total"] == "100%"  # grows up from the baseline
    tops = [float(top) for _, top, _, _ in bars]
    assert tops[0] == pytest.approx(tops[1])


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"steps": [{"label": "A", "value": 1}]}, "2-8"),
        ({"steps": [{"label": "A", "value": 1}] * 9}, "2-8"),
        ({"start": {"label": "S"}}, "numeric `value`"),
        ({"start": {"label": "", "value": 1}}, "non-empty `label`"),
        ({"steps": [{"label": "A", "value": "x"}, {"label": "B", "value": 1}]}, "must be a number"),
        ({"end": {"value": 5}}, "non-empty `label`"),
        ({"end": {"label": "P", "value": 1}}, "does not equal start \\+ steps"),
    ],
)
def test_waterfall_rejects_bad_props(patch, message):
    with pytest.raises(SceneRenderError, match=message):
        render({**WATERFALL, **patch})


def test_waterfall_end_error_allows_a_matching_value_and_rounds_to_the_rupee():
    from video_editor_core.typographic_scenes import waterfall_end_error

    assert waterfall_end_error({**WATERFALL, "end": {"label": "P", "value": 65000}}) is None
    assert waterfall_end_error(WATERFALL) is None
    assert waterfall_end_error({**WATERFALL, "end": {"label": "P", "value": 65001}})


def test_silencing_covers_waterfall_steps():
    props = silence_unspoken_typographic_cues(
        WATERFALL, [{"word": "rent", "start": 0.5, "end": 0.9}]
    )
    cues = scene_cues({"id": "s", "props": props})
    assert cues == {"item_1": "Rent"}


def test_indian_grouping_and_signs():
    from video_editor_core.typographic_scenes import format_amount, indian_number

    assert indian_number(12345678) == "1,23,45,678"
    assert indian_number(999) == "999"
    assert indian_number(1234.5, 1) == "1,234.5"
    assert format_amount(-1500, "₹") == "−₹1,500"
    assert format_amount(12, "%", signed=True) == "+12%"


# --- line ----------------------------------------------------------------------

LINE = {
    "layout": "line",
    "slope": 60,
    "intercept": -9120,
    "x_range": [0, 250],
    "unit": "₹",
    "x_label": "Tiffins a day",
    "y_label": "Profit",
    "mark": {"x": 152, "label": "Break-even"},
    "dots": [{"x": 90, "label": "Today"}, {"x": 152, "label": "Even"}, {"x": 220, "label": "Goal"}],
}


def test_line_draws_axes_a_zero_rule_the_dashed_mark_and_a_dash_reveal():
    result = render(LINE)
    assert "typo-ln-zero" in result.markup  # profit crosses zero inside the range
    assert 'pathLength="1"' in result.markup
    assert "typo-ln-mark" in result.markup and "Break-even" in result.markup
    assert "Tiffins a day" in result.markup and "Profit" in result.markup
    assert "tp_lndraw_1" in result.css and "stroke-dashoffset" in result.css
    assert "₹4,080" in result.markup  # dot at x=220: 60*220-9120, Indian grouping
    assert "−₹10,320" in result.markup  # padded y axis floor


def test_line_follows_a_polyline_and_skips_the_zero_rule_when_all_positive():
    result = render(
        {
            "layout": "line",
            "points": [[0, 2], [1, 5], [2, 4], [3, 9]],
            "dots": [{"x": 2, "label": "B"}],
        }
    )
    assert result.markup.count("M") >= 1 and "L" in result.markup
    assert "typo-ln-zero" not in result.markup


def test_each_following_dot_slides_from_the_previous_dots_x():
    result = render(LINE)
    assert "tp_ln_dot_1_0" not in result.css  # the first dot just appears
    assert "@keyframes tp_ln_dot_1_1" in result.css and "@keyframes tp_ln_dot_1_2" in result.css
    first_stop = re.search(r"tp_ln_dot_1_1 \{ 0\.000% \{ left: ([\d.]+)%", result.css)
    # x=90 maps to the same percentage the first dot sits at
    first_dot = re.search(r'typo-ln-dot" style="left: ([\d.]+)%', result.markup)
    assert float(first_stop.group(1)) == pytest.approx(float(first_dot.group(1)), abs=0.01)
    assert result.markup.count("typo-ln-dots is-passed") == 2  # all but the last hide


def test_a_slide_passes_through_polyline_vertices():
    result = render(
        {
            "layout": "line",
            "points": [[0, 0], [1, 10], [2, 0], [3, 10]],
            "dots": [{"x": 0, "label": "A"}, {"x": 3, "label": "B"}],
        }
    )
    stops = re.search(r"@keyframes tp_ln_dot_1_1 \{([^}]*\}[^}]*\}[^}]*\}[^}]*\})", result.css)
    assert stops.group(1).count("%") >= 4  # 0%, 33%, 67%, 100%


def test_line_cues_default_to_labels_and_follow_the_narration():
    words = [
        {"word": "today", "start": 1.0, "end": 1.3},
        {"word": "goal", "start": 3.0, "end": 3.3},
    ]
    assert scene_cues({"id": "s", "props": LINE}) == {
        "item_2": "Break-even",
        "item_3": "Today",
        "item_4": "Even",
        "item_5": "Goal",
    }
    markup = render(LINE, words=words, duration=8.0).markup
    delays = re.findall(r'typo-chart-ln" style="animation-delay:([\d.]+)s', markup)
    assert float(delays[3]) == pytest.approx(1.0)
    assert float(delays[5]) == pytest.approx(3.0)
    assert "--n:3.000s" in markup  # the middle dot hands over to the last


def test_silencing_covers_line_mark_and_dots():
    props = silence_unspoken_typographic_cues(LINE, [{"word": "today", "start": 1, "end": 2}])
    assert scene_cues({"id": "s", "props": props}) == {"item_3": "Today"}


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"slope": None, "intercept": None}, "needs `slope`"),
        ({"points": [[0, 1], [1, 2], [2, 3]]}, "not both"),
        ({"x_range": None, "mark": None, "dots": None}, "needs an `x_range`"),
        ({"x_range": [5, 5]}, "low < high"),
        ({"mark": {"x": 152}}, "non-empty `label`"),
        ({"mark": {"x": 999, "label": "far"}}, "outside x_range"),
        ({"dots": []}, "1-3"),
        ({"dots": [{"x": 1, "label": "a"}] * 4}, "1-3"),
        ({"dots": [{"x": "z", "label": "a"}]}, "must be a number"),
    ],
)
def test_line_rejects_bad_props(patch, message):
    with pytest.raises(SceneRenderError, match=message):
        render({**LINE, **patch})


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ([[0, 1], [1, 2]], "3-30"),
        ([[0, 1], [0, 2], [1, 3]], "strictly increasing"),
        ([[0, 1], [1], [2, 3]], "\\[x, y\\] pair"),
    ],
)
def test_line_rejects_bad_points(points, message):
    with pytest.raises(SceneRenderError, match=message):
        render({"layout": "line", "points": points})


# --- donut ---------------------------------------------------------------------

DONUT = {
    "layout": "donut",
    "unit": "₹",
    "parts": [
        {"label": "Rent", "value": 40000},
        {"label": "Staff", "value": 52000},
        {"label": "Gas", "value": 8000},
    ],
    "center": {"value": 100000, "label": "monthly cost"},
}


def test_donut_sweeps_one_segment_per_part_with_a_legend_row_each():
    result = render(DONUT)
    assert result.markup.count('class="typo-dn-seg"') == 3
    assert result.markup.count('class="typo-dn-row"') == 3
    assert "₹40,000" in result.markup and "₹52,000" in result.markup
    assert "tp_dnsweep_1" in result.css and "stroke-dasharray" in result.css
    # arcs run in order: each starts where the previous one ended
    offsets = [float(v) for v in re.findall(r"stroke-dashoffset:(-?[\d.]+)", result.markup)]
    assert offsets == pytest.approx([0.0, -0.4, -0.92])
    fractions = [float(v) for v in re.findall(r"stroke-dasharray:([\d.]+) 1", result.markup)]
    assert sum(fractions) == pytest.approx(1.0 - 3 * 0.006, abs=0.001)


def test_donut_centre_value_counts_up_like_a_big_stat():
    result = render(
        {
            **DONUT,
            "center": {"value": 2400, "label": "total"},
            "parts": [{"label": "A", "value": 1400}, {"label": "B", "value": 1000}],
        }
    )
    assert result.markup.count('class="typo-count-step"') >= 2
    assert "monthly" not in result.markup and "total" in result.markup
    assert "tp_count_1_0" in result.css


def test_donut_segments_start_on_their_cues_in_order():
    words = [
        {"word": "rent", "start": 1.0, "end": 1.3},
        {"word": "staff", "start": 2.5, "end": 2.8},
    ]
    markup = render(DONUT, words=words, duration=8.0).markup
    delays = re.findall(r'typo-chart-dn" style="animation-delay:([\d.]+)s', markup)
    # The first slice opens the scene at 0.4 s so the donut is never blank;
    # the later slice keeps its own cue.
    assert float(delays[1]) == pytest.approx(0.4)
    assert float(delays[2]) == pytest.approx(2.5)
    assert scene_cues({"id": "s", "props": DONUT}) == {
        "item_1": "Rent",
        "item_2": "Staff",
        "item_3": "Gas",
    }


def test_donut_escapes_and_renders_telugu_labels():
    props = {
        **DONUT,
        "parts": [{"label": "అద్దె <i>", "value": 1}, {"label": "జీతాలు", "value": 2}],
        "center": None,
    }
    markup = render(props).markup
    assert "అద్దె &lt;i&gt;" in markup and "జీతాలు" in markup


def test_donut_centre_may_be_a_label_only_and_defaults_to_nothing_without_center():
    assert "typo-dn-center" not in render({**DONUT, "center": None}).markup
    assert "typo-dn-value" not in render({**DONUT, "center": {"label": "costs"}}).markup


def test_silencing_covers_donut_parts():
    props = silence_unspoken_typographic_cues(DONUT, [{"word": "rent", "start": 0.5, "end": 0.9}])
    assert scene_cues({"id": "s", "props": props}) == {"item_1": "Rent"}


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"parts": [{"label": "A", "value": 1}]}, "2-6"),
        ({"parts": [{"label": "A", "value": 1}] * 7}, "2-6"),
        ({"parts": [{"label": "A", "value": 0}, {"label": "B", "value": 1}]}, "positive"),
        ({"parts": [{"label": "A", "value": -3}, {"label": "B", "value": 1}]}, "positive"),
        ({"parts": [{"label": "", "value": 1}, {"label": "B", "value": 1}]}, "non-empty `label`"),
        ({"parts": [{"label": "A"}, {"label": "B", "value": 1}]}, "numeric `value`"),
        ({"center": {"value": 99999, "label": "x"}}, "does not equal the sum of parts"),
        ({"center": {}}, "needs a `value` or a `label`"),
        ({"center": "total"}, "must be an object"),
    ],
)
def test_donut_rejects_bad_props(patch, message):
    with pytest.raises(SceneRenderError, match=message):
        render({**DONUT, **patch})


def test_donut_centre_check_uses_the_shown_decimals():
    from video_editor_core.typographic_scenes import donut_center_error

    props = {"parts": [{"label": "A", "value": 0.1}, {"label": "B", "value": 0.2}]}
    assert donut_center_error({**props, "center": {"value": 0.3}}) is None
    assert donut_center_error({**props, "center": {"value": 0.4}})
    assert donut_center_error(props) is None
