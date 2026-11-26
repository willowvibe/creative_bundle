"""Large photo cards that pop in on map-scene cue words."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image
from video_editor_core.designs import load_design
from video_editor_core.safe_area import SHORTS_UI_SAFE_BOTTOM, caption_top
from video_editor_maps.cards import card_bottom, card_fit_band
from video_editor_maps.geo import find_country
from video_editor_maps.scenes import (
    MapSceneError,
    _coverage_bounds,
    _geometry_frame,
    _prepare,
    render_map,
)

CARD_TITLE = "తిరుపతి లడ్డూ"
SECOND_TITLE = "నెల్లూరు చేపల పులుసు"


def _photo(tmp_path: Path, name: str = "dish.jpg") -> Path:
    path = tmp_path / name
    Image.new("RGB", (1600, 1200), (200, 120, 40)).save(path)
    return path


def _render(cards: list | None = None, *, words: list | None = None, duration: float = 6.0):
    props: dict = {"country": "India", "frame": "full"}
    if cards is not None:
        props["cards"] = cards
    return render_map(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        index=1,
        duration=duration,
        width=1080,
        height=1920,
        words=words,
    )


def test_one_card_element_per_entry_with_title_and_badge(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    result = _render(
        [
            {"image": str(photo), "title": CARD_TITLE, "badge": "GI", "cue": "తిరుపతి"},
            {"image": str(photo), "title": SECOND_TITLE, "cue": "నెల్లూరు"},
        ]
    )
    assert result.markup.count('class="map-photo-card"') == 2
    assert 'data-card="0"' in result.markup and 'data-card="1"' in result.markup
    assert CARD_TITLE in result.markup and SECOND_TITLE in result.markup
    assert 'class="map-photo-card-badge"' in result.markup and ">GI<" in result.markup
    assert result.markup.count('src="data:image/jpeg;base64,') == 2
    assert "@keyframes map_photo_card_in_1_0" in result.css
    assert "@keyframes map_photo_card_out_1_1" in result.css


def test_the_card_allows_intentional_overlap_and_its_title_can_grow(
    tmp_path: Path,
) -> None:
    photo = _photo(tmp_path)
    result = _render([{"image": str(photo), "title": CARD_TITLE, "cue": "తిరుపతి"}])
    assert result.markup.count("data-layout-allow-overlap") == 1
    assert 'class="map-photo-card" data-card="0" data-layout-allow-overlap' in result.markup

    match = re.search(r"\.map-1 \.map-photo-card-title \{([^}]*)\}", result.css)
    assert match is not None
    title_rule = match.group(1)
    assert "line-height:1.35" in title_rule
    assert "height:auto" in title_rule
    assert not re.search(r"(?<!-)\bheight:\s*[\d.]+(?:px|em)", title_rule)


def test_long_titles_are_scaled_down_to_fit(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    long_title = "భీమవరం రొయ్యల వేపుడు"
    result = _render([{"image": str(photo), "title": long_title, "cue": "భీమవరం"}])
    assert long_title in result.markup
    match = re.search(
        r'<div class="map-photo-card-title[^"]*" style="font-size:([\d.]+)px">', result.markup
    )
    assert match is not None
    assert 0.0 < float(match.group(1)) < 56.7


def test_entry_and_exit_times_come_from_the_word_timings(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    words = [
        {"word": "తిరుపతి", "start": 1.0, "end": 1.4},
        {"word": "నెల్లూరు", "start": 3.0, "end": 3.4},
    ]
    result = _render(
        [
            {
                "image": str(photo),
                "title": CARD_TITLE,
                "cue": "తిరుపతి",
                "hold": 1.5,
            },
            {"image": str(photo), "title": SECOND_TITLE, "cue": "నెల్లూరు"},
        ],
        words=words,
    )
    assert 'data-card-start="1.000"' in result.markup
    assert 'data-card-exit="2.500"' in result.markup
    assert 'data-card-start="3.000"' in result.markup
    assert 'data-card-exit="5.700"' in result.markup
    assert "1.000s both" in result.markup and "2.500s forwards" in result.markup


def test_the_last_card_holds_to_the_scene_end_minus_three_tenths(tmp_path: Path) -> None:
    photo = _photo(tmp_path)
    words = [{"word": "తిరుపతి", "start": 1.0, "end": 1.4}]
    result = _render(
        [{"image": str(photo), "title": CARD_TITLE, "cue": "తిరుపతి"}],
        words=words,
        duration=4.0,
    )
    assert 'data-card-start="1.000"' in result.markup
    assert 'data-card-exit="3.700"' in result.markup


def test_a_scene_without_cards_renders_exactly_as_before(tmp_path: Path) -> None:
    plain = _render()
    empty = _render([])
    assert plain.markup == empty.markup
    assert plain.css == empty.css
    assert "map-photo-card" not in plain.markup
    assert "map-photo-card" not in plain.css


BBOX = [79.0, 13.0, 80.5, 14.5]


def _corners_y(cards_fit: str | None, tmp_path: Path) -> tuple[float, float]:
    props: dict = {
        "country": "India",
        "bbox": BBOX,
        "cards": [{"image": str(_photo(tmp_path)), "title": CARD_TITLE, "cue": "తిరుపతి"}],
    }
    if cards_fit:
        props["cards_fit"] = cards_fit
    prepared = _prepare(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        width=1080,
        height=1920,
        duration=6.0,
    )
    west, south, east, north = BBOX
    ys = [
        prepared.plate_y + prepared.view.project(lon, lat)[1]
        for lon in (west, east)
        for lat in (south, north)
    ]
    return min(ys), max(ys)


def test_a_card_scene_fits_its_bbox_between_the_card_and_the_captions(tmp_path: Path) -> None:
    top, bottom = _corners_y(None, tmp_path)
    card_box_bottom = card_bottom(1080, 1920) + 1920 * 0.02
    assert top >= card_box_bottom - 1.0
    assert bottom <= caption_top(1080, 1920) + 1.0


def test_cards_fit_frame_restores_the_full_frame_fit(tmp_path: Path) -> None:
    band_top, band_bottom = _corners_y(None, tmp_path)
    full_top, full_bottom = _corners_y("frame", tmp_path)
    assert (full_bottom - full_top) > (band_bottom - band_top)
    assert full_top < card_bottom(1080, 1920)


def test_a_missing_card_image_is_a_scene_error_with_the_card_index() -> None:
    with pytest.raises(MapSceneError, match="card 0"):
        _render([{"image": "/no/such/photo.jpg", "title": CARD_TITLE}])


def test_band_bottom_is_caption_top_when_burned_and_shorts_ui_when_not() -> None:
    burned = card_fit_band(1080, 1920)
    sidecar = card_fit_band(1080, 1920, captions_burned=False)
    assert burned is not None and sidecar is not None
    assert burned[1] == caption_top(1080, 1920) == int(1920 * 0.72)
    assert sidecar[1] == int(1920 * SHORTS_UI_SAFE_BOTTOM) == int(1920 * 0.80)
    assert sidecar[0] == burned[0]
    # Landscape is unchanged either way.
    wide = card_fit_band(1920, 1080, captions_burned=False)
    assert wide is not None and wide[1] == caption_top(1920, 1080)


def test_cards_band_bottom_override_is_clamped() -> None:
    assert card_fit_band(1080, 1920, bottom_fraction=0.9)[1] == pytest.approx(1920 * 0.9)
    assert card_fit_band(1080, 1920, bottom_fraction=0.99)[1] == pytest.approx(1920 * 0.95)
    low = card_fit_band(1080, 1920, bottom_fraction=0.3)
    assert low is not None and low[1] == pytest.approx(1920 * 0.5)


def _prepared_bottom(tmp_path: Path, **extra) -> float:
    props = {
        "country": "India",
        "frame": "full",
        "bbox": [74.0, 8.0, 86.0, 20.0],
        "cards": [{"image": str(_photo(tmp_path)), "title": CARD_TITLE}],
        **extra,
    }
    prepared = _prepare(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        width=1080,
        height=1920,
        duration=6.0,
    )
    ys = [
        prepared.plate_y + prepared.view.project(lon, lat)[1]
        for lon in (74.0, 86.0)
        for lat in (8.0, 20.0)
    ]
    return max(ys)


def test_scene_fit_uses_burned_not_burned_and_override_bottoms(tmp_path: Path) -> None:
    burned = _prepared_bottom(tmp_path)
    assert burned <= 1920 * 0.72 + 1.0
    sidecar = _prepared_bottom(tmp_path, _captions_burned=False)
    assert 1920 * 0.72 + 1.0 < sidecar <= 1920 * 0.80 + 1.0
    override = _prepared_bottom(tmp_path, cards_band={"bottom": 0.9})
    assert sidecar < override <= 1920 * 0.90 + 1.0


def _card_scene_props(tmp_path: Path) -> dict:
    return {
        "country": "India",
        "frame": "full",
        "bbox": [78.8, 13.2, 81.0, 17.2],
        "regions": [{"district": "Guntur", "label": "GUNTUR"}],
        "cards": [{"image": str(_photo(tmp_path)), "title": CARD_TITLE}],
    }


def _prepared_card_scene(tmp_path: Path):
    props = _card_scene_props(tmp_path)
    prepared = _prepare(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        width=1080,
        height=1920,
        duration=6.0,
    )
    return props, prepared


def _distance_to_path(x: float, y: float, path_d: str) -> float:
    """Smallest distance from ``(x, y)`` to the segments of an M/L/Z path."""
    best = float("inf")
    for ring in re.findall(r"M[^M]+", path_d):
        points = [
            (float(a), float(b))
            for a, b in re.findall(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)", ring)
        ]
        for (x1, y1), (x2, y2) in zip(points, [*points[1:], points[0]], strict=True):
            dx, dy = x2 - x1, y2 - y1
            length = dx * dx + dy * dy
            t = 0.0 if length == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length))
            best = min(best, ((x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2) ** 0.5)
    return best


def test_card_scene_land_uses_the_regions_projection(tmp_path: Path) -> None:
    props, prepared = _prepared_card_scene(tmp_path)
    result = render_map(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        index=1,
        duration=6.0,
        width=1080,
        height=1920,
    )
    subject = re.search(r'<g class="map-subject"[^>]*>(.*?)</g>', result.markup, re.S)
    assert subject is not None

    # A real 50m coastline vertex near the Krishna mouth stands in for the
    # named point (this small-bbox scene draws its outline from the 50m set);
    # every layer must project it with the regions' view.
    india = find_country("India", detail="50m")
    coast = min(
        (point for polygon in india.polygons for ring in polygon for point in ring),
        key=lambda point: (point[0] - 80.3) ** 2 + (point[1] - 15.9) ** 2,
    )
    x, y = prepared.view.project(*coast)

    # The land layer may be drawn from a finer outline, so compare geometry:
    # the projected vertex must lie on (within 2 px of) the drawn coastline.
    distance = min(
        _distance_to_path(x, y, path_d)
        for path_d in re.findall(r'<path d="([^"]+)"', subject.group(1))
    )
    assert distance <= 2.0, distance


def test_card_scene_land_clip_covers_the_whole_map_frame(tmp_path: Path) -> None:
    props, prepared = _prepared_card_scene(tmp_path)
    result = render_map(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        index=1,
        duration=6.0,
        width=1080,
        height=1920,
    )
    match = re.search(
        r'<clipPath id="map-clip-1"><rect x="([\d.-]+)" y="([\d.-]+)" '
        r'width="([\d.-]+)" height="([\d.-]+)"/>',
        result.markup,
    )
    assert match is not None
    _x, y, _w, height = (float(value) for value in match.groups())
    frame_y = prepared.geom.map_frame[1] * prepared.scale
    frame_h = prepared.geom.map_frame[3] * prepared.scale
    assert y <= frame_y + 0.1
    assert height >= frame_h - 0.1


def test_a_scene_without_cards_keeps_the_committed_geometry() -> None:
    props = {"country": "India"}
    prepared = _prepare(
        {"id": "s", "type": "map", "props": props},
        load_design("night_map"),
        width=1080,
        height=1920,
        duration=6.0,
    )
    map_frame_px = (
        prepared.geom.map_frame[0] * prepared.scale,
        prepared.geom.map_frame[1] * prepared.scale,
        prepared.geom.map_frame[2] * prepared.scale,
        prepared.geom.map_frame[3] * prepared.scale,
    )
    clip_frame = _geometry_frame(prepared.view.frame, map_frame_px, prepared.camera_moves)
    assert clip_frame == prepared.view.frame
    assert _coverage_bounds(prepared.view, clip_frame) == prepared.view.bounds
