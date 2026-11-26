"""Tests for the map scene renderer and the geometry it draws from."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from video_editor_core.designs import load_design
from video_editor_maps.geo import (
    GeoError,
    View,
    countries,
    find_country,
    find_region,
    find_river,
    parse_bounds,
    parse_position,
    view_for,
)
from video_editor_maps.scenes import (
    _LANDSCAPE,
    _PORTRAIT,
    TITLE_EYEBROW_MARGIN,
    TITLE_HEADLINE_MARGIN,
    TITLE_LINE_HEIGHT,
    TITLE_MAX_LINES,
    TITLE_SUBTITLE_MARGIN,
    MapSceneError,
    _camera_transform,
    _title_max_bottom,
    _track_positions,
    _zoom_view,
    _zooms,
    map_scene_cues,
    map_scene_motion_problems,
    map_scene_motion_warnings,
    render_map,
    silence_unspoken_map_cues,
)

DESIGN = load_design("paper_editorial")


def render(props: dict, *, duration: float = 6.0, words=None, index: int = 1):
    return render_map(
        {"id": "scene_1", "type": "map", "props": props},
        DESIGN,
        index=index,
        duration=duration,
        width=1920,
        height=1080,
        words=words,
    )


def test_a_map_scene_draws_the_subject_the_pin_and_its_label():
    result = render(
        {
            "country": "China",
            "focus": "116.4,39.9",
            "label": "BEIJING",
            "eyebrow": "Chapter 01",
            "headline": "The Middle Kingdom",
            "subtitle": "East Asia",
        }
    )
    assert 'class="map-subject"' in result.markup
    assert "BEIJING" in result.markup
    assert "The Middle Kingdom" in result.markup
    # The subject is filled in the design's highlight colour, not the ghost.
    assert "#59608A" in result.css
    assert "map-clip-1" in result.markup


def test_the_eyebrow_text_is_never_read_as_a_colour():
    """``eyebrow`` is display copy; a quote in it must not reach the CSS.

    The renderer once treated the prop as a colour override, emitting
    ``color:Southeast Asia's...``. The apostrophe opened an unterminated CSS
    string, so Chrome dropped the rest of the stylesheet and every later plate
    rendered unstyled.
    """
    result = render({"country": "Malaysia", "eyebrow": "Southeast Asia's gate"})
    assert "Southeast Asia" not in result.css
    assert "#B8862F" in result.css


def test_eyebrow_color_is_the_separate_colour_override():
    result = render({"country": "Malaysia", "eyebrow": "Chapter 01", "eyebrow_color": "#C8102E"})
    assert "Chapter 01" in result.markup
    assert "color:#C8102E" in result.css


def test_the_map_is_clipped_to_its_panel():
    result = render({"country": "India"})
    assert re.search(r'<clipPath id="map-clip-1"><rect x="150\.0" y="158\.0"', result.markup)


def test_neighbours_are_ghosted_and_the_subject_is_not_duplicated():
    result = render({"country": "China"})
    ghosts = re.search(r'<g class="map-ghost"[^>]*>(.*?)</g>', result.markup)
    subject = re.search(r'<g class="map-subject"[^>]*>(.*?)</g>', result.markup)
    assert ghosts is not None and subject is not None
    assert len(ghosts.group(1)) > len(subject.group(1))


def test_callouts_become_numbered_cards():
    result = render({"country": "China", "callouts": ["BUILT IN 495 CE", "UNESCO HERITAGE"]})
    assert "01" in result.markup
    assert "02" in result.markup
    assert "BUILT IN 495 CE" in result.markup
    assert "UNESCO HERITAGE" in result.markup


def test_a_callout_reveals_on_the_word_that_earns_it():
    words = [
        {"word": "built", "start": 1.0, "end": 1.2},
        {"word": "in", "start": 1.2, "end": 1.3},
        {"word": "495", "start": 1.3, "end": 1.5},
    ]
    result = render(
        {"country": "China", "callouts": [{"text": "495 CE", "cue": "495"}]},
        words=words,
    )
    delays = [float(value) for value in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert delays and delays[0] == pytest.approx(1.3, abs=0.05)


def test_without_a_spoken_cue_cards_fall_in_on_a_bounded_stagger():
    result = render({"country": "China", "callouts": ["one", "two", "three"]}, duration=6.0)
    delays = [float(value) for value in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert len(delays) == 3
    assert delays == sorted(delays)
    assert max(delays) < 6.0


def test_a_focus_outside_the_window_drops_the_pin():
    result = render({"country": "China", "focus": "-60,-30", "label": "AWAY"})
    assert "map-pin-point" in result.css  # the rule exists
    assert "AWAY" not in result.markup


def _subject_width(markup: str) -> float:
    group = re.search(r'<g class="map-subject"[^>]*>(.*?)</g>', markup)
    assert group is not None
    numbers = [float(value) for value in re.findall(r"-?\d+\.\d+", group.group(1))]
    xs = numbers[0::2]
    return max(xs) - min(xs)


def test_a_custom_bbox_widens_the_window():
    default = render({"country": "China", "bbox": "73,18,135,54"})
    widened = render({"country": "China", "bbox": "40,0,160,70"})
    # A wider window draws the same country smaller inside the same panel.
    assert _subject_width(widened.markup) < _subject_width(default.markup)


def test_unknown_or_missing_country_fails_closed():
    with pytest.raises(MapSceneError, match="country"):
        render({"headline": "Nowhere"})
    with pytest.raises(MapSceneError, match="unknown country"):
        render({"country": "Atlantis"})


def test_a_malformed_focus_is_an_error_not_a_silent_centre():
    with pytest.raises(MapSceneError, match="focus"):
        render({"country": "China", "focus": "north"})


def test_cues_are_read_without_a_design():
    assert map_scene_cues(
        {"props": {"country": "China", "callouts": ["Gold", {"text": "Silk", "cue": "silk road"}]}}
    ) == {"item_0": "Gold", "item_1": "silk road"}


def test_a_callout_can_opt_out_of_its_default_cue():
    assert (
        map_scene_cues(
            {"props": {"country": "Japan", "callouts": [{"text": "Tokyo", "cue": False}]}}
        )
        == {}
    )


def test_silencing_turns_off_only_callouts_the_narration_never_says():
    narration = "Tokyo sits on the bay."
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_map_cues(
        {"country": "Japan", "callouts": ["Tokyo", {"text": "Kyoto"}]}, words
    )
    assert out["callouts"] == ["Tokyo", {"text": "Kyoto", "cue": False}]


def test_colours_come_from_the_design_then_the_scene():
    design_colour = render({"country": "China"})
    assert "#59608A" in design_colour.css
    overridden = render({"country": "China", "highlight": "#C8102E"})
    assert "#C8102E" in overridden.css


# --- markers and routes ---------------------------------------------------


def test_markers_draw_a_pin_and_a_label_for_every_named_place():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "markers": [
                {"text": "Penang", "lon": 100.33, "lat": 5.41},
                {"text": "Malacca", "lon": 102.25, "lat": 2.19},
            ],
        }
    )
    assert result.markup.count('class="map-marker"') == 2
    assert result.markup.count('class="map-place') == 2
    assert "Penang" in result.markup and "Malacca" in result.markup


def test_a_marker_reveals_on_the_word_that_names_it():
    words = [
        {"word": "then", "start": 0.4, "end": 0.6},
        {"word": "Malacca", "start": 2.0, "end": 2.4},
    ]
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        },
        words=words,
    )
    # The pin and its label share one track, so both wait for the spoken name.
    assert result.markup.count('data-camera-track="marker_0"') == 2
    assert "animation: map_marker_1 0.490s ease-out 2.000s both;" in result.css


def test_a_marker_can_opt_out_of_its_default_cue():
    assert (
        map_scene_cues(
            {
                "props": {
                    "country": "Malaysia",
                    "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19, "cue": False}],
                }
            }
        )
        == {}
    )


def test_silencing_turns_off_markers_the_narration_never_names():
    narration = "Malacca grew rich."
    words = [{"word": token, "start": 0.0, "end": 0.0} for token in narration.split()]
    out = silence_unspoken_map_cues(
        {
            "country": "Malaysia",
            "markers": [
                {"text": "Malacca", "lon": 102.25, "lat": 2.19},
                {"text": "Singapore", "lon": 103.82, "lat": 1.35},
            ],
            "route": [[102.25, 2.19], [103.82, 1.35]],
            "route_cue": "the strait",
        },
        words,
    )
    assert out["markers"][0] == {"text": "Malacca", "lon": 102.25, "lat": 2.19}
    assert out["markers"][1]["cue"] is False
    assert out["route_cue"] is False


def test_a_route_draws_between_its_points_with_a_matching_dash():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "markers": [
                {"text": "Penang", "lon": 100.33, "lat": 5.41},
                {"text": "Singapore", "lon": 103.82, "lat": 1.35},
            ],
            "route": [[100.33, 5.41], [102.25, 2.19], [103.82, 1.35]],
        }
    )
    assert 'class="map-route"' in result.markup
    (dash,) = re.findall(r'stroke-dasharray="([\d.]+)"', result.markup)
    assert float(dash) > 0
    # The hidden offset equals the dash, so the keyframe can reveal the path.
    assert f"stroke-dashoffset: var(--map-route-draw-1, {dash});" in result.css
    assert "map_route_1" in result.css


def test_a_route_can_wait_for_its_cue():
    words = [{"word": "sailed", "start": 1.5, "end": 1.8}]
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "route": [[100.33, 5.41], [103.82, 1.35]],
            "route_cue": "sailed",
        },
        words=words,
    )
    route = re.search(r'<path class="map-route"[^>]*>', result.markup)
    assert route is not None and "animation-delay" not in route.group(0)
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    draw = re.search(r"map_route_1 [\d.]+s \S+ ([\d.]+)s both", ghost.group(1))
    assert draw is not None and float(draw.group(1)) == pytest.approx(1.5, abs=0.01)


def test_two_close_labels_flip_to_opposite_sides():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "60,-30,150,30",
            "markers": [
                {"text": "Malacca", "lon": 102.25, "lat": 2.19},
                {"text": "Singapore", "lon": 103.82, "lat": 1.35},
            ],
        }
    )
    assert result.markup.count(" is-left") == 1
    assert result.markup.count(" is-right") == 1


def test_a_marker_outside_the_window_fails_closed():
    with pytest.raises(MapSceneError, match="outside the map window"):
        render({"country": "China", "markers": [{"text": "Away", "lon": -60.0, "lat": -30.0}]})


def test_a_malformed_marker_or_route_fails_closed():
    with pytest.raises(MapSceneError, match="marker"):
        render({"country": "China", "markers": [{"text": "No point"}]})
    with pytest.raises(MapSceneError, match="at least two points"):
        render({"country": "China", "route": [[116.4, 39.9]]})
    with pytest.raises(MapSceneError, match="route point"):
        render({"country": "China", "route": [[116.4, 39.9], "north"]})


def test_cue_qa_fails_a_marker_the_narration_never_names():
    from video_editor_pipelines.composition_qa import cue_problems

    plan = {
        "scenes": [
            {
                "id": "geo",
                "type": "map",
                "props": {
                    "country": "Malaysia",
                    "markers": [
                        {"text": "Malacca", "lon": 102.25, "lat": 2.19},
                        {"text": "Singapore", "lon": 103.82, "lat": 1.35},
                    ],
                    "word_times": [{"word": "malacca", "start": 0.5, "end": 0.9}],
                },
            }
        ]
    }
    assert cue_problems(plan) == ["geo: 'Singapore'"]


# --- portrait plate -------------------------------------------------------


def render_portrait(
    props: dict,
    *,
    width: int = 1080,
    height: int = 1920,
    duration: float = 6.0,
    words=None,
    index: int = 1,
):
    return render_map(
        {"id": "scene_1", "type": "map", "props": props},
        DESIGN,
        index=index,
        duration=duration,
        width=width,
        height=height,
        words=words,
    )


def test_the_portrait_map_sits_below_the_worst_case_title_and_above_the_cards():
    """The title block is clamped, but its geometry still has to fit."""
    title_bottom = (
        _PORTRAIT.title[1]
        + _PORTRAIT.rule[1]
        + TITLE_EYEBROW_MARGIN
        + _PORTRAIT.eyebrow * TITLE_LINE_HEIGHT
        + TITLE_HEADLINE_MARGIN
        + _PORTRAIT.headline * TITLE_MAX_LINES
        + TITLE_SUBTITLE_MARGIN
        + _PORTRAIT.subtitle * TITLE_LINE_HEIGHT * TITLE_MAX_LINES
    )
    assert _title_max_bottom(_PORTRAIT) == pytest.approx(title_bottom)
    _, map_top, _, map_height = _PORTRAIT.map_frame
    cards_top = _PORTRAIT.cards[1]
    assert map_top - title_bottom >= 24.0
    assert cards_top - (map_top + map_height) >= 40.0


def test_the_title_is_clamped_to_two_lines():
    result = render_portrait(
        {
            "country": "China",
            "headline": "A headline long enough to wrap onto a second line",
            "subtitle": "A subtitle long enough to wrap onto a second line too",
        }
    )
    assert result.css.count("-webkit-line-clamp:2") == 2
    assert result.css.count("overflow:hidden") >= 2


def test_the_landscape_title_stays_below_the_map_with_no_overlap():
    _, map_top, _, map_height = _LANDSCAPE.map_frame
    assert map_top + map_height <= _LANDSCAPE.title[1]


def test_a_portrait_map_stacks_the_title_above_the_map_and_the_cards_below_it():
    result = render_portrait(
        {
            "country": "China",
            "focus": "116.4,39.9",
            "label": "BEIJING",
            "eyebrow": "Chapter 01",
            "headline": "The Middle Kingdom",
            "callouts": ["BUILT IN 495 CE", "UNESCO HERITAGE"],
        }
    )
    # The plate is the portrait one, drawn at 1:1 in a 1080x1920 frame.
    assert 'width:1080.0px;height:1920.0px"' in result.markup
    # The map window is the portrait panel, and the clip agrees with it.
    assert "left:72.0px; top:516.0px;" in result.css
    assert "width:936.0px; height:650.0px;" in result.css
    assert '<rect x="72.0" y="516.0" width="936.0" height="650.0"' in result.markup
    # Title above the map; cards below it.
    assert "top:200.0px; width:936.0px" in result.css
    assert "top:1230.0px; width:936.0px" in result.css
    assert "font-size:72.0px" in result.css  # portrait headline
    # Every reading size clears the craft check's 28 px floor at 1080.
    assert "font-size:28.0px" in result.css  # portrait place label
    assert "BEIJING" in result.markup


def test_a_landscape_frame_keeps_the_side_by_side_plate():
    landscape = render({"country": "China", "callouts": ["one"]})
    portrait = render_portrait({"country": "China", "callouts": ["one"]})
    assert "left:1148.0px" in landscape.css  # cards in the right column
    assert "left:1148.0px" not in portrait.css
    assert "font-size:88.0px" in landscape.css  # landscape headline
    assert "font-size:72.0px" in portrait.css


def test_a_portrait_that_is_not_nine_by_sixteen_letterboxes_onto_paper():
    """4:5 contains the portrait plate at one uniform scale, never stretched."""
    result = render_portrait({"country": "China", "headline": "Contained"}, width=1080, height=1350)
    assert "left:160.3px;top:0.0px;width:759.4px;height:1350.0px" in result.markup
    # Type scales with the plate, so the layout does not spill out of it.
    assert "font-size:50.6px" in result.css  # 72 * 0.703125


def test_a_square_frame_takes_the_portrait_plate():
    result = render_portrait({"country": "China", "headline": "Square"}, width=1080, height=1080)
    assert "width:607.5px;height:1080.0px" in result.markup
    assert "font-size:40.5px" in result.css  # 72 * 0.5625


def test_portrait_callout_cues_still_reveal_on_their_word():
    words = [
        {"word": "built", "start": 1.0, "end": 1.2},
        {"word": "in", "start": 1.2, "end": 1.3},
        {"word": "495", "start": 1.3, "end": 1.5},
    ]
    result = render_portrait(
        {"country": "China", "callouts": [{"text": "495 CE", "cue": "495"}]},
        words=words,
    )
    delays = [float(value) for value in re.findall(r"animation-delay:([\d.]+)s", result.markup)]
    assert delays and delays[0] == pytest.approx(1.3, abs=0.05)


# --- geometry -------------------------------------------------------------


def test_projection_uses_one_unit_on_both_axes():
    """Degrees on x with radians on y collapses the map to a flat line."""
    view = View(
        frame=(0.0, 0.0, 100.0, 100.0), bounds=(0.0, 0.0, 10.0, 10.0), projection="equirectangular"
    )
    left, bottom = view.project(0.0, 0.0)
    right, top = view.project(10.0, 10.0)
    assert right - left == pytest.approx(100.0)
    assert bottom - top == pytest.approx(100.0)


def test_view_for_keeps_the_subject_inside_the_frame():
    subject = find_country("China")
    frame = (150.0, 158.0, 950.0, 604.0)
    view = view_for(subject, frame, padding=0.22)
    west, south, east, north = subject.bounds()
    for lon, lat in ((west, south), (east, south), (west, north), (east, north)):
        x, y = view.project(lon, lat)
        assert frame[0] <= x <= frame[0] + frame[2]
        assert frame[1] <= y <= frame[1] + frame[3]


def test_the_bundled_geometry_loads_every_country_once():
    loaded = countries()
    assert len(loaded) > 150
    assert len({country.name for country in loaded}) == len(loaded)


def _svg_path_points(markup: str) -> int:
    svg = re.search(r'<svg class="map-svg".*?</svg>', markup, re.S)
    assert svg is not None
    return len(re.findall(r"[ML]", svg.group(0)))


def test_the_50m_geometry_loads_every_country_once():
    loaded = countries("50m")
    assert len(loaded) == 242
    assert len({country.name for country in loaded}) == len(loaded)
    # The 50m file is a superset, so every 110m scene can still find its names.
    assert {country.name for country in countries()} <= {country.name for country in loaded}
    assert find_country("CHN", detail="50m").name == "China"


def test_a_high_zoom_draws_the_outlines_from_the_finer_detail():
    props = {
        "country": "Malaysia",
        "bbox": "95,-5,110,10",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
    }
    coarse = render(props)
    fine = render({**props, "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}]}, duration=8.0)
    camera = _keyframes(fine.css, "map_camera_1")
    factors = [
        float(value) for value in re.findall(r"scale\(([\d.]+)\)", " ".join(camera.values()))
    ]
    assert factors and max(factors) > 2.5
    assert _svg_path_points(fine.markup) > _svg_path_points(coarse.markup)


def test_a_scene_without_a_zoom_stays_on_the_110m_geometry():
    props = {
        "country": "Malaysia",
        "bbox": "95,-5,110,10",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
    }
    plain = render(props)
    # A mild zoom lands below the 2.5x threshold: still 110m, byte-identical.
    mild = render({**props, "zooms": [{"bbox": "95,-5,110,10"}]})
    factors = [
        float(value)
        for value in re.findall(
            r"scale\(([\d.]+)\)", " ".join(_keyframes(mild.css, "map_camera_1").values())
        )
    ]
    assert factors and 1.0 < max(factors) <= 2.5
    assert plain.markup == mild.markup


def test_country_lookup_accepts_name_and_iso():
    assert find_country("China").name == "China"
    assert find_country("CHN").name == "China"


def test_bad_bounds_and_positions_are_rejected():
    with pytest.raises(GeoError):
        parse_bounds("1,2,3")
    with pytest.raises(GeoError):
        parse_bounds("10,20,5,30")
    with pytest.raises(GeoError):
        parse_position("1,2,3")
    with pytest.raises(GeoError):
        parse_position("200,10")


# --- pipeline wiring ------------------------------------------------------


def test_the_adapter_compiles_a_map_scene_to_one_html_layer():
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    plan = {
        "job_id": "map_job",
        "aspect_ratio": "16:9",
        "design": "paper_editorial",
        "scenes": [
            {
                "id": "geo",
                "type": "map",
                "duration_seconds": 5.0,
                "narration": "Beijing is the capital.",
                "props": {"country": "China", "focus": "116.4,39.9", "label": "BEIJING"},
            }
        ],
    }
    composition = from_scene_plan(plan)
    assert composition.design is not None
    assert composition.canvas.width == 1920
    (scene,) = composition.scenes
    (layer,) = scene.layers
    assert layer.kind == "html"
    assert layer.preset == "map"
    assert "map-subject" in layer.content.text
    assert "map-0" in str(layer.style.get("extra_css", ""))


def test_cue_qa_fails_a_callout_the_narration_never_says():
    from video_editor_pipelines.composition_qa import cue_problems

    plan = {
        "scenes": [
            {
                "id": "geo",
                "type": "map",
                "props": {
                    "country": "China",
                    "callouts": ["BUILT IN 495 CE", {"text": "UNESCO", "cue": "world heritage"}],
                    "word_times": [
                        {"word": "built", "start": 1.0, "end": 1.1},
                        {"word": "in", "start": 1.1, "end": 1.2},
                        {"word": "495", "start": 1.2, "end": 1.4},
                        {"word": "ce", "start": 1.4, "end": 1.5},
                    ],
                },
            }
        ]
    }
    problems = cue_problems(plan)
    assert problems == ["geo: 'world heritage'"]


def test_a_zoom_pushes_the_camera_into_a_tighter_window():
    """A zoom is one uniform scale plus a translation, so a seek reproduces it."""
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    assert 'class="map-window"' in result.markup
    assert 'class="map-camera"' in result.markup
    # Base-view and target-view points are mapped by scale(k) then translate.
    # Read the factor from the camera's own keyframes: the stylesheet also
    # carries the stage drift's gentle scale, which must not stand in for it.
    camera = re.search(r"@keyframes map_camera_1 \{(.*?)\n      \}", result.css, re.S)
    assert camera is not None
    factors = [float(value) for value in re.findall(r"scale\(([\d.]+)\)", camera.group(1))]
    assert factors and max(factors) > 1.5


def test_the_camera_transform_maps_base_points_onto_target_points():
    """``p_target = k p_base + T`` is exact, not just a translation.

    Regression: comparing each view's own bounds span reads 1.0, because a
    fitted window always fills the frame; the zoom then panned without
    scaling. Two points shared by both views determine the similarity.
    """
    frame = (72.0, 470.0, 936.0, 720.0)
    base = View(frame=frame, bounds=(95.0, 0.5, 119.0, 8.0))
    target = View(frame=frame, bounds=(100.0, 1.0, 104.5, 5.5))
    factor, dx, dy = _camera_transform(base, target)
    assert factor > 4.0
    for lon, lat in ((102.25, 2.19), (95.0, 4.0), (119.0, 1.0)):
        base_x, base_y = base.project(lon, lat)
        target_x, target_y = target.project(lon, lat)
        assert factor * base_x + dx == pytest.approx(target_x, abs=0.01)
        assert factor * base_y + dy == pytest.approx(target_y, abs=0.01)


def test_track_landings_are_absolute_not_chained():
    """Each move's transform replaces the previous one, so land from the base."""
    moves = [(1.0, 2.0, 10.0, 0.0), (2.0, 4.0, 0.0, 0.0)]
    assert _track_positions(3.0, 1.0, moves) == [
        (3.0, 1.0),
        (16.0, 2.0),
        (12.0, 4.0),
    ]


