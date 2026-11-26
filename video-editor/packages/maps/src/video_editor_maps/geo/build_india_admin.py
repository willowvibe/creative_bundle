"""Build the bundled India states and districts from the downloaded sources.

Run once, offline, after downloading (see ``geo/INDIA_ADMIN_SOURCES.md``)::

    uv run python -m video_editor_maps.geo.build_india_admin \\
        --states ne_10m_admin_1_states_provinces.shp \\
        --districts geoBoundaries-IND-ADM2_simplified.geojson \\
        --out packages/maps/src/video_editor_maps/geo

Stdlib only: a polygon shapefile is a simple binary format, so the build reads
it directly rather than pulling in a GIS stack for a one-off step. States come
from Natural Earth 10m admin-1 (public domain; current, with Telangana).
Districts come from geoBoundaries IND ADM2 (ODbL 1.0) and carry no state
name, so each is assigned to the state containing its largest ring's centroid.

Natural Earth draws admin-1 *de facto*: Jammu and Kashmir and Ladakh stop at
the Line of Control, while the map plate uses India's own outline. Those units
(and their districts) are marked ``disputed`` and the renderer refuses to fill
them, so a scene can never show a border that contradicts the base map.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

Point = tuple[float, float]
Ring = list[Point]
Polygon = list[Ring]

#: Natural Earth's admin-1 lines for these follow control, not India's claim.
DISPUTED_STATES = frozenset({"Jammu and Kashmir", "Ladakh"})
#: About 500 m: states from 10m source keep their coastline character.
STATE_TOLERANCE = 0.005
DISTRICT_TOLERANCE = 0.004
DECIMALS = 3


def read_dbf(path: Path, fields: Sequence[str]) -> list[dict[str, str]]:
    """The named text fields of every record of a dBASE III file."""
    data = path.read_bytes()
    count = struct.unpack("<I", data[4:8])[0]
    header_len, record_len = struct.unpack("<HH", data[8:12])
    layout: list[tuple[str, int]] = []
    position = 32
    while data[position] != 0x0D:
        name = data[position : position + 11].split(b"\0")[0].decode("ascii")
        layout.append((name, data[position + 16]))
        position += 32
    wanted = set(fields)
    rows: list[dict[str, str]] = []
    for index in range(count):
        record = data[header_len + index * record_len : header_len + (index + 1) * record_len]
        offset = 1  # deletion flag
        row: dict[str, str] = {}
        for name, length in layout:
            if name in wanted:
                raw = record[offset : offset + length]
                row[name] = raw.decode("utf-8", "replace").replace("\0", "").strip()
            offset += length
        rows.append(row)
    return rows


def read_shp_polygons(path: Path) -> list[list[Ring]]:
    """Each record's rings (as lon/lat lists), in file order; non-polygons are empty."""
    data = path.read_bytes()
    position = 100
    records: list[list[Ring]] = []
    while position + 8 <= len(data):
        _number, words = struct.unpack(">ii", data[position : position + 8])
        content = data[position + 8 : position + 8 + words * 2]
        position += 8 + words * 2
        shape_type = struct.unpack("<i", content[:4])[0]
        if shape_type not in (5, 15, 25):
            records.append([])
            continue
        parts_count, points_count = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{parts_count}i", content[44 : 44 + 4 * parts_count]))
        start = 44 + 4 * parts_count
        coords = struct.unpack(f"<{2 * points_count}d", content[start : start + 16 * points_count])
        points = [(coords[2 * i], coords[2 * i + 1]) for i in range(points_count)]
        bounds = [*parts, points_count]
        records.append([points[bounds[i] : bounds[i + 1]] for i in range(parts_count)])
    return records


def _signed_area(ring: Sequence[Point]) -> float:
    return 0.5 * sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False))


def _in_ring(point: Point, ring: Sequence[Point]) -> bool:
    x, y = point
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def rings_to_polygons(rings: Sequence[Ring]) -> list[Polygon]:
    """Shapefile rings to polygons: clockwise rings are outers, the rest holes."""
    outers = [ring for ring in rings if _signed_area(ring) < 0]
    holes = [ring for ring in rings if _signed_area(ring) >= 0]
    polygons: list[Polygon] = [[ring] for ring in outers]
    for hole in holes:
        for polygon in polygons:
            if _in_ring(hole[0], polygon[0]):
                polygon.append(hole)
                break
    return polygons


