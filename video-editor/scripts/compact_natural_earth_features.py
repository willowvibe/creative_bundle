"""Compact Natural Earth rivers and geography regions for map scenes.

    python -I scripts/compact_natural_earth_features.py \
        ne_50m_rivers_lake_centerlines.geojson ne_50m_geography_regions_polys.geojson \
        packages/maps/src/video_editor_maps/geo/

Writes ``rivers_50m.json`` (every named river centreline: ``name`` plus
LineString/MultiLineString coordinates) and ``regions_50m.json`` (named
mountain ranges, plateaus, plains, deserts, deltas and basins: ``name``,
``kind`` plus MultiPolygon coordinates). Coordinates are rounded to three
decimals, like ``world_50m.json``.

Source: https://github.com/nvkelso/natural-earth-vector (public domain).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: Natural Earth ``FEATURECLA`` values kept, mapped to the scene's region kind.
REGION_KINDS = {
    "Range/mtn": "range",
    "Plateau": "plateau",
    "Plain": "plain",
    "Desert": "desert",
    "Delta": "delta",
    "Basin": "basin",
    "Lowland": "plain",
    "Valley": "valley",
}


def _round(value: Any) -> Any:
    if isinstance(value, float | int) and not isinstance(value, bool):
        return round(float(value), 3)
    return [_round(item) for item in value]


def _dedupe(line: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for point in line:
        if not out or point != out[-1]:
            out.append(point)
    return out


def _title(name: str) -> str:
    """'WESTERN GHATS' -> 'Western Ghats'; keeps already mixed-case names."""
    if name.isupper():
        return " ".join(word.capitalize() for word in name.split())
    return name


def compact_rivers(source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        props = feature["properties"]
        name = (props.get("name_en") or props.get("name") or "").strip()
        geometry = feature.get("geometry") or {}
        if not name or geometry.get("type") not in {"LineString", "MultiLineString"}:
            continue
        lines = (
            [geometry["coordinates"]]
            if geometry["type"] == "LineString"
            else geometry["coordinates"]
        )
        lines = [_dedupe(_round(line)) for line in lines]
        lines = [line for line in lines if len(line) >= 2]
        if lines:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"name": name, "scalerank": props.get("scalerank")},
                    "geometry": {"type": "MultiLineString", "coordinates": lines},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def compact_regions(source: Path) -> dict[str, Any]:
    data = json.loads(source.read_text(encoding="utf-8"))
    features = []
    for feature in data["features"]:
        props = feature["properties"]
        kind = REGION_KINDS.get(str(props.get("FEATURECLA") or ""))
        name = _title(str(props.get("NAME_EN") or props.get("NAME") or "").strip())
        geometry = feature.get("geometry") or {}
        if not kind or not name or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
        rounded = [[_dedupe(_round(ring)) for ring in polygon] for polygon in polygons]
        rounded = [[ring for ring in polygon if len(ring) >= 4] for polygon in rounded]
        rounded = [polygon for polygon in rounded if polygon]
        if rounded:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"name": name, "kind": kind},
                    "geometry": {"type": "MultiPolygon", "coordinates": rounded},
                }
            )
    return {"type": "FeatureCollection", "features": features}


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    out_dir = Path(argv[3])
    rivers = compact_rivers(Path(argv[1]))
    regions = compact_regions(Path(argv[2]))
    (out_dir / "rivers_50m.json").write_text(json.dumps(rivers, separators=(",", ":")), "utf-8")
    (out_dir / "regions_50m.json").write_text(json.dumps(regions, separators=(",", ":")), "utf-8")
    print(f"{len(rivers['features'])} rivers, {len(regions['features'])} regions -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