def test_a_zoom_carries_pins_and_labels_without_scaling_them():
    """Only the artwork passes through the camera; place names keep their size."""
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    assert '</div></div></div><div class="map-overlay">' in result.markup
    overlay = result.markup.split('class="map-overlay">', 1)[1]
    assert 'data-camera-track="marker_0"' in overlay
    assert "map-place" in overlay
    # The overlay follows the same move: its own left/top keyframes, no scale.
    track = re.search(r"@keyframes map_track_1_marker_0 \{(.*?)\n      \}", result.css, re.S)
    assert track is not None
    frames = track.group(1)
    start = re.search(r"0% \{ left:([\d.]+)px", frames)
    end = re.search(r"100% \{ left:([\d.]+)px", frames)
    assert start and end and start.group(1) != end.group(1)
    assert "scale(" not in frames and "font-size" not in frames


# --- non-scaling artwork and the tracked focus pin (#329, #331) -----------


def _keyframes(css: str, name: str) -> dict[float, str]:
    """``{percent: declarations}`` for one ``@keyframes`` block."""
    block = re.search(rf"@keyframes {name} \{{(.*?)\n      \}}", css, re.S)
    assert block is not None, name
    return {
        float(percent): body
        for percent, body in re.findall(r"([\d.]+)% \{ (.*?) \}", block.group(1), re.S)
    }


