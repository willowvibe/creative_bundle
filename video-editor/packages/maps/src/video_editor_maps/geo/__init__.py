"""Offline country geometry for map scenes.

The data is Natural Earth admin-0 (public domain), compacted to
``name``/``iso``/``adm0``/``region`` plus coordinates rounded to three
decimals (about 110 m at the equator). Two bundled scales ship inside the
package so a map scene renders with no network call and the same output on
every host: the default 1:110m set and a 1:50m set for close zooms.

Coordinates are ``(longitude, latitude)`` in degrees, matching GeoJSON. A
*ring* is one closed list of positions; a *polygon* is an outer ring plus any
holes; a country is one or more polygons. Everything here is a pure function
of the bundled file, so the renderer can be asserted on with plain strings.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA_PATH = Path(__file__).with_name("world_110m.json")

#: Bundled Natural Earth river centrelines (1:50m), worldwide.
RIVERS_PATH = Path(__file__).with_name("rivers_50m.json")

#: Bundled Natural Earth named geography regions (1:50m), worldwide.
REGIONS_PATH = Path(__file__).with_name("regions_50m.json")

#: Bundled Natural Earth scales. ``110m`` is the default; ``50m`` ships for
#: scenes that zoom far enough for the coarse outline to show.
_DATA_PATHS = {
    "110m": DATA_PATH,
    "50m": Path(__file__).with_name("world_50m.json"),
}

#: Point-of-view border sets (#343): Natural Earth's per-country editions draw
#: disputed borders as that country claims them. Each file holds only the
#: countries whose outline changes (the subject and its neighbours, from the
#: 1:10m edition, simplified); every other country stays the regular feature.
_WORLDVIEW_PATHS = {
    "ind": Path(__file__).with_name("worldview_ind.json"),
}

#: Bundled Natural Earth populated places (1:50m) for the gazetteer (#342).
PLACES_PATH = Path(__file__).with_name("places_50m.json")
#: India's states and districts (see INDIA_ADMIN_SOURCES.md for sources and
#: licences; districts are ODbL 1.0 and need a credit line where shown).
ADMIN_PATHS = {
    "state": Path(__file__).with_name("india_states.json"),
    "district": Path(__file__).with_name("india_districts.json"),
}

Position = tuple[float, float]
Ring = tuple[Position, ...]
Polygon = tuple[Ring, ...]
Bounds = tuple[float, float, float, float]
Frame = tuple[float, float, float, float]

#: A ring wider than this spans the antimeridian in the source data and would
#: be drawn straight across the frame. 110m splits the big cases already, so
#: the guard only drops the leftovers.
_MAX_RING_SPAN_DEGREES = 300.0


class GeoError(ValueError):
    """Geometry that cannot be loaded, found or projected."""


@dataclass(frozen=True)
class Country:
    """One admin-0 unit: its names and its projected-ready geometry."""

    name: str
    iso: str
    adm0: str
    region: str
    polygons: tuple[Polygon, ...]

    def matches(self, query: str) -> bool:
        """True when ``query`` names this country by name or ISO code."""
        wanted = query.strip().casefold()
        return wanted in {
            self.name.casefold(),
            self.iso.casefold(),
            self.adm0.casefold(),
        }

    def bounds(self) -> Bounds:
        """``(west, south, east, north)`` in degrees."""
        lons: list[float] = []
        lats: list[float] = []
        for polygon in self.polygons:
            for ring in polygon:
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)
        if not lons:
            raise GeoError(f"country {self.name!r} has no geometry")
        return min(lons), min(lats), max(lons), max(lats)


@dataclass(frozen=True)
class River:
    """One natural-earth river feature: a name and its centreline segments."""

    name: str
    scalerank: int
    segments: tuple[tuple[Position, ...], ...]


@dataclass(frozen=True)
class Region:
    """One named geography region: its kind and every polygon it covers."""

    name: str
    kind: str
    polygons: tuple[Polygon, ...]


#: Names the editorial vocabulary uses for rivers whose data name differs.
_RIVER_ALIASES: dict[str, str] = {
    "amazon": "Amazonas",
    "ganga": "Ganges",
    "mahanadi": "Mahäna Nadï",
    "yellow": "Huang He",
}


def _ring(raw: Sequence[Sequence[float]]) -> Ring:
    points: list[Position] = []
    for position in raw:
        lon = float(position[0])
        lat = float(position[1])
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        points.append((lon, lat))
    if len(points) < 3:
        return ()
    lons = [lon for lon, _ in points]
    if max(lons) - min(lons) > _MAX_RING_SPAN_DEGREES:
        return ()
    return tuple(points)


def _polygon(raw: Sequence[Sequence[Sequence[float]]]) -> Polygon:
    rings = tuple(ring for ring in (_ring(r) for r in raw) if ring)
    return rings


def _country(feature: dict[str, object]) -> Country | None:
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    name = str(properties.get("name") or "").strip()
    if not name:
        return None
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    polygons: list[Polygon] = []
    if kind == "Polygon" and isinstance(coordinates, list):
        polygons.append(_polygon(coordinates))
    elif kind == "MultiPolygon" and isinstance(coordinates, list):
        polygons.extend(_polygon(poly) for poly in coordinates)
    polygons = [polygon for polygon in polygons if polygon]
    if not polygons:
        return None
    return Country(
        name=name,
        iso=str(properties.get("iso") or ""),
        adm0=str(properties.get("adm0") or ""),
        region=str(properties.get("region") or ""),
        polygons=tuple(polygons),
    )


@cache
def countries(detail: str = "110m", worldview: str = "") -> tuple[Country, ...]:
    """Every bundled country at ``detail`` (``110m`` default, or ``50m``).

    ``worldview`` (``"ind"``) swaps in that point-of-view edition's outlines
    for the countries it covers, in the same order as the default set.
    """
    if worldview:
        try:
            pov_path = _WORLDVIEW_PATHS[worldview]
        except KeyError:
            known = ", ".join(sorted(_WORLDVIEW_PATHS))
            raise GeoError(f"unknown worldview {worldview!r}; choose one of: {known}") from None
        replaced = {country.adm0: country for country in _load_countries(pov_path)}
        return tuple(replaced.get(country.adm0, country) for country in countries(detail))
    try:
        path = _DATA_PATHS[detail]
    except KeyError:
        raise GeoError(f"unknown map detail {detail!r}; choose 110m or 50m") from None
    return _load_countries(path)


@cache
def _load_countries(path: Path) -> tuple[Country, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeoError(f"map geometry is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GeoError(f"map geometry is not valid JSON: {path}") from exc
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise GeoError(f"map geometry has no features: {path}")
    loaded = (
        country
        for country in (_country(feature) for feature in features if isinstance(feature, dict))
        if country
    )
    return tuple(loaded)


def find_country(query: str, detail: str = "110m", worldview: str = "") -> Country:
    """Resolve a country by name or ISO code, or raise :class:`GeoError`."""
    wanted = query.strip()
    if not wanted:
        raise GeoError("a map scene needs a country")
    for country in countries(detail, worldview):
        if country.matches(wanted):
            return country
    known = ", ".join(
        sorted({country.name for country in countries(detail) if country.region != "Antarctica"})
    )
    raise GeoError(f"unknown country {wanted!r}; known countries: {known}")


def _river(feature: dict[str, object]) -> River | None:
    """One river feature, or ``None`` when it has no usable centreline."""
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    name = str(properties.get("name") or "").strip()
    if not name or geometry.get("type") != "MultiLineString":
        return None
    raw = geometry.get("coordinates")
    if not isinstance(raw, list):
        return None
    segments: list[tuple[Position, ...]] = []
    for line in raw:
        if not isinstance(line, list):
            continue
        points: list[Position] = []
        for position in line:
            try:
                lon = float(position[0])
                lat = float(position[1])
            except (IndexError, TypeError, ValueError):
                continue
            if math.isfinite(lon) and math.isfinite(lat):
                points.append((lon, lat))
        if len(points) >= 2:
            segments.append(tuple(points))
    if not segments:
        return None
    try:
        scalerank = int(properties.get("scalerank") or 0)
    except (TypeError, ValueError):
        scalerank = 0
    return River(name=name, scalerank=scalerank, segments=tuple(segments))


@cache
def rivers() -> tuple[River, ...]:
    """Every bundled river feature, worldwide (Natural Earth 1:50m)."""
    try:
        document = json.loads(RIVERS_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeoError(f"river geometry is missing: {RIVERS_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise GeoError(f"river geometry is not valid JSON: {RIVERS_PATH}") from exc
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise GeoError(f"river geometry has no features: {RIVERS_PATH}")
    loaded = (
        river
        for river in (_river(feature) for feature in features if isinstance(feature, dict))
        if river
    )
    return tuple(loaded)


def _plain(name: str) -> str:
    """Casefolded, without diacritics or spaces: ``Mahäna Nadï`` -> ``mahananadi``."""
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(
        ch for ch in decomposed.casefold() if ch.isalnum() and not unicodedata.combining(ch)
    )


def find_river(query: str) -> tuple[River, ...]:
    """Every river feature named ``query`` (case-insensitive), or raise.

    A few editorial names differ from the Natural Earth name; the aliases map
    ``Amazon`` to ``Amazonas``, ``Ganga`` to ``Ganges`` and ``Yellow`` to
    ``Huang He`` when that feature is present. A river may be split into
    several features, so the lookup returns all of them.
    """
    wanted = query.strip()
    if not wanted:
        raise GeoError("a map scene needs a river")
    key = wanted.casefold()
    candidates: list[str] = []
    alias = _RIVER_ALIASES.get(key)
    if alias is not None:
        candidates.append(alias)
    candidates.append(wanted)
    for candidate in candidates:
        # Accents are ignored: the data spells "Godävari" and "Mahäna Nadï".
        wanted_key = _plain(candidate)
        found = tuple(river for river in rivers() if _plain(river.name) == wanted_key)
        if found:
            return found
    known = ", ".join(sorted({river.name for river in rivers()}))
    raise GeoError(f"unknown river {wanted!r}; known rivers: {known}")


def _region(feature: dict[str, object]) -> Region | None:
    """One named region feature, or ``None`` when it has no usable polygon."""
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    name = str(properties.get("name") or "").strip()
    kind = str(properties.get("kind") or "").strip()
    if not name or not kind:
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return None
    polygons: list[Polygon] = []
    if geometry.get("type") == "Polygon":
        polygons.append(_polygon(coordinates))
    elif geometry.get("type") == "MultiPolygon":
        polygons.extend(_polygon(polygon) for polygon in coordinates)
    polygons = [polygon for polygon in polygons if polygon]
    if not polygons:
        return None
    return Region(name=name, kind=kind, polygons=tuple(polygons))


@cache
def regions() -> tuple[Region, ...]:
    """Every bundled named geography region, worldwide (Natural Earth 1:50m)."""
    try:
        document = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeoError(f"region geometry is missing: {REGIONS_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise GeoError(f"region geometry is not valid JSON: {REGIONS_PATH}") from exc
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise GeoError(f"region geometry has no features: {REGIONS_PATH}")
    loaded = (
        region
        for region in (_region(feature) for feature in features if isinstance(feature, dict))
        if region
    )
    return tuple(loaded)


@cache
def find_region(query: str) -> Region:
    """The region named ``query`` (case-insensitive), all features merged.

    A name can appear on more than one feature (the Western Ghats is two
    polygons), so every matching feature's polygons are returned together.
    """
    wanted = query.strip()
    if not wanted:
        raise GeoError("a map scene needs a region")
    matches = [region for region in regions() if region.name.casefold() == wanted.casefold()]
    if not matches:
        known = ", ".join(sorted({region.name for region in regions()}))
        raise GeoError(f"unknown region {wanted!r}; known regions: {known}")
    first = matches[0]
    return Region(
        name=first.name,
        kind=first.kind,
        polygons=tuple(polygon for region in matches for polygon in region.polygons),
    )


def bounds_of(units: Iterable[Country]) -> Bounds:
    """The union of ``units``' bounds."""
    boxes = [unit.bounds() for unit in units]
    if not boxes:
        raise GeoError("cannot take the bounds of no countries")
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _projected_y(lat: float, projection: str) -> float:
    """The y coordinate of ``lat`` in the chosen projection."""
    clamped = max(-85.0, min(85.0, lat))
    if projection == "mercator":
        return math.log(math.tan(math.pi / 4.0 + math.radians(clamped) / 2.0))
    if projection == "equirectangular":
        return math.radians(clamped)
    raise GeoError(f"unknown projection {projection!r}; choose mercator or equirectangular")


