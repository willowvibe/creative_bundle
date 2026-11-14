"""The India states/districts build: simplify, assign districts, mark the disputed north."""

from __future__ import annotations

import struct
from pathlib import Path

from video_editor_maps.geo.build_india_admin import (
    DISPUTED_STATES,
    assign_state,
    read_dbf,
    read_shp_polygons,
    rings_to_polygons,
    simplify_ring,
)


def test_simplify_drops_points_on_a_straight_edge_and_keeps_corners() -> None:
    ring = [(0.0, 0.0), (0.5, 0.0001), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    simplified = simplify_ring(ring, tolerance=0.01)
    assert (0.5, 0.0001) not in simplified
    assert simplified[0] == simplified[-1] == (0.0, 0.0)
    assert (1.0, 1.0) in simplified


def test_a_tiny_ring_is_never_simplified_below_a_triangle() -> None:
    ring = [(0.0, 0.0), (0.001, 0.0), (0.001, 0.001), (0.0, 0.0)]
    assert len(simplify_ring(ring, tolerance=1.0)) >= 4


def test_holes_attach_to_the_outer_ring_that_contains_them() -> None:
    outer = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)]  # clockwise
    hole = [(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0), (2.0, 2.0)]  # counter-clockwise
    assert rings_to_polygons([outer, hole]) == [[outer, hole]]


def test_a_district_goes_to_the_state_that_contains_it() -> None:
    states = {
        "Left": [[[(0.0, 0.0), (0.0, 5.0), (5.0, 5.0), (5.0, 0.0), (0.0, 0.0)]]],
        "Right": [[[(5.0, 0.0), (5.0, 5.0), (10.0, 5.0), (10.0, 0.0), (5.0, 0.0)]]],
    }
    assert assign_state((7.0, 2.0), states) == "Right"
    assert assign_state((50.0, 50.0), states) is None


def test_the_disputed_north_is_marked() -> None:
    assert {"Jammu and Kashmir", "Ladakh"} <= DISPUTED_STATES


def _write_tiny_shapefile(folder: Path) -> tuple[Path, Path]:
    """One square polygon record plus a one-field dbf naming it."""
    points = [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    content = struct.pack("<i4d", 5, 0.0, 0.0, 1.0, 1.0) + struct.pack("<ii", 1, len(points))
    content += struct.pack("<i", 0) + b"".join(struct.pack("<2d", *p) for p in points)
    record = struct.pack(">ii", 1, len(content) // 2) + content
    header = struct.pack(">i", 9994) + b"\0" * 20 + struct.pack(">i", (100 + len(record)) // 2)
    header += struct.pack("<ii", 1000, 5) + struct.pack("<4d", 0, 0, 1, 1) + b"\0" * 32
    shp = folder / "t.shp"
    shp.write_bytes(header + record)
    field = b"name".ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([10]) + b"\0" * 15
    dbf_header = struct.pack("<BBBBIHH", 3, 0, 0, 0, 1, 32 + 32 + 1, 11) + b"\0" * 20
    dbf = folder / "t.dbf"
    dbf.write_bytes(dbf_header + field + b"\r" + b" " + b"Square".ljust(10) + b"\x1a")
    return shp, dbf


def test_the_shapefile_reader_returns_polygons_and_attributes(tmp_path: Path) -> None:
    shp, dbf = _write_tiny_shapefile(tmp_path)
    assert read_dbf(dbf, ["name"]) == [{"name": "Square"}]
    polygons = read_shp_polygons(shp)
    assert polygons[0][0][:2] == [(0.0, 0.0), (0.0, 1.0)]