def test_a_zoom_holds_the_route_screen_width_on_the_camera_keyframes():
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "route": [[100.33, 5.41], [102.25, 2.19]],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    camera = _keyframes(result.css, "map_camera_1")
    route = _keyframes(result.css, "map_route_width_1")
    # The route keeps every camera landing and adds its interior width stops.
    assert set(camera) <= set(route)
    landings = {
        percent: float(re.search(r"scale\(([\d.]+)\)", body).group(1))
        for percent, body in camera.items()
        if "scale(" in body
    }
    assert landings
    for percent, factor in landings.items():
        width = re.search(r"--map-route-width-1:\s*([\d.]+)", route[percent])
        assert width is not None
        # The base is 4.6 at the 1920x1080 plate's scale of 1.0.
        assert float(width.group(1)) == pytest.approx(4.6 / factor, abs=1e-3)
    # Width is linear between its own stops; the camera keeps its curve.
    for body in route.values():
        if "animation-timing-function" in body:
            assert "animation-timing-function: linear" in body
    # The width animation starts on the scene's own clock, not the draw delay.
    route_tag = re.search(r'<path class="map-route"[^>]*>', result.markup)
    assert route_tag is not None
    assert "animation-delay" not in route_tag.group(0)
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    assert "map_route_width_1 8.000s linear 0s both" in ghost.group(1)


def test_without_a_camera_move_the_route_is_not_stroke_animated():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "route": [[100.33, 5.41], [102.25, 2.19]],
        }
    )
    assert "map_route_width" not in result.css


def test_only_the_route_opts_out_of_non_scaling_strokes():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,-5,110,10",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "route": [[100.33, 5.41], [102.25, 2.19]],
        }
    )
    for group in ("map-ghost", "map-subject"):
        match = re.search(rf'<g class="{group}"[^>]*>(.*?)</g>', result.markup, re.S)
        assert match is not None
        paths = match.group(1)
        assert paths.count("<path") == paths.count('vector-effect="non-scaling-stroke"')
    graticule = re.findall(
        r'<path d="[^"]*" fill="none" stroke="[^"]*" stroke-width="1"[^>]*/>',
        result.markup,
    )
    assert graticule
    assert all('vector-effect="non-scaling-stroke"' in path for path in graticule)
    route = re.search(r'<path class="map-route"[^>]*>', result.markup)
    assert route is not None
    # Its dasharray/dashoffset draw-in is in user units, so it must not scale
    # its stroke; the renderer animates stroke-width instead.
    assert "vector-effect" not in route.group(0)


def test_the_focus_pin_is_overlay_markup_that_follows_the_camera():
    result = render(
        {
            "country": "Malaysia",
            "focus": "102.25,2.19",
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    svg = re.search(r"<svg.*?</svg>", result.markup, re.S)
    assert svg is not None
    assert "map-pin-point" not in svg.group(0)
    overlay = result.markup.split('class="map-overlay">', 1)[1]
    assert overlay.count('class="map-pin-point"') == 1
    assert 'data-camera-track="focus"' in overlay
    assert "map-pin-halo" in overlay and "map-pin-ring" in overlay and "map-pin-dot" in overlay
    # It is carried by the same track machinery as the marker pins.
    assert (
        '.map-1 [data-camera-track="focus"] { animation: map_pin_1 '
        "0.490s cubic-bezier(.2,.8,.2,1) 0.000s both, "
    ) in result.css
    track = _keyframes(result.css, "map_track_1_focus")
    start = re.search(r"left:([\d.]+)px; top:([\d.]+)px", track[0.0])
    end = re.search(r"left:([\d.]+)px; top:([\d.]+)px", track[100.0])
    assert start is not None and end is not None
    assert start.group(1) != end.group(1)
    assert "scale(" not in "".join(track.values())


def test_markers_replace_the_focus_pin_unless_the_scene_asks_for_it():
    props = {
        "country": "Malaysia",
        "bbox": "95,-5,110,10",
        "focus": "101.5,2.5",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
    }
    result = render(props)
    assert 'class="map-pin-point"' not in result.markup
    assert result.markup.count('class="map-marker"') == 1
    asked = render({**props, "focus_pin": True})
    assert asked.markup.count('class="map-pin-point"') == 1
    assert 'data-camera-track="focus"' in asked.markup


def test_a_focus_without_markers_keeps_its_pin():
    result = render({"country": "Malaysia", "bbox": "95,-5,110,10", "focus": "102.25,2.19"})
    assert result.markup.count('class="map-pin-point"') == 1


def test_the_window_fade_is_on_a_non_scaling_wrapper():
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    assert '<div class="map-window"><div class="map-fade"><div class="map-camera">' in result.markup
    # The overlay is the fade's sibling, not its child, so pins stay unmasked.
    assert '</div></div></div><div class="map-overlay">' in result.markup
    fade = re.search(r"\.map-1 \.map-fade \{[^}]*\}", result.css)
    assert fade is not None
    assert "mask-image" in fade.group(0)
    # The wrapper is positioned and sized to the map frame, and is not scaled.
    # (a scene with a zoom bbox gets the widened bbox panel: plate minus title)
    assert "left:60.0px; top:158.0px;" in fade.group(0)
    assert "width:1800.0px; height:618.0px;" in fade.group(0)
    assert "transform" not in fade.group(0)
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{[^}]*\}", result.css)
    assert ghost is not None
    assert "mask" not in ghost.group(0)


def test_the_fade_mask_is_transparent_on_every_frame_edge():
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    fade = re.search(r"\.map-1 \.map-fade \{(.*?)\}", result.css, re.S)
    assert fade is not None
    rule = fade.group(1)
    for prop in ("-webkit-mask-image", "mask-image"):
        declaration = re.search(rf"{re.escape(prop)}:(.*?);", rule, re.S)
        assert declaration is not None, prop
        value = declaration.group(1)
        assert "radial-gradient" not in value
        for direction in ("to bottom", "to right"):
            gradient = re.search(
                rf"linear-gradient\(\s*{direction},\s*transparent,"
                r"\s*#000 [\d.]+%,\s*#000 [\d.]+%,\s*transparent\s*\)",
                value,
            )
            assert gradient is not None, (prop, direction)
        assert value.count("linear-gradient") == 2
    assert "-webkit-mask-composite: source-in" in rule
    assert "mask-composite: intersect" in rule


def _cubic_bezier_ease(progress: float, timing: str) -> float:
    """A CSS timing function's eased progress at raw fraction ``progress``."""
    numbers = [float(value) for value in re.findall(r"-?[\d.]+", timing)]
    if len(numbers) != 4:
        return progress  # linear (or anything without control points)
    x1, y1, x2, y2 = numbers

    def axis(t: float, first: float, second: float) -> float:
        return 3 * (1 - t) ** 2 * t * first + 3 * (1 - t) * t**2 * second + t**3

    low, high = 0.0, 1.0
    for _ in range(40):
        middle = (low + high) / 2.0
        if axis(middle, x1, x2) < progress:
            low = middle
        else:
            high = middle
    return axis((low + high) / 2.0, y1, y2)


def _timing_of(body: str) -> str:
    match = re.search(r"animation-timing-function:\s*([^;]+);", body)
    return match.group(1).strip() if match else "linear"


def test_a_zoom_holds_the_route_screen_width_throughout_the_move():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "route": [[100.33, 5.41], [102.25, 2.19]],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    camera = _keyframes(result.css, "map_camera_1")
    width = _keyframes(result.css, "map_route_width_1")
    end_pct = min(percent for percent, body in camera.items() if "scale(" in body)
    start_pct = max(percent for percent in camera if percent < end_pct)
    start_scale = (
        float(re.search(r"scale\(([\d.]+)\)", camera[start_pct]).group(1))
        if "scale(" in camera[start_pct]
        else 1.0
    )
    end_scale = float(re.search(r"scale\(([\d.]+)\)", camera[end_pct]).group(1))
    base = float(re.search(r"--map-route-width-1:\s*([\d.]+)", width[0.0]).group(1))
    for step in range(1, 100):
        time = start_pct + (end_pct - start_pct) * step / 100.0
        raw = (time - start_pct) / (end_pct - start_pct)
        eased = _cubic_bezier_ease(raw, _timing_of(camera[start_pct]))
        scale = start_scale + (end_scale - start_scale) * eased
        left = max(percent for percent in width if percent <= time)
        right = min(percent for percent in width if percent > time)
        local = (time - left) / (right - left)
        width_progress = _cubic_bezier_ease(local, _timing_of(width[left]))
        width_at_left = float(re.search(r"--map-route-width-1:\s*([\d.]+)", width[left]).group(1))
        width_at_right = float(re.search(r"--map-route-width-1:\s*([\d.]+)", width[right]).group(1))
        stroke = width_at_left + (width_at_right - width_at_left) * width_progress
        assert stroke * scale == pytest.approx(base, rel=0.05)


def test_a_zoom_can_cue_on_the_spoken_phrase():
    props = {"country": "Malaysia", "zooms": [{"bbox": "100,1,104,5", "cue": "the strait"}]}
    assert map_scene_cues({"props": props}) == {"zoom_0": "the strait"}


def test_an_unspoken_zoom_cue_is_switched_off():
    props = {"country": "Malaysia", "zooms": [{"bbox": "100,1,104,5", "cue": "the strait"}]}
    words = [{"word": "nothing", "start": 0.2, "end": 0.4}]
    silenced = silence_unspoken_map_cues(props, words)
    assert silenced["zooms"][0]["cue"] is False


@pytest.mark.parametrize(
    "zooms",
    [
        "100,1,104,5",
        [{}],
        [{"bbox": "1,2,3"}],
        [{"bbox": "5,1,4,2"}],
    ],
)
def test_a_bad_zoom_is_rejected(zooms):
    with pytest.raises(MapSceneError):
        render({"country": "Malaysia", "zooms": zooms})


# --- named zoom targets that fit the map panel (#344) ---------------------


def _projected_aspect(view: View) -> float:
    """The projected width/height of a view's bounds, measured by ``project``."""
    west, south, east, north = view.bounds
    left, _ = view.project(west, (south + north) / 2.0)
    right, _ = view.project(east, (south + north) / 2.0)
    _, top = view.project((west + east) / 2.0, north)
    _, bottom = view.project((west + east) / 2.0, south)
    return (right - left) / (bottom - top)


def _bounds_cover(bounds: tuple[float, float, float, float], points) -> bool:
    west, south, east, north = bounds
    return all(west <= lon <= east and south <= lat <= north for lon, lat in points)


def test_a_to_zoom_resolves_a_river_by_its_alias():
    zoom = _zooms({"zooms": [{"to": ["Ganga"]}]})[0]
    points = [
        point for river in find_river("Ganga") for segment in river.segments for point in segment
    ]
    assert _bounds_cover(zoom.bounds, points)


def test_a_to_zoom_resolves_a_region():
    zoom = _zooms({"zooms": [{"to": ["Western Ghats"]}]})[0]
    region = find_region("Western Ghats")
    points = [point for polygon in region.polygons for ring in polygon for point in ring]
    assert _bounds_cover(zoom.bounds, points)


def test_a_to_zoom_resolves_a_scene_marker_case_insensitively():
    from video_editor_maps.scenes import _markers

    props = {
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        "zooms": [{"to": ["MALACCA"]}],
    }
    zoom = _zooms(props)[0]
    # The zoom centres on the marker as drawn (after any coast snap, #342).
    marker = _markers(props)[0]
    assert zoom.bounds == pytest.approx(
        (marker.lon - 0.6, marker.lat - 0.6, marker.lon + 0.6, marker.lat + 0.6)
    )


def test_a_to_zoom_unions_several_named_features():
    zoom = _zooms(
        {
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"to": ["Malacca", "Western Ghats"]}],
        }
    )[0]
    region = find_region("Western Ghats")
    points = [point for polygon in region.polygons for ring in polygon for point in ring]
    points.append((102.25, 2.19))
    assert _bounds_cover(zoom.bounds, points)