def _distance(point: Point, start: Point, end: Point) -> float:
    (x, y), (x0, y0), (x1, y1) = point, start, end
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(x - x0, y - y0)
    t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))


def _douglas_peucker(points: Sequence[Point], tolerance: float) -> list[Point]:
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, -1
        for i in range(first + 1, last):
            d = _distance(points[i], points[first], points[last])
            if d > best:
                best, index = d, i
        if index != -1 and best > tolerance:
            keep[index] = True
            stack.extend([(first, index), (index, last)])
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify_ring(ring: Sequence[Point], *, tolerance: float) -> Ring:
    """Douglas-Peucker on a closed ring; never fewer than a closed triangle."""
    if len(ring) <= 4:
        return list(ring)
    # Split at the point farthest from the start so a closed ring keeps its shape.
    far = max(
        range(len(ring)), key=lambda i: math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1])
    )
    simplified = _douglas_peucker(ring[: far + 1], tolerance)[:-1] + _douglas_peucker(
        ring[far:], tolerance
    )
    if len(simplified) < 4:
        step = max(1, (len(ring) - 1) // 3)
        simplified = [ring[0], ring[step], ring[2 * step], ring[0]]
    return simplified


def _round(polygons: Sequence[Polygon], tolerance: float) -> list[list[list[list[float]]]]:
    out = []
    for polygon in polygons:
        rings = []
        for ring in polygon:
            simple = simplify_ring(ring, tolerance=tolerance)
            rounded = [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in simple]
            deduped = [p for i, p in enumerate(rounded) if i == 0 or p != rounded[i - 1]]
            if len(deduped) >= 4:
                rings.append(deduped)
        if rings:
            out.append(rings)
    return out


def _centroid(ring: Sequence[Point]) -> Point:
    area = _signed_area(ring)
    if abs(area) < 1e-12:
        xs, ys = zip(*ring, strict=True)
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return cx / (6 * area), cy / (6 * area)


def assign_state(point: Point, states: dict[str, list[Polygon]]) -> str | None:
    """The state whose polygons contain ``point``, else ``None``."""
    for name, polygons in states.items():
        for polygon in polygons:
            if _in_ring(point, polygon[0]) and not any(_in_ring(point, h) for h in polygon[1:]):
                return name
    return None


def _nearest_state(point: Point, states: dict[str, list[Polygon]]) -> str:
    def gap(name: str) -> float:
        return min(
            math.hypot(point[0] - x, point[1] - y)
            for polygon in states[name]
            for x, y in polygon[0]
        )

    return min(states, key=gap)


def _feature(name: str, kind: str, polygons: list[Any], **props: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"name": name, "kind": kind, **props},
        "geometry": {"type": "MultiPolygon", "coordinates": polygons},
    }


def _polygons(geometry: dict[str, Any]) -> list[Polygon]:
    """A GeoJSON Polygon or MultiPolygon as the build's polygon lists."""
    raw = geometry["coordinates"]
    polygons = [raw] if geometry["type"] == "Polygon" else raw
    return [[[(x, y) for x, y in ring] for ring in polygon] for polygon in polygons]


def clean_district_name(name: str) -> str:
    """An OSM district name without a trailing "District" or extra spaces."""
    cleaned = " ".join(str(name).split())
    if cleaned.casefold().endswith(" district"):
        cleaned = cleaned[: -len(" district")].rstrip()
    return cleaned


def override_features(state: str, source: Path) -> list[dict[str, Any]]:
    """One override file's features in the same shape as the bundled districts."""
    document = json.loads(source.read_text(encoding="utf-8"))
    features: list[dict[str, Any]] = []
    for feature in document["features"]:
        properties = feature.get("properties") or {}
        name = clean_district_name(properties.get("name") or properties.get("query") or "")
        if not name:
            continue
        extra: dict[str, Any] = {}
        name_te = str(properties.get("name_te") or "").strip()
        if name_te:
            extra["name_te"] = name_te
        features.append(
            _feature(
                name,
                "district",
                _round(_polygons(feature["geometry"]), DISTRICT_TOLERANCE),
                state=state,
                disputed=state in DISPUTED_STATES,
                **extra,
            )
        )
    return features


