"""Journeys on a map: multi-stop routes, a km counter on the line, a travelling icon."""

from __future__ import annotations

import math
import re

import pytest
from video_editor_maps.journey import (
    JourneyError,
    curved_leg,
    journey_markers,
    journey_zooms,
    km_label,
    leg_km,
    parse_journey,
)

COIMBATORE = {"text": "Coimbatore", "lon": 76.96, "lat": 11.02}
CHENNAI = {"text": "Chennai", "lon": 80.27, "lat": 13.08}
ELURU = {"text": "Eluru", "lon": 81.10, "lat": 16.71, "km": 1000}


def _props(**extra):
    return {"journey": {"stops": [COIMBATORE, CHENNAI, ELURU], **extra}}


def test_a_journey_has_one_leg_per_stop_after_the_first() -> None:
    journey = parse_journey(_props())
    assert [leg.destination.text for leg in journey.legs] == ["Chennai", "Eluru"]
    assert journey.icon == "car" and journey.show_km is True and journey.follow is False


def test_a_journey_needs_two_stops_and_a_known_icon() -> None:
    with pytest.raises(JourneyError, match="at least two stops"):
        parse_journey({"journey": {"stops": [COIMBATORE]}})
    with pytest.raises(JourneyError, match="icon"):
        parse_journey(_props(icon="rocket"))


def test_a_leg_uses_the_authored_road_km_else_the_great_circle() -> None:
    journey = parse_journey(_props())
    first, second = journey.legs
    # Coimbatore -> Chennai is ~420 km as the crow flies; rounded to 10 km.
    assert 400 <= leg_km(first) <= 440 and leg_km(first) % 10 == 0
    assert leg_km(second) == 1000


def test_the_counter_rolls_from_the_previous_total_to_the_new_one() -> None:
    journey = parse_journey(_props())
    totals = [(leg.km_from, leg.km_to) for leg in journey.legs]
    assert totals[0][0] == 0 and totals[1][0] == totals[0][1]
    assert totals[1][1] == totals[0][1] + 1000
    assert km_label(1100) == "1,100 KM"


def test_stops_become_markers_cued_by_their_names() -> None:
    markers = journey_markers(parse_journey(_props()))
    assert [(m["text"], m["cue"]) for m in markers] == [
        ("Coimbatore", "Coimbatore"),
        ("Chennai", "Chennai"),
        ("Eluru", "Eluru"),
    ]


def test_follow_frames_each_leg_when_its_destination_is_spoken() -> None:
    zooms = journey_zooms(parse_journey(_props(follow=True)))
    assert zooms == [
        {"to": ["Coimbatore", "Chennai"], "cue": "Chennai", "pad": 0.4, "min_span": 5.0},
        {"to": ["Chennai", "Eluru"], "cue": "Eluru", "pad": 0.4, "min_span": 5.0},
    ]
    assert journey_zooms(parse_journey(_props())) == []