@pytest.mark.parametrize("projection", ["mercator", "equirectangular"])
def test_a_to_zoom_fits_the_map_panel_aspect(projection):
    frame = (150.0, 158.0, 950.0, 604.0)
    zoom = _zooms({"zooms": [{"to": ["Western Ghats"]}]})[0]
    view = _zoom_view(zoom, scale=1.0, frame=frame, projection=projection)
    assert _projected_aspect(view) == pytest.approx(frame[2] / frame[3], rel=0.01)


@pytest.mark.parametrize("projection", ["mercator", "equirectangular"])
def test_a_to_zoom_keeps_the_feature_centre_in_the_frame_centre(projection):
    frame = (150.0, 158.0, 950.0, 604.0)
    zoom = _zooms({"zooms": [{"to": ["Western Ghats"]}]})[0]
    view = _zoom_view(zoom, scale=1.0, frame=frame, projection=projection)
    west, south, east, north = zoom.bounds
    x, y = view.project((west + east) / 2.0, (south + north) / 2.0)
    assert x == pytest.approx(frame[0] + frame[2] / 2.0, abs=2.0)
    assert y == pytest.approx(frame[1] + frame[3] / 2.0, abs=2.0)


def test_an_unknown_to_name_is_named_in_the_error():
    with pytest.raises(MapSceneError, match="Fictional Feature"):
        _zooms({"zooms": [{"to": ["Fictional Feature"]}]})


def test_a_zoom_cannot_give_both_bbox_and_to():
    with pytest.raises(MapSceneError):
        _zooms({"zooms": [{"bbox": "100,1,104,5", "to": ["Malacca"]}]})


def test_a_zoom_cannot_give_neither_bbox_nor_to():
    with pytest.raises(MapSceneError):
        _zooms({"zooms": [{}]})


def test_a_bbox_zoom_is_not_fitted_and_keeps_its_exact_bounds():
    zoom = _zooms({"zooms": [{"bbox": "100,1,104,5"}]})[0]
    assert zoom.fit is False
    assert zoom.bounds == (100.0, 1.0, 104.0, 5.0)
    frame = (150.0, 158.0, 950.0, 604.0)
    view = _zoom_view(zoom, scale=1.0, frame=frame, projection="mercator")
    assert view.bounds == zoom.bounds


# --- dropped zoom record and mid-motion geometry (#332, #334) -------------

PILOT_PLAN = (
    Path(__file__).resolve().parents[1]
    / "projects"
    / "editorial-pilot-malacca-3"
    / "plan"
    / "scene_plan.json"
)


def _dropped_zoom_props() -> dict:
    return {
        "country": "Malaysia",
        "bbox": "95,0.5,119,8",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        "zooms": [
            {"bbox": "100.0,1.0,104.5,5.5"},
            {"bbox": "100.0,1.0,104.5,5.5"},
            {"bbox": "100.0,1.0,104.5,5.5"},
        ],
    }


def test_every_zoom_that_misses_the_cut_is_recorded_including_those_after_it():
    result = render(_dropped_zoom_props(), duration=2.0)
    # The first move lands; the second cannot, and the old break also skipped
    # the third. Both dropped indexes must be recorded, not just the first.
    assert result.dropped_zooms == (1, 2)


def test_a_dropped_zoom_logs_its_scene_needed_and_available_seconds(caplog):
    with caplog.at_level("WARNING", logger="video_editor_maps.scenes"):
        render(_dropped_zoom_props(), duration=2.0)
    assert "map_zoom_dropped" in caplog.text
    assert "scene_1" in caplog.text
    assert "zoom_index" in caplog.text
    assert "0.84" in caplog.text
    assert "0.42" in caplog.text


def test_motion_warnings_read_as_the_cut_shortfall():
    warnings = map_scene_motion_warnings(
        {"id": "scene_2", "type": "map", "props": _dropped_zoom_props()},
        DESIGN,
        width=1920,
        height=1080,
        duration=2.0,
    )
    assert warnings == [
        "scene_2: zoom 1 dropped: needs 0.84s, 0.42s left before the cut",
        "scene_2: zoom 2 dropped: needs 0.84s, 0.42s left before the cut",
    ]


def test_no_warnings_when_every_zoom_plays():
    warnings = map_scene_motion_warnings(
        {
            "id": "scene_2",
            "type": "map",
            "props": {
                "country": "Malaysia",
                "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
            },
        },
        DESIGN,
        width=1920,
        height=1080,
        duration=8.0,
    )
    assert warnings == []


def _pilot_map_scene() -> tuple[dict, float, list[dict]]:
    if not PILOT_PLAN.is_file():
        pytest.skip(f"shipped Malacca pilot plan is not present at {PILOT_PLAN}")
    plan = json.loads(PILOT_PLAN.read_text(encoding="utf-8"))
    scenes = plan["scenes"]
    position = next(index for index, scene in enumerate(scenes) if scene["id"] == "scene_2")
    scene = scenes[position]
    start = sum(float(other.get("duration_seconds") or 0.0) for other in scenes[:position])
    words = [
        {**word, "start": word["start"] - start, "end": word["end"] - start}
        for word in scene["props"]["word_times"]
    ]
    return scene, float(scene["duration_seconds"]), words


def test_the_malacca_pilot_map_passes_the_motion_geometry_check():
    scene, duration, words = _pilot_map_scene()
    problems = map_scene_motion_problems(
        scene,
        load_design("cream_editorial"),
        width=1080,
        height=1920,
        duration=duration,
        words=words,
    )
    assert problems == []


def test_a_marker_label_that_lands_past_the_map_frame_is_a_motion_problem():
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [
                {
                    "text": "A very long place label that will not fit the frame",
                    "lon": 108.0,
                    "lat": 4.0,
                    "cue": False,
                }
            ],
            "zooms": [{"bbox": "100,0.7,112,5"}],
        },
    }
    problems = map_scene_motion_problems(scene, DESIGN, width=1080, height=1920, duration=8.0)
    assert any("label lands outside the map frame" in problem for problem in problems)


def test_the_map_window_clip_moves_to_the_trimmed_overlay():
    result = render(
        {
            "country": "Malaysia",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
        },
        duration=8.0,
    )
    window = re.search(r"\.map-1 \.map-window \{[^}]*\}", result.css)
    assert window is not None
    assert "clip-path" not in window.group(0)
    overlay = re.search(r"\.map-1 \.map-overlay \{[^}]*\}", result.css)
    assert overlay is not None
    inset = "inset(158.0px 60.0px 304.0px 60.0px)"
    assert f"-webkit-clip-path: {inset}" in overlay.group(0)
    assert f"clip-path: {inset}" in overlay.group(0)


def test_the_label_width_estimate_matches_the_css_tracking():
    from video_editor_maps.scenes import (
        LABEL_ADVANCE,
        LABEL_TRACKING,
        _label_box,
        _ProjectedMarker,
    )

    result = render({"country": "China"})
    assert f"letter-spacing:{LABEL_TRACKING:g}em" in result.css
    marker = _ProjectedMarker(text="Eastern Ghats", x=0.0, y=0.0, delay=0.0)
    box = _label_box(marker, "right", 0.0, 0.0, font_size=28.0, gap=24.0)
    assert box[2] == pytest.approx((LABEL_ADVANCE + LABEL_TRACKING) * 28.0 * 13 + 24.0)


def test_the_motion_gate_checks_the_base_view_label():
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "India",
            "bbox": "72,7,84,22",
            "markers": [{"text": "Eastern Ghats", "lon": 86.29, "lat": 15.0, "cue": False}],
            "zooms": [{"bbox": "85.6,10,91.6,16", "cue": "later"}],
        },
    }
    words = [{"word": "later", "start": 4.5, "end": 4.8}]
    problems = map_scene_motion_problems(
        scene,
        load_design("cream_editorial"),
        width=1080,
        height=1920,
        duration=6.0,
        words=words,
    )
    assert any("base view" in problem for problem in problems)


def test_the_focus_place_label_uses_the_same_side_choice_and_gate():
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "India",
            "bbox": "72,7,84,22",
            "focus": "86.29,15.0",
            "label": "Eastern Ghats",
            "zooms": [{"bbox": "85.6,10,91.6,16"}],
        },
    }
    design = load_design("cream_editorial")
    result = render_map(scene, design, index=1, duration=6.0, width=1080, height=1920)
    label = re.search(
        r'class="map-place is-(left|right)" data-camera-track="place"[^>]*>Eastern Ghats',
        result.markup,
    )
    assert label is not None and label.group(1) == "right"
    problems = map_scene_motion_problems(scene, design, width=1080, height=1920, duration=6.0)
    assert problems == []


def test_a_zoomed_marker_label_fits_the_frame_and_the_qa_agrees(monkeypatch):
    """The zoom's fitted window is wider than its bbox, so the pin stays in shot."""
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "India",
            "bbox": "72,7,84,22",
            "markers": [
                {"text": "Western Ghats", "lon": 75.3, "lat": 13.0},
                {"text": "Eastern Ghats", "lon": 78.0, "lat": 15.0},
                {"text": "Deccan", "lon": 77.5, "lat": 17.5},
            ],
            "zooms": [{"bbox": "73.5,8.5,77.5,15.5"}],
        },
    }
    design = load_design("cream_editorial")
    result = render_map(scene, design, index=1, duration=5.9, width=1080, height=1920)
    label = re.search(
        r'class="map-place is-(left|right)" data-camera-track="marker_1"[^>]*>Eastern Ghats',
        result.markup,
    )
    assert label is not None and label.group(1) == "left"
    assert map_scene_motion_problems(scene, design, width=1080, height=1920, duration=5.9) == []

    # The old base-only side choice draws this label to the right, where the
    # landing clips it, and the gate must catch that.
    from video_editor_maps.scenes import _LabelPlacement

    monkeypatch.setattr(
        "video_editor_maps.scenes._layout_labels",
        lambda specs, view, moves, frame, *, font_size, margin, seconds, occupied_shapes=None: [
            _LabelPlacement("right", 0.0) for _ in specs
        ],
    )
    problems = map_scene_motion_problems(scene, design, width=1080, height=1920, duration=5.9)
    assert any(
        "Eastern Ghats" in problem and "label lands outside" in problem for problem in problems
    )


