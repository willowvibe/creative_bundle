"""Compact a Natural Earth admin-0 GeoJSON into the map scenes' bundled format.

Keeps ``name``/``iso``/``adm0``/``region`` (from ``NAME``/``ISO_A3``/``ADM0_A3``/
``REGION_UN``) and rounds coordinates to three decimals, the format of
``packages/maps/src/video_editor_maps/geo/world_110m.json``.

    python -I scripts/compact_natural_earth.py ne_50m_admin_0_countries.geojson \
        packages/maps/src/video_editor_maps/geo/world_50m.json

    # India's point of view (#343): only India and its neighbours, simplified
    python -I scripts/compact_natural_earth.py ne_10m_admin_0_countries_ind.geojson \
        packages/maps/src/video_editor_maps/geo/worldview_ind.json \
        --only IND,PAK,CHN,NPL,BTN,BGD,MMR,LKA,AFG --simplify 0.01

Source: https://github.com/nvkelso/natural-earth-vector (public domain).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _round(value: Any) -> Any:
    if isinstance(value, float | int) and not isinstance(value, bool):
        return round(float(value), 3)
    return [_round(item) for item in value]


def _dedupe(ring: list[list[float]]) -> list[list[float]]:
    """Drop consecutive points that rounding made identical."""
    out: list[list[float]] = []
    for point in ring:
        if not out or point != out[-1]:
            out.append(point)
    return out


def _segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = max(
        0.0,
        min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)),
    )
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


def _simplify(ring: list[list[float]], tolerance: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker in degrees, iterative; keeps the ring closed."""
    if tolerance <= 0 or len(ring) < 5:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            distance = _segment_distance(ring[i], ring[first], ring[last])
            if distance > worst:
                worst, index = distance, i
        if index >= 0 and worst > tolerance:
            keep[index] = True
            stack.extend([(first, index), (index, last)])
    return [point for point, kept in zip(ring, keep, strict=True) if kept]


def compact(
    source: Path, *, only: set[str] | None = None, tolerance: float = 0.0
) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        props = feature["properties"]
        if only is not None and props["ADM0_A3"] not in only:
            continue
        geometry = feature["geometry"]
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
        rounded = [
            [_dedupe(_round(_simplify(ring, tolerance))) for ring in polygon]
            for polygon in polygons
        ]
        rounded = [[ring for ring in polygon if len(ring) >= 4] for polygon in rounded]
        rounded = [polygon for polygon in rounded if polygon]
        if not rounded:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": props["NAME"],
                    "iso": props["ISO_A3"],
                    "adm0": props["ADM0_A3"],
                    "region": props["REGION_UN"],
                },
                "geometry": {"type": "MultiPolygon", "coordinates": rounded},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--only", default="", help="comma-separated ADM0_A3 codes to keep")
    parser.add_argument("--simplify", type=float, default=0.0, help="RDP tolerance in degrees")
    args = parser.parse_args(argv)
    only = {code.strip() for code in args.only.split(",") if code.strip()} or None
    out = compact(args.source, only=only, tolerance=args.simplify)
    args.output.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out['features'])} features -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