def test_a_curved_leg_keeps_its_ends_and_bows_to_one_side() -> None:
    points = curved_leg((0.0, 0.0), (100.0, 0.0), curve=0.2)
    assert points[0] == (0.0, 0.0) and points[-1] == (100.0, 0.0)
    middle = points[len(points) // 2]
    assert math.isclose(middle[0], 50.0, abs_tol=1.0)
    assert abs(middle[1]) == pytest.approx(10.0, abs=1.0)  # 0.2 x length / 2
    assert curved_leg((0.0, 0.0), (100.0, 0.0), curve=0.0) == [(0.0, 0.0), (100.0, 0.0)]


def test_via_points_shape_a_winding_road() -> None:
    journey = parse_journey(
        {"journey": {"stops": [COIMBATORE, {**CHENNAI, "via": [[78.1, 11.6], [79.4, 12.5]]}]}}
    )
    assert journey.legs[0].points == ((76.96, 11.02), (78.1, 11.6), (79.4, 12.5), (80.27, 13.08))


def test_the_map_scene_draws_legs_icon_and_counter() -> None:
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import render_map

    result = render_map(
        {"id": "s", "type": "map", "props": {"country": "India", **_props(follow=True)}},
        load_design("paper_editorial"),
        index=2,
        duration=8.0,
        width=1080,
        height=1920,
    )
    assert result.markup.count('class="map-leg map-leg-') == 2
    assert result.markup.count('class="map-journey-icon') == 2
    assert "1,000 KM" not in result.markup  # the first leg's total is computed
    assert re.search(r"1,\d{3} KM", result.markup)  # the second leg's running total
    assert "@keyframes map_leg_2_0" in result.css and "@keyframes map_leg_2_1" in result.css
    assert 'offset-path: path("M' in result.css


def _render(design: str):
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import render_map

    return render_map(
        {"id": "s", "type": "map", "props": {"country": "India", **_props()}},
        load_design(design),
        index=1,
        duration=6.0,
        width=1080,
        height=1920,
    )


def test_the_night_map_design_glows_and_colours_the_route() -> None:
    night = _render("night_map")
    assert "drop-shadow" in night.css
    assert 'stroke="#C6F35A"' in night.markup  # neon route
    assert "#0B3A36" in night.css  # dark teal sea


def test_existing_designs_keep_their_look() -> None:
    paper = _render("paper_editorial")
    assert "drop-shadow" not in paper.css
    assert 'class="map-leg map-leg-0"' in paper.markup
    assert 'stroke="#B8862F"' in paper.markup  # route falls back to the pin colour


def test_the_icon_counter_scales_against_the_camera_zoom() -> None:
    from video_editor_maps.journey import ProjectedLeg, legs_css

    leg = ProjectedLeg(
        path="M0 0L10 0", length=10.0, delay=0.5, draw=1.0, km_from=0, km_to=10, label_at=(5.0, 0.0)
    )
    css, _ = legs_css(
        [leg], index=3, seconds=4.0, icon="car", width_var="--map-route-width-3", base=4.0
    )
    assert "scale(calc(var(--map-route-width-3, 4) / 4))" in css
    assert "transform-box: fill-box" in css


def test_only_the_current_legs_counter_stays_on_screen() -> None:
    from video_editor_maps.journey import ProjectedLeg, km_markup

    first = ProjectedLeg(
        path="M0 0L1 0",
        length=1.0,
        delay=0.5,
        draw=1.0,
        km_from=0,
        km_to=100,
        label_at=(0.0, 0.0),
        until=2.0,
    )
    last = ProjectedLeg(
        path="M1 0L2 0",
        length=1.0,
        delay=2.0,
        draw=1.0,
        km_from=100,
        km_to=250,
        label_at=(1.0, 0.0),
    )
    markup, css = km_markup([first, last], index=1, seconds=4.0)
    assert "map_km_hide_1_0" in markup and "map_km_hide_1_0" in css
    assert "map_km_hide_1_1" not in markup  # the last total stays


def test_a_full_frame_map_fills_the_whole_plate() -> None:
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import _prepare

    prepared = _prepare(
        {"id": "s", "type": "map", "props": {"country": "India", "frame": "full"}},
        load_design("night_map"),
        width=1080,
        height=1920,
        duration=5.0,
    )
    assert prepared.geom.map_frame == (0.0, 0.0, 1080.0, 1920.0)


def test_follow_zooms_leave_room_around_each_leg() -> None:
    from video_editor_maps.journey import FOLLOW_PAD

    zooms = journey_zooms(parse_journey(_props(follow=True)))
    assert all(zoom["pad"] == FOLLOW_PAD for zoom in zooms) and FOLLOW_PAD >= 0.35


def test_the_glow_does_not_swell_with_the_zoom() -> None:
    night = render_follow = _render_follow("night_map")
    assert "drop-shadow(0 0 calc(" in night.css and "var(--map-route-width-1" in night.css
    del render_follow


def _render_follow(design: str):
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import render_map

    return render_map(
        {"id": "s", "type": "map", "props": {"country": "India", **_props(follow=True)}},
        load_design(design),
        index=1,
        duration=8.0,
        width=1080,
        height=1920,
    )


def test_river_names_match_without_their_diacritics() -> None:
    from video_editor_maps.geo import find_river

    assert find_river("Godavari")[0].name == "Godävari"
    assert find_river("Mahanadi")[0].name == "Mahäna Nadï"


def _course(name: str):
    # A straight "river" from (0, 0) to (10, 0), sampled every unit.
    assert name == "Test River"
    return [(float(x), 0.0) for x in range(11)]


def test_a_leg_along_a_river_traces_its_course_between_the_stops() -> None:
    journey = parse_journey(
        {
            "journey": {
                "stops": [
                    {"text": "A", "lon": 2.1, "lat": 0.3},
                    {"text": "B", "lon": 7.9, "lat": -0.2, "along": "Test River"},
                ]
            }
        },
        river_course=_course,
    )
    points = journey.legs[0].points
    assert points[0] == (2.1, 0.3) and points[-1] == (7.9, -0.2)
    assert (5.0, 0.0) in points  # the river's own vertices in between


def test_total_km_scales_measured_legs_to_the_documented_length() -> None:
    journey = parse_journey(
        {
            "journey": {
                "total_km": 1465,
                "stops": [
                    {"text": "A", "lon": 0.0, "lat": 0.0},
                    {"text": "B", "lon": 3.0, "lat": 0.0, "along": "Test River"},
                    {"text": "C", "lon": 10.0, "lat": 0.0, "along": "Test River"},
                ],
            }
        },
        river_course=_course,
    )
    assert journey.legs[-1].km_to == 1465
    first = journey.legs[0].km_to
    assert 400 <= first <= 480  # 3 of 10 units of the course


def test_along_needs_a_river_lookup() -> None:
    with pytest.raises(JourneyError, match="along"):
        parse_journey(
            {
                "journey": {
                    "stops": [
                        {"text": "A", "lon": 0, "lat": 0},
                        {"text": "B", "lon": 1, "lat": 0, "along": "X"},
                    ]
                }
            }
        )


def test_a_godavari_journey_follows_the_real_river() -> None:
    from video_editor_core.designs import load_design
    from video_editor_maps.scenes import render_map

    props = {
        "country": "India",
        "frame": "full",
        "journey": {
            "icon": "none",
            "total_km": 1465,
            "stops": [
                {"text": "Trimbakeshwar", "lon": 73.53, "lat": 19.93},
                {"text": "Bhadrachalam", "lon": 80.89, "lat": 17.67, "along": "Godavari"},
                {"text": "Rajahmundry", "lon": 81.80, "lat": 17.00, "along": "Godavari"},
            ],
        },
    }
    result = render_map(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        index=1,
        duration=8.0,
        width=1080,
        height=1920,
    )
    first_leg = re.search(r'class="map-leg map-leg-0" d="([^"]+)"', result.markup).group(1)
    assert first_leg.count("L") > 40  # the river's bends, not a two-point bow
    assert "1,465 KM" in result.markup


def test_a_zoom_can_insist_on_a_minimum_window() -> None:
    from video_editor_maps.scenes import _padded_bounds

    west, south, east, north = _padded_bounds([(80.9, 17.0, 81.8, 17.7)], 0.4, min_span=5.0)
    assert east - west >= 5.0 and north - south >= 5.0
    # Centred on the features.
    assert abs((west + east) / 2 - 81.35) < 0.01


def test_follow_zooms_never_frame_less_than_the_minimum() -> None:
    from video_editor_maps.journey import FOLLOW_MIN_SPAN

    zooms = journey_zooms(parse_journey(_props(follow=True)))
    assert all(zoom["min_span"] == FOLLOW_MIN_SPAN for zoom in zooms) and FOLLOW_MIN_SPAN >= 4.0