def test_two_long_marker_labels_are_separated_after_a_move():
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [
                {
                    "text": "A very long marker label number one",
                    "lon": 102.0,
                    "lat": 2.0,
                    "cue": False,
                },
                {
                    "text": "A very long marker label number two",
                    "lon": 109.5,
                    "lat": 2.0,
                    "cue": False,
                },
            ],
            "zooms": [{"bbox": "100,0.7,112,5"}],
        },
    }
    problems = map_scene_motion_problems(scene, DESIGN, width=1920, height=1080, duration=8.0)
    assert not any("overlap" in problem for problem in problems)


def test_every_map_type_size_clears_the_craft_floor():
    result = render(
        {
            "country": "Malaysia",
            "badge": "300 KM",
            "badge_sub": "of navigable strait",
            "eyebrow": "GEOGRAPHY",
            "headline": "The strait",
            "subtitle": "One narrow gap",
            "watermark": "EXPLAINER",
            "callouts": ["SPICE", "TIN"],
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        }
    )
    sizes = [float(value) for value in re.findall(r"font-size:([\d.]+)px", result.css)]
    assert sizes and min(sizes) >= 28.0


# --- Vox-style vector map motion (stutter, great circle, route dot) -------


def _new_prop_props() -> dict:
    """A plate that exercises every motion the new props may change."""
    return {
        "country": "Malaysia",
        "bbox": "95,0.5,119,8",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        "route": [[100.33, 5.41], [102.25, 2.19]],
        "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
    }


def test_a_scene_without_the_new_map_props_renders_exactly_as_before():
    props = _new_prop_props()
    plain = render(props, duration=8.0)
    # None of the new constructs leak into a scene that does not ask for them.
    assert "steps(" not in plain.css
    assert "offset-path" not in plain.css
    assert "map-route-dot" not in plain.markup
    # Explicitly switching each feature off is the same as leaving it out.
    off = render(
        {**props, "stutter": False, "route_curve": "straight", "route_dot": False},
        duration=8.0,
    )
    assert off == plain


def test_stutter_steps_the_overlay_reveals_but_not_the_camera():
    result = render({**_new_prop_props(), "stutter": True}, duration=8.0)
    # 12 fps on a 30 fps grid is a 2-frame step (1/15 s). The route draw is
    # min(2.4, max(.8, 8 * .28)) = 2.24 s, so N = round(2.24 * 15) = 34.
    assert "map_route_1 2.240s steps(34, end) 0.420s both" in result.css
    # A marker reveal (entry * 1.4 = .49 s) steps on the same grid: N = 7.
    assert (
        '[data-camera-track="marker_0"] { animation: map_marker_1 '
        "0.490s steps(7, end) 0.315s both, map_track_1_marker_0"
    ) in result.css
    # The camera itself keeps its smooth curve; only the overlay steps.
    camera = _keyframes(result.css, "map_camera_1")
    assert any("cubic-bezier(.4,0,.2,1)" in body for body in camera.values())
    # An anchor that follows the camera still interpolates left/top smoothly.
    track = _keyframes(result.css, "map_track_1_marker_0")
    assert track and all("steps(" not in body for body in track.values())


def test_stutter_rate_and_fps_snap_to_the_frame_grid():
    result = render_map(
        {"id": "scene_1", "type": "map", "props": {**_new_prop_props(), "stutter": 6}},
        DESIGN,
        index=1,
        duration=8.0,
        width=1920,
        height=1080,
        fps=24,
    )
    # 24 fps / 6 = 4-frame steps (1/6 s). A .49 s marker reveal needs 3 steps.
    assert "map_marker_1 0.490s steps(3, end)" in result.css


def test_a_bad_stutter_rate_fails_closed():
    with pytest.raises(MapSceneError, match="stutter"):
        render({**_new_prop_props(), "stutter": 0})


