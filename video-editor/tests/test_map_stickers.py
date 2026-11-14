"""Speech bubbles, stickers and a heart-shaped leg on map scenes."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PIL import Image
from video_editor_core.designs import load_design
from video_editor_maps.journey import heart_leg, parse_journey
from video_editor_maps.scenes import MapSceneError, render_map
from video_editor_maps.stickers import ICONS, StickerError, parse_overlays


def test_bubbles_and_stickers_anchor_to_a_named_place_or_a_coordinate() -> None:
    places = {"eluru": (81.10, 16.71)}
    bubbles, stickers = parse_overlays(
        {
            "bubbles": [{"text": "నమస్కారం", "at": "Eluru"}],
            "stickers": [{"icon": "laptop", "lon": 80.1, "lat": 13.2, "cue": "deadlines"}],
        },
        places,
    )
    assert (bubbles[0].text, bubbles[0].lon, bubbles[0].lat) == ("నమస్కారం", 81.10, 16.71)
    assert stickers[0].icon == "laptop" and stickers[0].cue == "deadlines"


def test_an_unknown_place_or_icon_is_refused() -> None:
    with pytest.raises(StickerError, match="Chennai"):
        parse_overlays({"bubbles": [{"text": "hi", "at": "Chennai"}]}, {})
    with pytest.raises(StickerError, match="icon"):
        parse_overlays({"stickers": [{"icon": "rocket", "lon": 1, "lat": 1}]}, {})


def test_the_icon_set_covers_the_reels_props() -> None:
    assert {"laptop", "tea", "family", "heart", "temple", "phone", "chat"} <= ICONS


def test_a_custom_image_sticker_is_embedded(tmp_path: Path) -> None:
    photo = tmp_path / "car.png"
    Image.new("RGBA", (300, 200), (220, 40, 50, 255)).save(photo)
    _, stickers = parse_overlays({"stickers": [{"image": str(photo), "lon": 1, "lat": 1}]}, {})
    assert stickers[0].image.startswith("data:image/png;base64,")


def test_a_heart_leg_starts_and_ends_on_its_stops_and_loops_between() -> None:
    points = heart_leg((0.0, 0.0), (200.0, 0.0))
    assert points[0] == (0.0, 0.0) and points[-1] == (200.0, 0.0)
    ys = [y for _x, y in points]
    assert min(ys) < -30  # the loop rises well above the chord (screen y is down)
    xs = [x for x, _y in points]
    assert max(xs) - min(xs) >= 200


def test_a_stop_can_ask_for_a_heart_in_its_own_colour() -> None:
    journey = parse_journey(
        {
            "journey": {
                "stops": [
                    {"text": "Coimbatore", "lon": 76.96, "lat": 11.02},
                    {
                        "text": "Chennai",
                        "lon": 80.27,
                        "lat": 13.08,
                        "shape": "heart",
                        "color": "#FF7A6B",
                    },
                ]
            }
        }
    )
    leg = journey.legs[0]
    assert leg.destination.shape == "heart" and leg.destination.color == "#FF7A6B"
    with pytest.raises(ValueError, match="shape"):
        parse_journey(
            {
                "journey": {
                    "stops": [
                        {"text": "A", "lon": 1, "lat": 1},
                        {"text": "B", "lon": 2, "lat": 2, "shape": "star"},
                    ]
                }
            }
        )


def _render(props: dict):
    return render_map(
        {"id": "s", "type": "map", "props": {"country": "India", "frame": "full", **props}},
        load_design("night_map"),
        index=1,
        duration=6.0,
        width=1080,
        height=1920,
    )


def test_the_scene_draws_bubbles_and_stickers_on_camera_tracks() -> None:
    result = _render(
        {
            "markers": [{"text": "Eluru", "lon": 81.10, "lat": 16.71}],
            "bubbles": [{"text": "నమస్కారం", "at": "Eluru"}],
            "stickers": [{"icon": "tea", "at": "Eluru"}],
        }
    )
    assert 'data-camera-track="bubble_0"' in result.markup and "నమస్కారం" in result.markup
    assert 'data-camera-track="sticker_0"' in result.markup and "<svg" in result.markup
    assert "@keyframes map_pop_1" in result.css


def test_the_heart_leg_draws_in_its_colour() -> None:
    result = _render(
        {
            "journey": {
                "icon": "none",
                "stops": [
                    {"text": "Coimbatore", "lon": 76.96, "lat": 11.02},
                    {
                        "text": "Chennai",
                        "lon": 80.27,
                        "lat": 13.08,
                        "shape": "heart",
                        "color": "#FF7A6B",
                    },
                ],
            }
        }
    )
    assert 'stroke="#FF7A6B"' in result.markup


def test_a_bubble_at_an_unknown_place_is_a_scene_error() -> None:
    with pytest.raises(MapSceneError, match="Hyderabad"):
        _render({"bubbles": [{"text": "hi", "at": "Hyderabad"}]})


def test_bubble_text_is_escaped() -> None:
    result = _render({"bubbles": [{"text": "<b>x</b>", "lon": 80.0, "lat": 13.0}]})
    assert "<b>x</b>" not in result.markup and "&lt;b&gt;" in result.markup
    assert not math.isnan(0.0)
