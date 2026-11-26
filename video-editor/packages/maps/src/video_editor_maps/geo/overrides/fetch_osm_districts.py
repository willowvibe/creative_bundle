"""Re-fetch one state's current district polygons from OpenStreetMap Nominatim.

This is a standalone, stdlib-only helper for refreshing an admin override such
as ``andhra_pradesh_districts_osm_2026-11-26.geojson``.  It was saved after an
ad-hoc fetch filled the Andhra Pradesh override: Overpass was unreachable
(HTTP 406) on 2026-11-26, so Nominatim is used instead.

Usage::

    python3 fetch_osm_districts.py \\
        --state "Andhra Pradesh" \\
        --out andhra_pradesh_districts_osm_YYYY-MM-DD.geojson

Nominatim's usage policy requires a descriptive User-Agent and at most one
request per second, so this sleeps between requests.  The output is a
GeoJSON FeatureCollection whose features have ``query``, ``name``,
``name_en``, ``name_te``, ``osm_id`` and ``wikidata`` properties, ready for
``build_india_admin --override "Andhra Pradesh=<file>"``.

Pass ``--check`` to look for overlapping districts; it needs shapely, which
is optional and never imported otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "map-build-script/1.0"
REQUEST_INTERVAL = 1.2

ANDHRA_PRADESH_DISTRICTS = (
    "Srikakulam",
    "Vizianagaram",
    "Parvathipuram Manyam",
    "Alluri Sitharama Raju",
    "Visakhapatnam",
    "Anakapalli",
    "Kakinada",
    "East Godavari",
    "Dr. B.R. Ambedkar Konaseema",
    "Eluru",
    "West Godavari",
    "NTR",
    "Krishna",
    "Palnadu",
    "Guntur",
    "Bapatla",
    "Prakasam",
    "Sri Potti Sriramulu Nellore",
    "Kurnool",
    "Nandyal",
    "Anantapuramu",
    "Sri Sathya Sai",
    "YSR Kadapa",
    "Annamayya",
    "Chittoor",
    "Tirupati",
    "Polavaram",
    "Markapuram",
)
DEFAULT_DISTRICTS = {"Andhra Pradesh": ANDHRA_PRADESH_DISTRICTS}


def fetch_district(name: str, state: str) -> tuple[dict[str, Any] | None, str | None]:
    """The first boundary/state_district hit for one district, or a miss reason."""
    query = f"{name} district, {state}, India"
    params = {
        "q": query,
        "format": "json",
        "polygon_geojson": 1,
        "limit": 3,
        "polygon_threshold": 0.001,
        "namedetails": 1,
        "extratags": 1,
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            results = json.load(response)
    except (OSError, ValueError) as error:
        return None, f"request failed: {error}"
    if not isinstance(results, list):
        return None, "unexpected response from Nominatim"
    for result in results:
        if (
            result.get("class") == "boundary"
            and result.get("addresstype") == "state_district"
            and result.get("geojson")
        ):
            return _feature(name, result), None
    return None, "no boundary/state_district result"


def _feature(query: str, result: dict[str, Any]) -> dict[str, Any]:
    details = result.get("namedetails") or {}
    extra = result.get("extratags") or {}
    return {
        "type": "Feature",
        "geometry": result["geojson"],
        "properties": {
            "query": query,
            "name": result.get("name") or result.get("display_name"),
            "name_en": details.get("name:en"),
            "name_te": details.get("name:te"),
            "osm_id": result.get("osm_id"),
            "wikidata": details.get("wikidata") or extra.get("wikidata"),
        },
    }


def check_geometry(features: Sequence[dict[str, Any]]) -> None:
    """Report pairwise overlaps over 1% and union area versus the area sum."""
    try:
        from shapely.geometry import shape  # type: ignore[import-untyped]
        from shapely.ops import unary_union  # type: ignore[import-untyped]
    except ImportError:
        print("--check: shapely is not installed; skipping geometry checks", file=sys.stderr)
        return
    shapes = [
        (
            str(feature["properties"].get("query") or feature["properties"].get("name")),
            shape(feature["geometry"]),
        )
        for feature in features
    ]
    print("overlap check (threshold 1% of the smaller district):")
    for index, (name_a, geometry_a) in enumerate(shapes):
        for name_b, geometry_b in shapes[index + 1 :]:
            if not geometry_a.intersects(geometry_b):
                continue
            overlap = geometry_a.intersection(geometry_b).area
            smaller = min(geometry_a.area, geometry_b.area)
            if smaller and overlap / smaller > 0.01:
                print(f"  {name_a} / {name_b}: {overlap / smaller:.1%} of the smaller")
    total = sum(geometry.area for _, geometry in shapes)
    union = unary_union([geometry for _, geometry in shapes]).area
    print(f"sum of district areas: {total:.6f} square degrees")
    print(f"union area:            {union:.6f} square degrees")
    if total:
        print(
            f"overlap (sum - union): {total - union:.6f} square degrees "
            f"({(total - union) / total:.2%} of the sum)"
        )


def _names_from(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    if args.names:
        return [name.strip() for name in args.names.split(",") if name.strip()]
    if args.names_file:
        lines = args.names_file.read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    try:
        return list(DEFAULT_DISTRICTS[args.state])
    except KeyError:
        parser.error(f"no built-in name list for {args.state!r}; use --names or --names-file")
        return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--state",
        default="Andhra Pradesh",
        help="state name used in the search query (default: Andhra Pradesh)",
    )
    names = parser.add_mutually_exclusive_group()
    names.add_argument("--names", help="comma-separated district names to fetch")
    names.add_argument("--names-file", type=Path, help="file with one district name per line")
    parser.add_argument("--out", type=Path, required=True, help="GeoJSON override file to write")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report pairwise overlaps and union area (requires shapely)",
    )
    args = parser.parse_args(argv)

    district_names = _names_from(args, parser)
    if not district_names:
        parser.error("no district names to fetch")

    features: list[dict[str, Any]] = []
    misses: list[tuple[str, str | None]] = []
    for index, name in enumerate(district_names):
        if index:
            time.sleep(REQUEST_INTERVAL)
        feature, error = fetch_district(name, args.state)
        if feature is None:
            misses.append((name, error))
            print(f"miss {name}: {error}", file=sys.stderr)
            continue
        features.append(feature)
        properties = feature["properties"]
        print(f"hit  {name} -> {properties['name']} (osm_id {properties['osm_id']})")

    document = {"type": "FeatureCollection", "features": features}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    print(f"{args.out}: {len(features)} of {len(district_names)} districts")

    if args.check:
        check_geometry(features)

    if misses:
        print(f"\n{len(misses)} district(s) not found:", file=sys.stderr)
        for name, error in misses:
            print(f"  {name}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
