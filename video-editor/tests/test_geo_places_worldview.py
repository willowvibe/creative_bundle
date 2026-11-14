"""#342 gazetteer + land snapping, #343 India point-of-view borders."""

from __future__ import annotations

import pytest
from video_editor_maps.geo import (
    GeoError,
    countries,
    find_country,
    find_place,
    on_land,
    snap_to_land,
)


def _inside(country, lon: float, lat: float) -> bool:
    from video_editor_maps.geo import point_in_country

    return point_in_country(country, lon, lat)


def test_find_place_resolves_cities_by_name_and_country():
    chennai = find_place("Chennai")
    assert chennai.adm0 == "IND"
    assert chennai.lon == pytest.approx(80.278, abs=0.01)
    assert find_place("Hyderabad", country="IND").adm0 == "IND"
    assert find_place("Hyderabad", country="PAK").adm0 == "PAK"
    with pytest.raises(GeoError, match="Atlantis"):
        find_place("Atlantis")


def test_the_scripts_offshore_chennai_snaps_onto_the_coast():
    assert not on_land(80.3, 13.1)
    lon, lat = snap_to_land(80.3, 13.1, max_km=15)
    assert on_land(lon, lat)
    assert abs(lon - 80.3) < 0.15 and abs(lat - 13.1) < 0.15


def test_a_point_far_out_at_sea_does_not_snap():
    assert snap_to_land(85.0, 15.0, max_km=15) is None


def test_india_worldview_draws_indias_claimed_borders():
    default = find_country("India", "50m")
    official = find_country("India", "50m", worldview="ind")
    gilgit = (74.3, 35.9)  # administered by Pakistan, claimed by India
    aksai_chin = (79.5, 35.3)  # administered by China, claimed by India
    assert not _inside(default, *gilgit)
    assert _inside(official, *gilgit) and _inside(official, *aksai_chin)
    pakistan = find_country("Pakistan", "50m", worldview="ind")
    assert not _inside(pakistan, *gilgit)
    # Countries outside the replaced set are the regular 1:50m features.
    assert find_country("France", "50m", worldview="ind") is find_country("France", "50m")
    assert len(countries("50m", worldview="ind")) == len(countries("50m"))


def test_unknown_worldview_is_an_error():
    with pytest.raises(GeoError, match="worldview"):
        countries("50m", worldview="xyz")


def test_find_place_accepts_a_country_name_or_code():
    assert find_place("Hyderabad", country="Pakistan").adm0 == "PAK"
    assert find_place("Hyderabad", country="ind").adm0 == "IND"