def _projected_box(bounds: Bounds, projection: str) -> tuple[float, float, float, float]:
    """The window in projection units.

    Longitude is converted to radians here because ``_projected_y`` returns
    radians: mixing degrees on x with radians on y makes the fitted scale
    collapse whichever axis disagrees, which draws the map as a flat line.
    """
    west, south, east, north = bounds
    top = _projected_y(north, projection)
    bottom = _projected_y(south, projection)
    return math.radians(west), -top, math.radians(east), -bottom


@dataclass(frozen=True)
class View:
    """A frame plus the geographic window fitted into it."""

    frame: Frame
    bounds: Bounds
    projection: str = "mercator"

    def __post_init__(self) -> None:
        x, y, width, height = self.frame
        if not (
            math.isfinite(x) and math.isfinite(y) and math.isfinite(width) and math.isfinite(height)
        ):
            raise GeoError(f"frame must be finite, got {self.frame!r}")
        if width <= 0 or height <= 0:
            raise GeoError(f"frame must have a positive size, got {self.frame!r}")
        west, south, east, north = self.bounds
        if east <= west or north <= south:
            raise GeoError(f"bounds must be (west, south, east, north), got {self.bounds!r}")
        _projected_box(self.bounds, self.projection)

    def project(self, lon: float, lat: float) -> Position:
        """Canvas coordinates for a geographic position inside ``bounds``."""
        left, top, right, bottom = _projected_box(self.bounds, self.projection)
        x, y, width, height = self.frame
        span_x = right - left
        span_y = bottom - top
        scale = min(width / span_x, height / span_y)
        offset_x = x + (width - span_x * scale) / 2.0
        offset_y = y + (height - span_y * scale) / 2.0
        projected_x = math.radians(lon)
        projected_y = -_projected_y(lat, self.projection)
        return offset_x + (projected_x - left) * scale, offset_y + (projected_y - top) * scale

    def contains(self, lon: float, lat: float, *, margin: float = 0.0) -> bool:
        """True when a position falls inside the window, padded by ``margin`` degrees."""
        west, south, east, north = self.bounds
        return west - margin <= lon <= east + margin and south - margin <= lat <= north + margin