def test_great_circle_sampling_bows_london_to_new_york_north():
    from video_editor_maps.scenes import _great_circle_points

    points = _great_circle_points((-0.1276, 51.5072), (-74.0060, 40.7128))
    assert points[0] == pytest.approx((-0.1276, 51.5072))
    assert points[-1] == pytest.approx((-74.0060, 40.7128))
    midpoint = points[len(points) // 2]
    # The great circle peaks near 52.4 N; the straight (Mercator) chord
    # crosses the Atlantic near 46 N.
    assert midpoint[1] > 52.0


def test_route_curve_great_circle_samples_the_drawn_path():
    props = {
        "country": "United Kingdom",
        "bbox": "-80,30,10,60",
        "route": [[-0.1276, 51.5072], [-74.0060, 40.7128]],
    }
    straight = render(props)
    curved = render({**props, "route_curve": "great_circle"})
    straight_path = re.search(r'<path class="map-route" d="([^"]+)"', straight.markup)
    curved_path = re.search(r'<path class="map-route" d="([^"]+)"', curved.markup)
    assert straight_path is not None and curved_path is not None
    # A straight segment is two commands; the sampled great circle is many.
    assert straight_path.group(1).count("L") == 1
    assert curved_path.group(1).count("L") >= 24


def test_a_great_circle_crossing_the_antimeridian_does_not_jump_the_frame():
    props = {
        "country": "Russia",
        "bbox": "-180,0,180,80",
        "route": [[179.0, 10.0], [-179.0, 10.0]],
        "route_curve": "great_circle",
    }
    result = render(props)
    path = re.search(r'<path class="map-route" d="([^"]+)"', result.markup)
    assert path is not None
    xs = [float(x) for x, _y in re.findall(r"[ML](-?[\d.]+) (-?[\d.]+)", path.group(1))]
    assert len(xs) >= 25
    # Unwrapped, the two meridians are ~2 degrees apart, not a full frame.
    assert max(abs(right - left) for left, right in zip(xs, xs[1:], strict=False)) < 50.0


def test_route_dot_travels_on_the_route_timing():
    result = render({**_new_prop_props(), "route_dot": True}, duration=8.0)
    dot = re.search(r'<circle class="map-route-dot"[^>]*>', result.markup)
    assert dot is not None and 'r="5.0"' in dot.group(0)
    route = re.search(r'<path class="map-route" d="([^"]+)"', result.markup)
    assert route is not None
    rule = re.search(r"\.map-1 \.map-route-dot \{(.*?)\}", result.css, re.S)
    assert rule is not None
    body = rule.group(1)
    # The dot travels the route's own path, not a copy that could drift.
    assert f'offset-path: path("{route.group(1)}")' in body
    assert "offset-distance: var(--map-route-dot-1" in body
    assert "opacity: var(--map-route-dot-fade-1" in body
    # Its travel animation has the route draw's duration, delay and curve,
    # and it lives on the HTML wrapper so the frame seek reaches it.
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    route_timing = re.search(
        r"map_route_1 ([\d.]+)s (\S+(?:\([^)]*\))?) ([\d.]+)s both", ghost.group(1)
    )
    dot_timing = re.search(
        r"map_route_dot_1 ([\d.]+)s (\S+(?:\([^)]*\))?) ([\d.]+)s both", ghost.group(1)
    )
    assert route_timing is not None and dot_timing is not None
    assert dot_timing.groups() == route_timing.groups()
    # It arrives, then fades shortly after.
    assert "map_route_dot_fade_1" in result.css


# --- route draw must be seeked on an HTML element -------------------------


def _route_props() -> dict:
    return {
        "country": "Malaysia",
        "bbox": "95,0.5,119,8",
        "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        "route": [[100.33, 5.41], [102.25, 2.19]],
        "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}],
    }


def test_the_route_draw_is_animated_on_an_html_element():
    """HyperFrames seeks CSS animations on HTML elements only, never SVG paths."""
    result = render(_route_props(), duration=8.0)
    assert "@property --map-route-draw-1" in result.css
    assert "@property --map-route-width-1" in result.css
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    # The draw and width animations live on the HTML wrapper.
    assert "map_route_1 " in ghost.group(1)
    assert "map_route_width_1 " in ghost.group(1)
    # The SVG path only reads the inherited values; it is not the target.
    route_tag = re.search(r'<path class="map-route"[^>]*>', result.markup)
    assert route_tag is not None
    assert "animation" not in route_tag.group(0)
    assert "animation-delay" not in route_tag.group(0)
    # The dash offset and stroke width are driven by the wrapper's properties.
    assert "stroke-dashoffset:var(--map-route-draw-1" in result.css.replace(" ", "")
    assert "stroke-width:var(--map-route-width-1" in result.css.replace(" ", "")


def test_the_route_draw_delay_is_in_the_rule_not_an_inline_style():
    words = [{"word": "sailed", "start": 1.5, "end": 1.8}]
    result = render({**_route_props(), "route_cue": "sailed"}, words=words, duration=8.0)
    route_tag = re.search(r'<path class="map-route"[^>]*>', result.markup)
    assert route_tag is not None and "animation-delay" not in route_tag.group(0)
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    assert re.search(r"map_route_1 [\d.]+s \S+ 1\.500s both", ghost.group(1))


# --- real rivers ----------------------------------------------------------


def test_find_river_is_case_insensitive_and_returns_every_feature():
    from video_editor_maps.geo import GeoError, find_river

    assert {river.name for river in find_river("nile")} == {"Nile"}
    assert len(find_river("Nile")) >= 2
    assert find_river("Amazon")[0].name == "Amazonas"
    assert find_river("Ganga")[0].name == "Ganges"
    with pytest.raises(GeoError, match="unknown river"):
        find_river("Fictional River")


def test_the_nile_runs_source_to_mouth_into_the_mediterranean():
    from video_editor_maps.geo import find_river
    from video_editor_maps.scenes import _river_course

    main, _tributaries = _river_course(find_river("Nile"))
    assert main[0][1] < 20.0  # source in central Africa
    assert main[-1][1] > 30.5  # the delta branch reaches the Mediterranean
    assert main[-1][1] > main[0][1]


def test_the_danube_runs_towards_the_east():
    from video_editor_maps.geo import find_river
    from video_editor_maps.scenes import _river_course

    main, _tributaries = _river_course(find_river("Danube"))
    assert main[-1][0] > main[0][0]
    assert main[-1][0] > 28.0


def test_the_ganges_ends_at_the_bay_of_bengal():
    from video_editor_maps.geo import find_river
    from video_editor_maps.scenes import _river_course

    main, _tributaries = _river_course(find_river("Ganges"))
    assert main[-1][0] > 88.0
    assert main[-1][1] < 23.0


def test_a_river_is_drawn_with_its_flow_layer_and_a_clear_blue():
    props = {
        "country": "Egypt",
        "bbox": "24,20,36,33",
        "rivers": [{"name": "Nile"}],
    }
    result = render(props, duration=8.0)
    assert 'class="map-river"' in result.markup
    assert "#9FD0FF" in result.css
    assert "@property --map-river-draw-1-0-0" in result.css
    assert "map_river_draw_1_0_0 " in result.css
    # The flow is a separate dashed copy that starts when the draw ends.
    assert 'class="map-river-flow"' in result.markup
    assert "map_river_flow_1_0_0 " in result.css
    assert "stroke-dasharray" in result.markup


def test_the_design_can_define_the_river_colour():
    from types import SimpleNamespace

    from video_editor_maps.scenes import _colors

    design = SimpleNamespace(layers=SimpleNamespace(map={"river": "#123456"}))
    assert _colors(design, {})["river"] == "#123456"
    # And a scene may still override the design.
    assert _colors(design, {"river": "#654321"})["river"] == "#654321"


def test_the_flow_layer_is_a_lighter_tint_than_the_river():
    result = render(
        {
            "country": "Egypt",
            "bbox": "24,20,36,33",
            "rivers": [{"name": "Nile"}],
        },
        duration=8.0,
    )
    main = re.search(r'class="map-river"[^>]*stroke="(#[0-9A-Fa-f]{6})"', result.markup)
    flow = re.search(r'class="map-river-flow"[^>]*stroke="(#[0-9A-Fa-f]{6})"', result.markup)
    assert main is not None and flow is not None
    main_total = sum(int(main.group(1)[start : start + 2], 16) for start in (1, 3, 5))
    flow_total = sum(int(flow.group(1)[start : start + 2], 16) for start in (1, 3, 5))
    assert flow_total > main_total


def test_a_river_flow_can_be_switched_off():
    props = {
        "country": "Egypt",
        "bbox": "24,20,36,33",
        "rivers": [{"name": "Nile"}],
    }
    without = render({**props, "flow": False}, duration=8.0)
    assert 'class="map-river-flow"' not in without.markup
    assert "map_river_flow_1_0_0" not in without.css


def test_a_river_reveals_on_its_cue_and_joins_the_cue_map():
    props = {
        "country": "Egypt",
        "bbox": "24,20,36,33",
        "rivers": [{"name": "Nile", "cue": "nile"}],
    }
    assert map_scene_cues({"props": props})["river_0"] == "nile"
    words = [{"word": "Nile", "start": 2.0, "end": 2.4}]
    result = render(
        {**props, "rivers": [{"name": "Nile"}]},
        words=words,
        duration=8.0,
    )
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    assert re.search(r"map_river_draw_1_0_0 [\d.]+s \S+ 2\.000s both", ghost.group(1))


def test_silencing_turns_off_a_river_the_narration_never_names():
    words = [{"word": "nothing", "start": 0.2, "end": 0.4}]
    out = silence_unspoken_map_cues({"country": "Egypt", "rivers": [{"name": "Nile"}]}, words)
    assert out["rivers"][0]["cue"] is False


def test_a_river_label_sits_near_the_course_and_follows_the_camera():
    result = render(
        {
            "country": "Egypt",
            "bbox": "24,20,36,33",
            "rivers": [{"name": "Nile", "label": True}],
        },
        duration=8.0,
    )
    assert "Nile" in result.markup
    assert 'data-camera-track="river_0"' in result.markup
    assert "map_river_label_1" in result.css


def test_river_draw_stutters_like_the_route():
    result = render(
        {
            "country": "Egypt",
            "bbox": "24,20,36,33",
            "rivers": [{"name": "Nile"}],
            "stutter": True,
        },
        duration=8.0,
    )
    assert re.search(r"map_river_draw_1_0_0 [\d.]+s steps\([\d]+, end\)", result.css)


def test_an_unknown_river_fails_closed_with_a_clear_error():
    with pytest.raises(MapSceneError, match="unknown river"):
        render(
            {
                "country": "Egypt",
                "bbox": "24,20,36,33",
                "rivers": [{"name": "Fictional River"}],
            }
        )


def test_a_scene_without_rivers_renders_exactly_as_before():
    props = _route_props()
    plain = render(props, duration=8.0)
    assert "map-river" not in plain.markup
    assert "map_river" not in plain.css
    assert render({**props, "rivers": []}, duration=8.0) == plain


# --- river casing and core ------------------------------------------------


def _river_props() -> dict:
    return {"country": "Egypt", "bbox": "24,20,36,33", "rivers": [{"name": "Nile"}]}


def _tag(markup: str, class_name: str) -> str:
    match = re.search(rf'<path class="{re.escape(class_name)}"[^>]*>', markup)
    assert match is not None, class_name
    return match.group(0)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', tag)
    assert match is not None, (name, tag)
    return match.group(1)


def test_a_river_is_a_dark_casing_under_a_light_core():
    result = render(_river_props(), duration=8.0)
    casing = _tag(result.markup, "map-river-casing")
    core = _tag(result.markup, "map-river")
    assert _attr(core, "stroke").lower() == "#9fd0ff"
    assert float(_attr(casing, "stroke-width")) == pytest.approx(
        float(_attr(core, "stroke-width")) * 2.2
    )
    assert float(_attr(casing, "stroke-opacity")) == pytest.approx(0.55)
    # Casing and core share one draw property, so they reveal together.
    assert _attr(casing, "style") == _attr(core, "style")
    flow = _tag(result.markup, "map-river-flow")
    assert _attr(flow, "stroke").lower() == "#ffffff"
    assert float(_attr(flow, "stroke-opacity")) == pytest.approx(0.85)


# --- focus pin suppression ------------------------------------------------


def test_rivers_without_markers_suppress_the_fallback_focus_pin():
    props = {
        "country": "Egypt",
        "bbox": "24,20,36,33",
        "focus": "31,26",
        **_river_props(),
    }
    result = render(props)
    assert 'class="map-pin-point"' not in result.markup
    forced = render({**props, "focus_pin": True})
    assert forced.markup.count('class="map-pin-point"') == 1


def test_regions_without_markers_suppress_the_fallback_focus_pin():
    props = {
        "country": "India",
        "bbox": "72,7,84,22",
        "focus": "77,15",
        "regions": [{"name": "Deccan Plateau"}],
    }
    result = render(props)
    assert 'class="map-pin-point"' not in result.markup
    forced = render({**props, "focus_pin": True})
    assert forced.markup.count('class="map-pin-point"') == 1


# --- named regions --------------------------------------------------------


def test_find_region_is_case_insensitive_and_merges_repeated_names():
    from video_editor_maps.geo import GeoError, find_region

    ghats = find_region("western ghats")
    assert ghats.name == "Western Ghats"
    assert ghats.kind == "range"
    assert len(ghats.polygons) == 2
    assert find_region("ALPS").kind == "range"
    with pytest.raises(GeoError, match="unknown region"):
        find_region("Fictional Region")


def test_region_styles_default_from_the_kind():
    cases = [
        ("Switzerland", "5,43,17,49", "Alps", "map-region-relief-1"),
        ("Algeria", "-10,15,35,35", "Sahara", "map-region-desert-1"),
        ("India", "72,7,84,22", "Deccan Plateau", "map-region-plateau-1"),
    ]
    for country, bbox, name, pattern in cases:
        result = render(
            {"country": country, "bbox": bbox, "regions": [{"name": name}]},
        )
        assert pattern in result.markup, name
        assert 'class="map-region-outline"' in result.markup
        assert "map_region_draw_1_0_0" in result.css


def test_the_andean_range_renders_across_peru():
    result = render(
        {
            "country": "Peru",
            "bbox": "-82,-20,-60,5",
            "regions": [{"name": "Andes"}],
        },
    )
    assert "map-region-relief-1" in result.markup
    assert 'class="map-region-outline"' in result.markup
    assert "map_region_draw_1_0_0" in result.css


def test_a_region_reveals_all_its_polygons():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Western Ghats"}],
        },
    )
    # Western Ghats has two separate Natural Earth polygons.
    assert result.markup.count('class="map-region-outline"') == 2
    assert result.markup.count('class="map-region-fill"') == 2


def test_region_outline_draws_on_and_the_fill_follows():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Western Ghats"}],
        },
        duration=6.0,
    )
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    # Draw starts at the bounded stagger (entry 0.35) and lasts 1.08 s; the
    # fill begins just after it.
    assert "map_region_draw_1_0_0 1.080s ease-in-out 0.350s both" in ghost.group(1)
    assert "map_region_fill_1_0 0.400s ease-out 1.430s both" in ghost.group(1)


def test_region_reveal_stutters_like_the_route():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Western Ghats"}],
            "stutter": True,
        },
        duration=6.0,
    )
    ghost = re.search(r"\.map-1 \.map-ghost-wrap \{(.*?)\}", result.css, re.S)
    assert ghost is not None
    assert re.search(r"map_region_draw_1_0_0 [\d.]+s steps\([\d]+, end\)", ghost.group(1))


def test_a_region_label_sits_at_the_largest_polygon_centre():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Deccan Plateau", "label": "Deccan"}],
        },
    )
    assert "Deccan" in result.markup
    assert 'class="map-place is-center"' in result.markup
    assert 'data-camera-track="region_0"' in result.markup
    assert "map_label_visible_1_region_0" in result.css


def test_a_region_reveals_on_its_cue_and_joins_the_cue_map():
    props = {
        "country": "India",
        "bbox": "72,7,84,22",
        "regions": [{"name": "Western Ghats", "cue": "western ghats"}],
    }
    assert map_scene_cues({"props": props})["region_0"] == "western ghats"
    words = [{"word": "nothing", "start": 0.2, "end": 0.4}]
    silenced = silence_unspoken_map_cues(props, words)
    assert silenced["regions"][0]["cue"] is False


def test_an_unknown_region_fails_closed_with_a_clear_error():
    with pytest.raises(MapSceneError, match="unknown region"):
        render(
            {
                "country": "India",
                "bbox": "72,7,84,22",
                "regions": [{"name": "Fictional Region"}],
            }
        )


def test_a_scene_without_regions_renders_exactly_as_before():
    props = _route_props()
    plain = render(props, duration=8.0)
    assert "map-region" not in plain.markup
    assert "map_region" not in plain.css
    assert render({**props, "regions": []}, duration=8.0) == plain


# --- pin drop -------------------------------------------------------------


def test_marker_pins_drop_and_settle():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
        },
        duration=6.0,
    )
    drop = _keyframes(result.css, "map_marker_drop_1")
    assert "translateY(-40px)" in drop[0.0]
    assert "translateY(4px)" in drop[70.0]
    assert "translateY(0)" in drop[100.0]
    pin = re.search(
        r'\.map-1 \.map-marker\[data-camera-track="marker_0"\] \{ animation: ([^;]+); \}',
        result.css,
    )
    assert pin is not None and "map_marker_drop_1" in pin.group(1)
    label = re.search(
        r'\.map-1 \[data-camera-track="marker_0"\] \{ animation: ([^;]+); \}',
        result.css,
    )
    assert label is not None and "map_marker_1" in label.group(1)


def test_pin_drop_stutters():
    result = render(
        {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19}],
            "stutter": True,
        },
        duration=6.0,
    )
    pin = re.search(
        r'\.map-1 \.map-marker\[data-camera-track="marker_0"\] \{ animation: ([^;]+); \}',
        result.css,
    )
    assert pin is not None and re.search(r"map_marker_drop_1 [\d.]+s steps\(", pin.group(1))


# --- label text overrides -------------------------------------------------


def test_a_river_label_string_replaces_the_data_name():
    result = render(
        {
            "country": "India",
            "bbox": "68,8,98,36",
            "rivers": [{"name": "Ganges", "label": "Ganga"}],
        },
        duration=6.0,
    )
    assert ">Ganga</div>" in result.markup
    assert ">Ganges</div>" not in result.markup
    assert 'data-camera-track="river_0"' in result.markup


def test_a_region_label_string_replaces_the_data_name():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Deccan Plateau", "label": "Deccan"}],
        },
        duration=6.0,
    )
    assert ">Deccan</div>" in result.markup
    assert ">Deccan Plateau</div>" not in result.markup
    assert 'data-camera-track="region_0"' in result.markup


