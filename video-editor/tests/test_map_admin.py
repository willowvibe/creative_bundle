"""States and districts on a map: fills, photo masks, and the disputed north."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from video_editor_core.designs import load_design
from video_editor_maps.geo import GeoError, find_admin
from video_editor_maps.scenes import MapSceneError, render_map


def test_states_and_districts_resolve_case_and_spacing_insensitively() -> None:
    assert find_admin("tamil nadu", "state").name == "Tamil Nadu"
    assert find_admin("ANDHRA  PRADESH", "state").kind == "state"
    district = find_admin("coimbatore", "district")
    assert district.name == "Coimbatore" and district.kind == "district"


def test_an_unknown_name_suggests_the_close_spelling() -> None:
    with pytest.raises(GeoError, match="Thanjavur"):
        find_admin("Thanjavoor", "district")


def test_the_disputed_north_is_refused_with_the_reason() -> None:
    with pytest.raises(GeoError, match="de facto"):
        find_admin("Ladakh", "state")
    with pytest.raises(GeoError, match="de facto"):
        find_admin("Srinagar", "district")


def _render(regions: list, design: str = "night_map"):
    return render_map(
        {
            "id": "s",
            "type": "map",
            "props": {"country": "India", "frame": "full", "regions": regions},
        },
        load_design(design),
        index=1,
        duration=6.0,
        width=1080,
        height=1920,
    )


def test_states_fill_in_their_own_colours_with_a_glowing_border() -> None:
    result = _render(
        [
            {"state": "Andhra Pradesh", "color": "#5E6B2E", "label": "ANDHRA PRADESH"},
            {"state": "Tamil Nadu", "color": "#14544C"},
        ]
    )
    assert 'fill="#5E6B2E"' in result.markup and 'fill="#14544C"' in result.markup
    assert "ANDHRA PRADESH" in result.markup
    assert ".map-region-outline" in result.css and "drop-shadow" in result.css


def test_a_photo_fills_a_district_shape(tmp_path: Path) -> None:
    photo = tmp_path / "temple.jpg"
    Image.new("RGB", (1600, 1000), (200, 120, 40)).save(photo)
    result = _render([{"district": "Dindigul", "image": str(photo), "label": "DINDIGUL DISTRICT"}])
    assert "<clipPath" in result.markup
    assert 'href="data:image/jpeg;base64,' in result.markup
    assert 'preserveAspectRatio="xMidYMid slice"' in result.markup


def test_a_missing_photo_is_a_scene_error() -> None:
    with pytest.raises(MapSceneError, match="image"):
        _render([{"district": "Dindigul", "image": "/no/such/photo.jpg"}])


def test_a_disputed_state_cannot_be_filled() -> None:
    with pytest.raises(MapSceneError, match="de facto"):
        _render([{"state": "Jammu and Kashmir"}])


def test_a_zoom_can_frame_a_district_or_state_by_name() -> None:
    from video_editor_maps.scenes import _named_zoom_bounds

    west, south, east, north = _named_zoom_bounds("district:Coimbatore", [])
    assert 76.3 < west < 77.0 and 77.0 < east < 77.6 and 10.0 < south < 10.8 and 11.0 < north < 11.6
    tn = _named_zoom_bounds("Tamil Nadu", [])  # bare names fall back to states
    assert tn[0] < 77.0 and tn[2] > 80.0


@pytest.mark.parametrize(
    ("asked", "found"),
    [
        ("Hyderabad", "Hydrabad"),
        ("Bengaluru", "Bangalore"),
        ("Bengaluru Urban", "Bangalore"),
        ("Mysuru", "Mysore"),
        ("Prayagraj", "Allahabad"),
        ("Gurugram", "Gurgaon"),
        ("Thoothukudi", "Thoothukkudi"),
        ("Nellore", "Sri Potti Sri Ramulu Nellore"),
        ("Kadapa", "YSR Kadapa"),
        ("Kadapa(YSR)", "YSR Kadapa"),
        ("YSR Kadapa", "YSR Kadapa"),
        ("Konaseema", "Dr. B.R. Ambedkar Konaseema"),
        ("Anantapur", "Anantapuramu"),
        ("Eluru", "Eluru"),
    ],
)
def test_current_and_common_names_find_the_sources_district(asked: str, found: str) -> None:
    assert find_admin(asked, "district").name == found


def test_andhra_pradesh_has_the_current_28_districts() -> None:
    from video_editor_maps.geo import admin_units

    ap = {unit.region.name for unit in admin_units("district") if unit.state == "Andhra Pradesh"}
    assert len(ap) == 28
    assert {"Polavaram", "Markapuram", "Eluru", "Kakinada", "NTR"} <= ap


def test_the_replaced_state_does_not_change_the_other_states() -> None:
    from video_editor_maps.geo import admin_units

    units = admin_units("district")
    assert len(units) == 735 - 13 + 28
    assert len([unit for unit in units if unit.state == "Telangana"]) == 33


def test_every_alias_points_at_a_real_district() -> None:
    from video_editor_maps.geo import DISTRICT_ALIASES, admin_units

    names = {unit.region.name for unit in admin_units("district")}
    assert set(DISTRICT_ALIASES.values()) <= names


def test_the_zooming_layers_are_not_frozen_as_one_bitmap() -> None:
    # ``will-change: transform`` on the stage or camera made Chrome stretch one
    # bitmap through every zoom, so district photos went blocky at close range.
    result = _render([{"state": "Kerala"}])
    assert "will-change:transform" not in result.css


def test_place_labels_get_a_backing_when_photos_are_on_the_map(tmp_path: Path) -> None:
    import re

    photo = tmp_path / "t.jpg"
    Image.new("RGB", (400, 300), (250, 240, 220)).save(photo)
    plain = render_map(
        {
            "id": "s",
            "type": "map",
            "props": {
                "country": "India",
                "markers": [{"text": "Chennai", "lon": 80.27, "lat": 13.08}],
            },
        },
        load_design("night_map"),
        index=1,
        duration=5.0,
        width=1080,
        height=1920,
    )
    with_photo = render_map(
        {
            "id": "s",
            "type": "map",
            "props": {
                "country": "India",
                "markers": [{"text": "Chennai", "lon": 80.27, "lat": 13.08}],
                "regions": [{"district": "Chennai", "image": str(photo)}],
            },
        },
        load_design("night_map"),
        index=1,
        duration=5.0,
        width=1080,
        height=1920,
    )
    assert not re.search(r"\.map-place \{[^}]*background:", plain.css)
    assert re.search(r"\.map-place \{[^}]*background:", with_photo.css)


def test_the_headline_leaves_room_for_marks_below_the_line() -> None:
    import re

    result = _render([{"state": "Kerala"}])
    rule = re.search(r"\.map-headline \{([^}]*)\}", result.css).group(1)
    assert "padding-bottom" in rule