def _polygon_paths(
    polygons: Sequence[Polygon],
    view: View,
    *,
    precision: int = 2,
    bounds: Bounds | None = None,
) -> list[str]:
    """One SVG path per polygon, projected into ``view``.

    ``bounds`` is an optional, larger culling window in the same geographic
    coordinates as ``view``; projection always uses ``view`` so every layer
    shares one scale and offset.
    """
    west, south, east, north = bounds or view.bounds
    paths: list[str] = []
    for polygon in polygons:
        rings: list[str] = []
        for ring in polygon:
            # Drop a ring that shares no point with the window: it cannot draw
            # anything inside the clipped frame and only bloats the markup.
            if not any(
                west - 1.0 <= lon <= east + 1.0 and south - 1.0 <= lat <= north + 1.0
                for lon, lat in ring
            ):
                continue
            start = view.project(*ring[0])
            parts = [f"M{start[0]:.{precision}f} {start[1]:.{precision}f}"]
            parts.extend(
                f"L{point[0]:.{precision}f} {point[1]:.{precision}f}"
                for point in (view.project(lon, lat) for lon, lat in ring[1:])
            )
            parts.append("Z")
            rings.append("".join(parts))
        if rings:
            # Even-odd so a hole inside an outer ring stays a hole.
            paths.append("".join(rings))
    return paths