def test_a_true_label_keeps_the_data_name():
    river = render(
        {
            "country": "India",
            "bbox": "68,8,98,36",
            "rivers": [{"name": "Ganges", "label": True}],
        },
        duration=6.0,
    )
    region = render(
        {
            "country": "India",
            "bbox": "72,7,84,22",
            "regions": [{"name": "Deccan Plateau", "label": True}],
        },
        duration=6.0,
    )
    assert ">Ganges</div>" in river.markup
    assert ">Deccan Plateau</div>" in region.markup


@pytest.mark.parametrize("label", [False, "", "   "])
def test_a_false_or_empty_label_shows_nothing(label):
    result = render(
        {
            "country": "India",
            "bbox": "68,8,98,36",
            "rivers": [{"name": "Ganges", "label": label}],
            "regions": [{"name": "Deccan Plateau", "label": label}],
        },
        duration=6.0,
    )
    assert 'data-camera-track="river_0"' not in result.markup
    assert 'data-camera-track="region_0"' not in result.markup


def test_the_shown_label_text_drives_the_label_gate(monkeypatch):
    from video_editor_maps import scenes as map_module

    captured: list[str] = []
    original = map_module._layout_labels

    def spy(specs, view, moves, frame, *, font_size, margin, seconds, occupied_shapes=None):
        captured.extend(spec.text for spec in specs)
        return original(
            specs,
            view,
            moves,
            frame,
            font_size=font_size,
            margin=margin,
            seconds=seconds,
            occupied_shapes=occupied_shapes,
        )

    monkeypatch.setattr(map_module, "_layout_labels", spy)
    render(
        {
            "country": "India",
            "bbox": "72,7,92,32",
            "rivers": [{"name": "Ganges", "label": "Ganga"}],
            "regions": [{"name": "Deccan Plateau", "label": "Deccan"}],
        },
        duration=6.0,
    )
    assert "Ganga" in captured
    assert "Deccan" in captured
    assert "Ganges" not in captured
    assert "Deccan Plateau" not in captured


# --- review regressions ---------------------------------------------------


ROUTE_NO_ZOOM = {
    "country": "Malaysia",
    "bbox": "95,-5,110,10",
    "markers": [
        {"text": "Penang", "lon": 100.33, "lat": 5.41},
        {"text": "Singapore", "lon": 103.82, "lat": 1.35},
    ],
    "route": [[100.33, 5.41], [103.82, 1.35]],
}


def _route_width_initial(css: str) -> str:
    block = re.search(r"@property --map-route-width-1 \{(.*?)\}", css, re.S)
    assert block is not None
    match = re.search(r"initial-value:\s*([^;]+);", block.group(1))
    assert match is not None
    return match.group(1).strip()


def test_a_route_without_camera_moves_keeps_a_nonzero_width():
    result = render(ROUTE_NO_ZOOM, duration=6.0)
    assert _route_width_initial(result.css) == "4.6"


def test_a_route_with_a_dropped_zoom_keeps_a_nonzero_width():
    result = render(
        {**ROUTE_NO_ZOOM, "zooms": [{"bbox": "100.0,1.0,104.5,5.5"}]},
        duration=1.0,
    )
    assert result.dropped_zooms == (0,)
    assert _route_width_initial(result.css) == "4.6"


def test_a_great_circle_that_bows_out_of_the_window_fails_closed():
    with pytest.raises(MapSceneError, match="leaves the map window"):
        render(
            {
                "country": "Norway",
                "bbox": "0,58.0,40,59.3",
                "route": [[1, 59], [29, 59]],
                "route_curve": "great_circle",
            }
        )


def test_region_labels_sit_inside_their_largest_polygon():
    from video_editor_maps.geo import find_region
    from video_editor_maps.scenes import _in_ring, _region_centre

    for name, predicate in (
        ("Western Ghats", lambda lon: lon < 77.0),
        ("Eastern Ghats", lambda lon: lon > 78.0),
    ):
        region = find_region(name)
        point = _region_centre(region)
        assert point is not None, name
        assert any(_in_ring(point[0], point[1], polygon[0]) for polygon in region.polygons), name
        assert predicate(point[0]), name


def _synthetic_region_scene() -> dict:
    """A cue-independent version of the scene_8 situation."""
    return {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "India",
            "bbox": "72,7,87,22",
            "stutter": True,
            "regions": [
                {"name": "Deccan Plateau", "label": "Deccan", "cue": "deccan"},
                {"name": "Western Ghats", "label": True, "cue": "western"},
                {"name": "Eastern Ghats", "label": True, "cue": "eastern"},
            ],
            "zooms": [
                {"bbox": "73,8,79.5,17", "cue": "western"},
                {"bbox": "77,10,86.5,20", "cue": "eastern"},
            ],
            "word_times": [
                {"word": "deccan", "start": 0.2, "end": 0.4},
                {"word": "western", "start": 1.0, "end": 1.2},
                {"word": "eastern", "start": 4.0, "end": 4.2},
            ],
        },
    }


def test_region_labels_anchor_on_the_visible_part_of_their_shape():
    from video_editor_maps.scenes import _prepare

    prepared = _prepare(
        _synthetic_region_scene(),
        load_design("cream_editorial"),
        width=1080,
        height=1920,
        duration=5.9,
    )
    western = next(region for region in prepared.regions if region.name == "Western Ghats")
    eastern = next(region for region in prepared.regions if region.name == "Eastern Ghats")
    assert len(western.label_points) == 3
    # The east zoom has no meaningful Western Ghats in shot, so its label is
    # hidden there rather than clamped into the corner.
    assert western.label_points[2] is None
    assert eastern.label_points[2] is not None


def test_labels_count_in_a_view_only_after_they_reveal():
    from video_editor_maps.scenes import (
        _label_view_boxes,
        _LabelPlacement,
        _LabelSpec,
        _view_ends,
    )

    frame = (150.0, 158.0, 950.0, 604.0)
    moves = [(1.0, 2.0, 0.0, 0.0)]
    view_ends = _view_ends(moves, 6.0)
    specs = [
        _LabelSpec("marker", 0, "Early", 500.0, 400.0, delay=0.2),
        _LabelSpec("marker", 1, "Late", 500.0, 400.0, delay=4.0),
    ]
    boxes = [
        _label_view_boxes(
            spec,
            _LabelPlacement("right", 0.0),
            [(spec.x, spec.y), (spec.x, spec.y)],
            frame,
            font_size=28.0,
            margin=24.0,
            view_ends=view_ends,
        )
        for spec in specs
    ]
    assert boxes[0][0] is not None
    assert boxes[1][0] is None


def test_the_synthetic_region_scene_has_no_base_label_overlap_or_false_qa():
    from video_editor_maps.scenes import (
        _label_points,
        _label_view_boxes,
        _layout_labels,
        _overlay_label_specs,
        _prepare,
        _view_ends,
    )

    design = load_design("cream_editorial")
    scene = _synthetic_region_scene()
    prepared = _prepare(scene, design, width=1080, height=1920, duration=5.9)
    frame = (72.0, 516.0, 936.0, 650.0)
    specs = _overlay_label_specs(prepared)
    placements = _layout_labels(
        specs,
        prepared.view,
        prepared.camera_moves,
        frame,
        font_size=28.0,
        margin=24.0,
        seconds=5.9,
    )
    view_ends = _view_ends(prepared.camera_moves, 5.9)
    boxes = [
        _label_view_boxes(
            spec,
            placement,
            _label_points(spec, prepared.camera_moves, frame),
            frame,
            font_size=28.0,
            margin=24.0,
            view_ends=view_ends,
        )
        for spec, placement in zip(specs, placements, strict=True)
    ]
    base_boxes = [box[0] for box in boxes if box[0] is not None]
    assert len(base_boxes) == 1  # only Deccan has revealed at the base view
    problems = map_scene_motion_problems(scene, design, width=1080, height=1920, duration=5.9)
    assert not any("overlap" in problem for problem in problems)
    assert not any("outside the map frame" in problem for problem in problems)


def test_no_map_test_reads_scratch_files():
    source = Path(__file__).read_text(encoding="utf-8")
    assert ("_oc_" + "scratch") not in source


# --- leader-line callouts -------------------------------------------------


def test_a_thin_range_forces_a_leader_callout():
    result = render(
        {
            "country": "India",
            "bbox": "73,8,78,21",
            "regions": [{"name": "Western Ghats", "label": True}],
        },
        duration=6.0,
    )
    assert 'class="map-callout"' in result.markup
    assert 'class="map-callout-line"' in result.markup
    assert 'class="map-callout-dot"' in result.markup
    assert "map_callout_line_1" in result.css


def test_a_wide_plateau_centres_its_label():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,87,22",
            "regions": [{"name": "Deccan Plateau", "label": "Deccan"}],
        },
        duration=6.0,
    )
    assert 'class="map-place is-center"' in result.markup
    assert 'class="map-callout"' not in result.markup


def test_callout_false_keeps_today_side_labels():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,87,22",
            "markers": [{"text": "Marker", "lon": 77.0, "lat": 15.0, "callout": False}],
            "rivers": [{"name": "Ganges", "label": True, "callout": False}],
            "regions": [
                {"name": "Western Ghats", "label": True, "callout": False},
                {"name": "Deccan Plateau", "label": "Deccan", "callout": False},
            ],
        },
        duration=6.0,
    )
    assert "map-callout" not in result.markup
    assert "map-callout" not in result.css
    assert "is-center" not in result.markup
    assert "is-left" in result.markup or "is-right" in result.markup


def test_the_india_callout_scene_lays_out_without_overlaps_or_crossings():
    scene = _synthetic_region_scene()
    problems = map_scene_motion_problems(
        scene, load_design("cream_editorial"), width=1080, height=1920, duration=5.9
    )
    assert not any("overlap" in problem for problem in problems)
    assert not any("crosses" in problem for problem in problems)


def test_a_leader_crossing_another_label_box_is_reported():
    from video_editor_maps.scenes import _label_problems, _LabelPlacement, _LabelSpec

    frame = (150.0, 158.0, 950.0, 604.0)
    specs = [
        _LabelSpec("marker", 0, "Left callout", 250.0, 300.0, delay=0.0),
        _LabelSpec("marker", 1, "Crossed label", 350.0, 300.0, delay=0.0),
    ]
    placements = [
        _LabelPlacement("centre", 0.0, "callout", (500.0, 0.0)),
        _LabelPlacement("right", 0.0),
    ]
    problems = _label_problems(
        specs,
        placements,
        View(frame=frame, bounds=(0.0, 0.0, 10.0, 10.0), projection="equirectangular"),
        (),
        frame,
        font_size=28.0,
        margin=24.0,
        scene_id="scene_1",
        seconds=6.0,
    )
    assert any("callout" in problem and "crosses" in problem for problem in problems)


def test_markers_and_rivers_can_force_a_callout():
    marker = render(
        {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [{"text": "Malacca", "lon": 102.25, "lat": 2.19, "callout": True}],
        },
        duration=6.0,
    )
    assert 'data-camera-track="callout_marker_0"' in marker.markup
    assert 'class="map-callout-line"' in marker.markup
    river = render(
        {
            "country": "India",
            "bbox": "68,8,98,36",
            "rivers": [{"name": "Ganges", "label": True, "callout": True}],
        },
        duration=6.0,
    )
    assert 'data-camera-track="callout_river_0"' in river.markup
    assert 'class="map-callout-line"' in river.markup


