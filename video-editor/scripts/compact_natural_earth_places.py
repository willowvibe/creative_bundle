"""Compact Natural Earth populated places into the map gazetteer (#342).

    python -I scripts/compact_natural_earth_places.py ne_50m_populated_places_simple.geojson \
        packages/maps/src/video_editor_maps/geo/places_50m.json

Each row is ``[name, ascii_name, alt_names, adm0_a3, lon, lat, pop_max]`` with
coordinates rounded to three decimals. Source:
https://github.com/nvkelso/natural-earth-vector (public domain).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def compact(source: Path) -> dict[str, object]:
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = []
    for feature in data["features"]:
        props = feature["properties"]
        lon, lat = feature["geometry"]["coordinates"][:2]
        rows.append(
            [
                props["name"],
                props.get("nameascii") or props["name"],
                props.get("namealt") or "",
                props.get("adm0_a3") or "",
                round(float(lon), 3),
                round(float(lat), 3),
                int(props.get("pop_max") or 0),
            ]
        )
    rows.sort(key=lambda row: -row[6])
    return {"fields": ["name", "ascii", "alt", "adm0", "lon", "lat", "pop"], "places": rows}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    out = compact(Path(argv[1]))
    Path(argv[2]).write_text(
        json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    print(f"{len(out['places'])} places -> {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