def country_paths(
    unit: Country, view: View, *, precision: int = 2, bounds: Bounds | None = None
) -> list[str]:
    """One SVG path per polygon of ``unit``, projected into ``view``."""
    return _polygon_paths(unit.polygons, view, precision=precision, bounds=bounds)


def region_paths(
    region: Region, view: View, *, precision: int = 2, bounds: Bounds | None = None
) -> list[str]:
    """One SVG path per polygon of ``region``, projected into ``view``."""
    return _polygon_paths(region.polygons, view, precision=precision, bounds=bounds)


def graticule_lines(
    view: View,
    *,
    step: float = 10.0,
    precision: int = 2,
    bounds: Bounds | None = None,
) -> tuple[list[str], list[str]]:
    """Meridians and parallels inside the window, as SVG path strings.

    The steps are snapped to the window's own edges so a line never floats a
    few degrees outside the frame, and the count is capped so a world-wide
    window at a small step cannot emit thousands of segments. ``bounds`` can
    extend the window without changing the projection, so a full-frame clip
    still gets graticule lines beyond the fitted view.
    """
    if step <= 0 or not math.isfinite(step):
        raise GeoError(f"graticule step must be positive, got {step!r}")
    west, south, east, north = bounds or view.bounds
    meridians: list[str] = []
    parallels: list[str] = []
    longitude = math.ceil(west / step) * step
    while longitude <= east and len(meridians) < 72:
        top = view.project(longitude, north)
        bottom = view.project(longitude, south)
        meridians.append(
            f"M{top[0]:.{precision}f} {top[1]:.{precision}f}"
            f"L{bottom[0]:.{precision}f} {bottom[1]:.{precision}f}"
        )
        longitude += step
    latitude = math.ceil(south / step) * step
    while latitude <= north and len(parallels) < 72:
        left = view.project(west, latitude)
        right = view.project(east, latitude)
        parallels.append(
            f"M{left[0]:.{precision}f} {left[1]:.{precision}f}"
            f"L{right[0]:.{precision}f} {right[1]:.{precision}f}"
        )
        latitude += step
    return meridians, parallels