def test_every_callout_group_has_an_inline_base_position():
    from video_editor_maps.scenes import _overlay_label_specs, _prepare

    for zooms in ([], [{"bbox": "73,8,77,17"}]):
        scene = {
            "id": "scene_1",
            "type": "map",
            "props": {
                "country": "India",
                "bbox": "73,8,78,21",
                "regions": [{"name": "Western Ghats", "label": True}],
                "zooms": zooms,
            },
        }
        result = render_map(scene, DESIGN, index=1, duration=6.0, width=1920, height=1080)
        groups = re.findall(
            r'<div class="map-callout" data-camera-track="([^"]+)"(?: style="([^"]*)")?>',
            result.markup,
        )
        assert groups
        prepared = _prepare(scene, DESIGN, width=1920, height=1080, duration=6.0)
        specs = {spec.key: spec for spec in _overlay_label_specs(prepared)}
        for track, style in groups:
            spec = specs[track.removeprefix("callout_")]
            first = next(
                (point for point in (spec.view_points or ()) if point is not None),
                (spec.x, spec.y),
            )
            assert f"left:{first[0]:.1f}px;top:{first[1]:.1f}px" in style


def test_the_focus_place_label_is_first_in_the_overlay():
    result = render(
        {
            "country": "India",
            "bbox": "72,7,87,22",
            "markers": [{"text": "Marker", "lon": 77.0, "lat": 15.0, "callout": False}],
            "regions": [{"name": "Deccan Plateau", "label": "Deccan", "callout": False}],
            "focus": "78,16",
            "label": "Focus Place",
            "focus_pin": True,
        },
        duration=6.0,
    )
    overlay = result.markup.split('class="map-overlay">', 1)[1]
    place_match = re.search(r'class="map-place[^"]*" data-camera-track="place"', overlay)
    marker_match = re.search(r'class="map-place[^"]*" data-camera-track="marker_0"', overlay)
    region_match = re.search(r'class="map-place[^"]*" data-camera-track="region_0"', overlay)
    assert place_match is not None and marker_match is not None and region_match is not None
    assert place_match.start() < marker_match.start()
    assert place_match.start() < region_match.start()


def test_a_synthetic_scene_with_unseparable_labels_reports_overlap():
    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "Malaysia",
            "bbox": "95,0.5,119,8",
            "markers": [
                {"text": f"Overlap {position}", "lon": 102.25, "lat": 2.19}
                for position in range(60)
            ],
        },
    }
    problems = map_scene_motion_problems(scene, DESIGN, width=1920, height=1080, duration=6.0)
    assert any("overlap" in problem for problem in problems)


def test_overlay_labels_include_markers_rivers_regions_and_focus():
    from video_editor_maps.scenes import _overlay_label_specs, _prepare

    scene = {
        "id": "scene_1",
        "type": "map",
        "props": {
            "country": "India",
            "bbox": "72,7,92,32",
            "markers": [{"text": "Marker", "lon": 77.0, "lat": 15.0}],
            "rivers": [{"name": "Ganges", "label": "Ganga"}],
            "regions": [{"name": "Deccan Plateau", "label": "Deccan"}],
            "focus": "78,16",
            "label": "Focus Place",
            "focus_pin": True,
        },
    }
    prepared = _prepare(scene, DESIGN, width=1920, height=1080, duration=6.0)
    texts = {spec.text for spec in _overlay_label_specs(prepared)}
    assert {"Marker", "Ganga", "Deccan", "Focus Place"} <= texts


# --- #342 gazetteer markers and land snapping, #343 worldview ------------------


def test_a_marker_without_coordinates_is_looked_up_in_the_gazetteer():
    from video_editor_maps.scenes import _markers

    markers = _markers(
        {
            "country": "India",
            "markers": [{"text": "Chennai"}, {"place": "Kolkata", "text": "Calcutta"}],
        }
    )
    assert markers[0].lon == pytest.approx(80.278, abs=0.01)
    assert markers[1].text == "Calcutta" and markers[1].lat == pytest.approx(22.57, abs=0.01)


def test_a_marker_prefers_the_scene_country_for_ambiguous_names():
    from video_editor_maps.scenes import _markers

    india = _markers({"country": "India", "markers": [{"text": "Hyderabad"}]})
    pakistan = _markers({"country": "Pakistan", "markers": [{"text": "Hyderabad"}]})
    assert india[0].lon > 75 and pakistan[0].lon < 70


def test_an_offshore_marker_snaps_onto_the_coast():
    from video_editor_maps.geo import on_land
    from video_editor_maps.scenes import _markers

    marker = _markers(
        {
            "country": "India",
            "worldview": "none",
            "markers": [{"text": "Chennai", "lon": 80.3, "lat": 13.1}],
        }
    )[0]
    assert on_land(marker.lon, marker.lat)


def test_a_city_far_at_sea_is_a_marker_problem_but_a_sea_label_is_not():
    from video_editor_maps.scenes import map_scene_marker_problems

    lost = {
        "id": "s",
        "props": {"country": "India", "markers": [{"text": "Port", "lon": 85.0, "lat": 15.0}]},
    }
    sea = {
        "id": "s",
        "props": {
            "country": "India",
            "markers": [{"text": "Bay of Bengal", "lon": 88.0, "lat": 15.0, "sea": True}],
        },
    }
    assert map_scene_marker_problems(lost) == [
        "s: marker 'Port' is in the sea (set sea: true for a sea label)"
    ]
    assert map_scene_marker_problems(sea) == []


def test_an_india_map_draws_indias_official_borders_by_default():
    from video_editor_maps.scenes import _worldview

    assert _worldview({"country": "India"}) == "ind"
    assert _worldview({"country": "IND"}) == "ind"
    assert _worldview({"country": "India", "worldview": "none"}) == ""
    assert _worldview({"country": "France"}) == ""
    with pytest.raises(MapSceneError, match="worldview"):
        _worldview({"country": "France", "worldview": "mars"})
    official = render({"country": "India"}).markup
    default = render({"country": "India", "worldview": "none"}).markup
    assert official != default


def test_a_relief_region_draws_soft_staggered_hills():
    result = render(
        {"country": "India", "regions": [{"name": "Western Ghats", "style": "relief"}]}
    ).markup
    assert 'id="map-region-relief-1"' in result
    tile = result.split('id="map-region-relief-1"', 1)[1].split("</pattern>", 1)[0]
    assert tile.count(" Q") == 2 and 'stroke-linecap="round"' in tile
    assert "url(#map-region-relief-1)" in result


def test_a_to_zoom_across_the_antimeridian_fails_closed():
    with pytest.raises(MapSceneError, match="antimeridian"):
        _zooms({"zooms": [{"to": "Russia"}]})


# --- bbox framing and one collision pass for labels and bubbles ----------------

TIFFIN_BBOX = "74,8,85,20"


def _bbox_scene(**extra) -> dict:
    return {
        "id": "scene_1",
        "type": "map",
        "props": {"country": "India", "bbox": TIFFIN_BBOX, **extra},
    }


def _projected_bbox_rect(prepared):
    west, south, east, north = parse_bounds(TIFFIN_BBOX)
    x0, y1 = prepared.view.project(west, south)
    x1, y0 = prepared.view.project(east, north)
    return x0, y0, x1, y1


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (1080, 1920)])
def test_a_bbox_fills_the_map_area_and_is_centred(width, height):
    from video_editor_maps.scenes import _prepare

    prepared = _prepare(_bbox_scene(), DESIGN, width=width, height=height, duration=6.0)
    frame = tuple(v * prepared.scale for v in prepared.geom.map_frame)
    x0, y0, x1, y1 = _projected_bbox_rect(prepared)
    fill_x = (x1 - x0) / frame[2]
    fill_y = (y1 - y0) / frame[3]
    assert max(fill_x, fill_y) >= 0.80  # the limiting axis is nearly full
    assert max(fill_x, fill_y) <= 1.0
    centre = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert abs(centre[0] - (frame[0] + frame[2] / 2.0)) <= 0.05 * frame[2]
    assert abs(centre[1] - (frame[1] + frame[3] / 2.0)) <= 0.05 * frame[3]


def test_a_bbox_map_area_is_the_plate_minus_the_title_block():
    from video_editor_maps.scenes import _prepare

    prepared = _prepare(_bbox_scene(), DESIGN, width=1920, height=1080, duration=6.0)
    x, y, w, h = prepared.geom.map_frame
    assert x + w / 2.0 == pytest.approx(prepared.geom.width / 2.0)  # centred on the plate
    assert y + h <= _LANDSCAPE.title[1]  # ends above the title block
    assert w > _LANDSCAPE.map_frame[2]  # wider than the default panel
    # callout cards keep their column
    with_cards = _prepare(
        _bbox_scene(callouts=[{"text": "one"}]), DESIGN, width=1920, height=1080, duration=6.0
    )
    assert with_cards.geom.map_frame[0] + with_cards.geom.map_frame[2] <= _LANDSCAPE.cards[0]


def test_a_zoom_bbox_scene_gets_the_widened_map_area_too():
    from video_editor_maps.scenes import _prepare

    scene = {
        "id": "s",
        "type": "map",
        "props": {"country": "India", "zooms": [{"bbox": TIFFIN_BBOX, "cue": False}]},
    }
    prepared = _prepare(scene, DESIGN, width=1920, height=1080, duration=6.0)
    assert prepared.geom.map_frame[2] > _LANDSCAPE.map_frame[2]
    assert prepared.camera_views
    landing = prepared.camera_views[0]
    west, south, east, north = parse_bounds(TIFFIN_BBOX)
    x0, y1 = landing.project(west, south)
    x1, y0 = landing.project(east, north)
    frame = landing.frame
    assert max((x1 - x0) / frame[2], (y1 - y0) / frame[3]) == pytest.approx(1.0)


def test_a_marker_outside_the_bbox_stays_in_the_window():
    from video_editor_maps.scenes import _prepare

    prepared = _prepare(
        _bbox_scene(markers=[{"text": "Far", "lon": 86.0, "lat": 15.0, "cue": False}]),
        DESIGN,
        width=1920,
        height=1080,
        duration=6.0,
    )
    assert prepared.view.contains(86.0, 15.0)


def test_overlapping_bubbles_are_nudged_apart_with_a_leader_line():
    from video_editor_maps.scenes import _bubble_box, _overlap, _resolve_bubbles
    from video_editor_maps.stickers import Bubble

    bubbles = [
        (Bubble(text="≈ ₹8,200 / day", lon=0.0, lat=0.0, cue=""), 900.0, 500.0, 0.5),
        (Bubble(text="≈ ₹8,000 / day", lon=0.0, lat=0.0, cue=""), 910.0, 505.0, 1.0),
    ]
    offsets = _resolve_bubbles(
        bubbles, [], [[]], scale=1.0, seconds=6.0, bounds=(0.0, 0.0, 1920.0, 1080.0)
    )
    assert offsets[0] == (0.0, 0.0)
    assert offsets[1] != (0.0, 0.0)
    boxes = [
        _bubble_box(b.text, (x, y), 1.0, offset)
        for (b, x, y, _), offset in zip(bubbles, offsets, strict=True)
    ]
    assert not _overlap(boxes[0], boxes[1])


def test_a_bubble_on_top_of_a_marker_label_is_nudged_clear_and_drawn_with_a_leader():
    result = render(
        {
            "country": "India",
            "bbox": TIFFIN_BBOX,
            "markers": [{"text": "Hyderabad", "lon": 78.47, "lat": 17.38, "cue": False}],
            "bubbles": [
                {"text": "≈ ₹8,200 / day", "at": "Hyderabad", "cue": False},
                {"text": "≈ ₹8,100 / day", "lon": 78.6, "lat": 17.3, "cue": False},
            ],
        }
    )
    assert result.markup.count('class="map-bubble"') == 2
    assert 'class="map-bubble-leader"' in result.markup
    assert ".map-bubble-leader" in result.css