def apply_overrides(
    district_features: Sequence[dict[str, Any]],
    overrides: Sequence[tuple[str, Path]],
) -> list[dict[str, Any]]:
    """Drop each override state's districts and add the override's own."""
    features = list(district_features)
    for state, source in overrides:
        features = [
            feature
            for feature in features
            if (feature.get("properties") or {}).get("state") != state
        ]
        features.extend(override_features(state, source))
    features.sort(key=lambda f: (f["properties"]["state"], f["properties"]["name"]))
    return features


def patch_existing(
    path: Path, overrides: Sequence[tuple[str, Path]]
) -> dict[str, Any]:
    """Apply overrides to an already built ``india_districts.json`` in place."""
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise ValueError(f"{path} is not a FeatureCollection")
    patched = {**document, "features": apply_overrides(features, overrides)}
    path.write_text(json.dumps(patched, ensure_ascii=False, separators=(",", ":")), "utf-8")
    return patched


def build(
    states_shp: Path,
    districts_geojson: Path,
    overrides: Sequence[tuple[str, Path]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two FeatureCollections: India's states and its districts."""
    rows = read_dbf(states_shp.with_suffix(".dbf"), ["adm0_a3", "name"])
    shapes = read_shp_polygons(states_shp)
    states: dict[str, list[Polygon]] = {}
    for row, rings in zip(rows, shapes, strict=True):
        if row.get("adm0_a3") == "IND" and rings:
            states[row["name"]] = rings_to_polygons(rings)
    state_features = [
        _feature(name, "state", _round(polygons, STATE_TOLERANCE), disputed=name in DISPUTED_STATES)
        for name, polygons in sorted(states.items())
    ]

    document = json.loads(districts_geojson.read_text(encoding="utf-8"))
    district_features = []
    for feature in document["features"]:
        district = _polygons(feature["geometry"])
        largest = max((polygon[0] for polygon in district), key=lambda r: abs(_signed_area(r)))
        centre = _centroid(largest)
        state = assign_state(centre, states) or _nearest_state(centre, states)
        name = str(feature["properties"].get("shapeName") or "").strip()
        district_features.append(
            _feature(
                name,
                "district",
                _round(district, DISTRICT_TOLERANCE),
                state=state,
                disputed=state in DISPUTED_STATES,
            )
        )
    district_features = apply_overrides(district_features, overrides)
    return (
        {"type": "FeatureCollection", "features": state_features},
        {"type": "FeatureCollection", "features": district_features},
    )


def _parse_override(value: str) -> tuple[str, Path]:
    state, separator, source = value.partition("=")
    state, source = state.strip(), source.strip()
    if not separator or not state or not source:
        raise argparse.ArgumentTypeError('an override is "State=<geojson>"')
    return state, Path(source)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--states", type=Path)
    parser.add_argument("--districts", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--override",
        action="append",
        type=_parse_override,
        default=[],
        metavar="STATE=GEOJSON",
        help="replace one state's districts with a GeoJSON file (repeatable)",
    )
    parser.add_argument(
        "--patch-existing",
        action="store_true",
        help="apply the overrides to the already built india_districts.json in --out",
    )
    args = parser.parse_args(argv)
    if args.patch_existing:
        if not args.override:
            parser.error("--patch-existing needs at least one --override")
        path = args.out / "india_districts.json"
        document = patch_existing(path, args.override)
        print(f"{path}: {len(document['features'])} features, {path.stat().st_size / 1e6:.2f} MB")
        return 0
    if args.states is None or args.districts is None:
        parser.error("--states and --districts are required unless --patch-existing is used")
    states, districts = build(args.states, args.districts, args.override)
    for name, document in (("india_states.json", states), ("india_districts.json", districts)):
        path = args.out / name
        path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), "utf-8")
        print(f"{path}: {len(document['features'])} features, {path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