def view_for(
    unit: Country,
    frame: Frame,
    *,
    padding: float = 0.12,
    bounds: Bounds | None = None,
    projection: str = "mercator",
) -> View:
    """A window fitted to ``bounds`` (or ``unit``'s own) plus padding."""
    window = bounds if bounds is not None else unit.bounds()
    west, south, east, north = window
    pad_x = max((east - west) * padding, 1.0)
    pad_y = max((north - south) * padding, 1.0)
    return View(
        frame=frame,
        bounds=(west - pad_x, max(-85.0, south - pad_y), east + pad_x, min(85.0, north + pad_y)),
        projection=projection,
    )


def parse_bounds(value: object) -> Bounds | None:
    """``"west,south,east,north"`` (or a four-item list) to a bounds tuple."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence):
        parts = [str(part).strip() for part in value]
    else:
        raise GeoError(f"bbox must be 'west,south,east,north', got {value!r}")
    if len(parts) != 4:
        raise GeoError(f"bbox needs four numbers, got {value!r}")
    try:
        numbers = tuple(float(part) for part in parts)
    except ValueError:
        raise GeoError(f"bbox needs four numbers, got {value!r}") from None
    if not all(math.isfinite(number) for number in numbers):
        raise GeoError(f"bbox needs finite numbers, got {value!r}")
    west, south, east, north = numbers
    if east <= west or north <= south:
        raise GeoError(f"bbox must be west,south,east,north, got {value!r}")
    return west, south, east, north


def parse_position(value: object) -> Position | None:
    """``"lon,lat"`` (or a two-item list) to a position tuple."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, Sequence):
        parts = [str(part).strip() for part in value]
    else:
        raise GeoError(f"focus must be 'lon,lat', got {value!r}")
    if len(parts) != 2:
        raise GeoError(f"focus needs two numbers, got {value!r}")
    try:
        lon, lat = (float(part) for part in parts)
    except ValueError:
        raise GeoError(f"focus needs two numbers, got {value!r}") from None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise GeoError(f"focus needs finite numbers, got {value!r}")
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise GeoError(f"focus must be a lon/lat pair, got {value!r}")
    return lon, lat


# --- point in polygon, land snapping (#342) -----------------------------------


def _in_ring(lon: float, lat: float, ring: Sequence[Position]) -> bool:
    """Ray-cast point-in-ring test in lon/lat space."""
    inside = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < crossing:
                inside = not inside
    return inside


def point_in_country(country: Country, lon: float, lat: float) -> bool:
    """True when ``(lon, lat)`` falls inside one of ``country``'s polygons."""
    for polygon in country.polygons:
        if (
            polygon
            and _in_ring(lon, lat, polygon[0])
            and not any(_in_ring(lon, lat, hole) for hole in polygon[1:])
        ):
            return True
    return False


def on_land(lon: float, lat: float, *, detail: str = "50m", worldview: str = "") -> bool:
    """True when a point is inside any bundled country (not the sea)."""
    if not -180.0 <= lon <= 180.0:
        lon = ((lon + 180.0) % 360.0) - 180.0
    for country in countries(detail, worldview):
        west, south, east, north = country.bounds()
        if west <= lon <= east and south <= lat <= north and point_in_country(country, lon, lat):
            return True
    return False


_KM_PER_DEGREE = 111.32


def _nearest_on_segment(
    point: Position, start: Position, end: Position, cos_lat: float
) -> tuple[float, Position]:
    """(distance in km, nearest point) from ``point`` to one ring edge."""
    px, py = point[0] * cos_lat, point[1]
    ax, ay = start[0] * cos_lat, start[1]
    bx, by = end[0] * cos_lat, end[1]
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    nx, ny = ax + t * dx, ay + t * dy
    distance = math.hypot(px - nx, py - ny) * _KM_PER_DEGREE
    return distance, (nx / cos_lat, ny)


def snap_to_land(
    lon: float,
    lat: float,
    *,
    max_km: float = 15.0,
    detail: str = "50m",
    worldview: str = "",
) -> Position | None:
    """Move a point just offshore onto the nearest coast, or ``None`` if too far.

    A marker typed a few kilometres out ("Chennai 80.3, 13.1") lands in the sea
    at map scale. Points already on land come back unchanged; points farther
    than ``max_km`` from any coast return ``None`` so the caller can decide
    (a sea label is legitimate, a city in the sea is not).
    """
    if on_land(lon, lat, detail=detail, worldview=worldview):
        return (lon, lat)
    cos_lat = max(0.05, math.cos(math.radians(lat)))
    reach = max_km / _KM_PER_DEGREE
    best: tuple[float, Position] | None = None
    for country in countries(detail, worldview):
        west, south, east, north = country.bounds()
        if lon < west - reach / cos_lat or lon > east + reach / cos_lat:
            continue
        if lat < south - reach or lat > north + reach:
            continue
        for polygon in country.polygons:
            for ring in polygon:
                for index in range(len(ring) - 1):
                    found = _nearest_on_segment((lon, lat), ring[index], ring[index + 1], cos_lat)
                    if best is None or found[0] < best[0]:
                        best = found
    if best is None or best[0] > max_km:
        return None
    coast = best[1]
    step_x, step_y = coast[0] - lon, coast[1] - lat
    norm = math.hypot(step_x, step_y) or 1.0
    # Step a little past the coastline so the pin sits on land, not on the line.
    for inland in (0.01, 0.02, 0.04, 0.08):
        candidate = (coast[0] + step_x / norm * inland, coast[1] + step_y / norm * inland)
        if on_land(*candidate, detail=detail, worldview=worldview):
            return (round(candidate[0], 4), round(candidate[1], 4))
    return (round(coast[0], 4), round(coast[1], 4))


# --- gazetteer (#342) ---------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """One populated place from the bundled gazetteer."""

    name: str
    adm0: str
    lon: float
    lat: float
    population: int


@cache
def _places() -> tuple[tuple[Place, frozenset[str]], ...]:
    try:
        document = json.loads(PLACES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeoError(f"place gazetteer is unreadable: {PLACES_PATH}") from exc
    loaded: list[tuple[Place, frozenset[str]]] = []
    for name, ascii_name, alt, adm0, lon, lat, population in document["places"]:
        keys = {str(name).casefold(), str(ascii_name).casefold()}
        keys.update(part.strip().casefold() for part in str(alt).split("|") if part.strip())
        loaded.append(
            (Place(str(name), str(adm0), float(lon), float(lat), int(population)), frozenset(keys))
        )
    return tuple(loaded)


def find_place(query: str, *, country: str = "") -> Place:
    """The most populous bundled place named ``query`` (optionally in ``country``).

    ``country`` is an ADM0/ISO code or a country name. Raises :class:`GeoError`
    when nothing matches, so a script typo fails at planning time.
    """
    wanted = query.strip().casefold()
    code = ""
    if country:
        try:
            code = find_country(country).adm0
        except GeoError:
            code = country.strip().upper()
    for place, keys in _places():  # sorted by population, largest first
        if wanted in keys and (not code or place.adm0 == code):
            return place
    where = f" in {country}" if country else ""
    raise GeoError(f"unknown place {query!r}{where}; give lon/lat for places not in the gazetteer")


@dataclass(frozen=True)
class AdminUnit:
    """An Indian state or district, with its parent state and dispute flag."""

    region: Region
    state: str
    disputed: bool


#: Current or common names -> the district data's own name. The source predates
#: several renamings (Mysuru, Prayagraj) and misspells Hyderabad; Andhra Pradesh
#: now carries its 28 districts from OpenStreetMap, including the 2022 carve-outs.
DISTRICT_ALIASES: dict[str, str] = {
    "hyderabad": "Hydrabad",
    "bengaluru": "Bangalore",
    "bengaluruurban": "Bangalore",
    "bangaloreurban": "Bangalore",
    "bengalururural": "Bangalore Rural",
    "mysuru": "Mysore",
    "belagavi": "Belgaum",
    "kalaburagi": "Gulbarga",
    "ballari": "Bellary",
    "shivamogga": "Shimoga",
    "tumakuru": "Tumkur",
    "vijayapura": "Bijapur",
    "gurugram": "Gurgaon",
    "prayagraj": "Allahabad",
    "nasik": "Nashik",
    "pondicherry": "Puducherry",
    "kanyakumari": "Kanniyakumari",
    "trichy": "Tiruchirappalli",
    "tiruchy": "Tiruchirappalli",
    "kadapa": "YSR Kadapa",
    "kadapaysr": "YSR Kadapa",
    "ysrkadapa": "YSR Kadapa",
    "anantapur": "Anantapuramu",
    "ananthapur": "Anantapuramu",
    "ananthapuramu": "Anantapuramu",
    "nellore": "Sri Potti Sri Ramulu Nellore",
    "spsrnellore": "Sri Potti Sri Ramulu Nellore",
    "konaseema": "Dr. B.R. Ambedkar Konaseema",
    "trivandrum": "Thiruvananthapuram",
    "thoothukudi": "Thoothukkudi",
    "tuticorin": "Thoothukkudi",
}


def _admin_key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


@cache
def admin_units(kind: str) -> tuple[AdminUnit, ...]:
    """Every bundled unit of ``kind`` (``state`` or ``district``)."""
    path = ADMIN_PATHS.get(kind)
    if path is None:
        raise GeoError(f"unknown admin kind {kind!r}; use 'state' or 'district'")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GeoError(f"admin geometry is missing: {path}") from exc
    units: list[AdminUnit] = []
    for feature in document.get("features", []):
        region = _region(feature)
        if region is None:
            continue
        properties = feature.get("properties") or {}
        units.append(
            AdminUnit(
                region=region,
                state=str(properties.get("state") or region.name),
                disputed=bool(properties.get("disputed")),
            )
        )
    return tuple(units)


def find_admin(query: str, kind: str) -> Region:
    """The state or district named ``query``, ignoring case, spaces and punctuation.

    An unknown name suggests the closest spellings (district names vary:
    ``Thoothukkudi``). A unit in the disputed north is refused: the source draws
    it de facto, which would contradict the map plate's own outline.
    """
    wanted = _admin_key(query)
    if not wanted:
        raise GeoError(f"a map {kind} needs a name")
    if kind == "district" and wanted in DISTRICT_ALIASES:
        wanted = _admin_key(DISTRICT_ALIASES[wanted])
    units = admin_units(kind)
    matches = [unit for unit in units if _admin_key(unit.region.name) == wanted]
    if not matches:
        import difflib

        names = sorted({unit.region.name for unit in units})
        close = difflib.get_close_matches(query.strip(), names, n=3, cutoff=0.6)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        raise GeoError(f"unknown {kind} {query.strip()!r}{hint}")
    unit = matches[0]
    if unit.disputed:
        raise GeoError(
            f"{kind} {unit.region.name!r} is drawn de facto in the boundary source "
            "(it stops at the Line of Control), which would contradict the map's "
            "outline of India; it cannot be filled"
        )
    return unit.region
