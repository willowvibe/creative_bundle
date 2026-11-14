"""Journeys: multi-stop routes with a km counter on the line and a travelling icon.

A map scene may declare::

    journey:
      icon: car            # car | plane | dot | none
      km: true             # running total riding each leg
      curve: 0.15          # how far a leg bows (0 = straight)
      follow: false        # camera frames each leg as it draws
      stops:
        - {text: Coimbatore, lon: 76.96, lat: 11.02}
        - {text: Eluru, lon: 81.10, lat: 16.71, km: 1000, via: [[78.1, 11.6]]}

Every stop after the first is a leg. A leg draws when its destination's name is
spoken (the stop's ``cue``, default its text), the icon drives it, and the
counter rolls from the previous total to the new one. ``km`` on a stop is the
real road distance; without it the leg is measured as a great circle, rounded
to 10 km, which understates a road trip, so authors should give road km.

Stops become ordinary scene markers and ``follow`` becomes ordinary ``to:``
zooms, so labels, pins and the camera reuse the map scene's own machinery. The
leg paths, icon and counter are drawn here. HyperFrames seeks CSS animations on
HTML elements only, so every leg's draw, icon position and icon visibility are
custom properties animated on the HTML wrapper and read by the SVG via ``var``.
"""

from __future__ import annotations

import html
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from video_editor_core.cues import declared_cue
from video_editor_core.paper import safe_css_color
from video_editor_core.tally_chip import indian_format

ICONS = frozenset({"car", "plane", "dot", "none"})
#: Leg shapes a stop may ask for; "" is the bowed chord or ``via`` road.
SHAPES = frozenset({"", "heart"})
#: Heart width as a share of its leg's length, and its sampling.
HEART_SIZE = 0.38
HEART_SAMPLES = 48
DEFAULT_CURVE = 0.15
EARTH_RADIUS_KM = 6371.0
#: Samples along a curved leg; enough that the polyline reads as a curve.
CURVE_SAMPLES = 24
#: Catmull-Rom samples per segment of a ``via`` road.
ROAD_SAMPLES = 8
#: Padding around a followed leg (share of its span); the title floats over a
#: full-frame map, so a tight fit lands a stop under it.
FOLLOW_PAD = 0.4
#: The tightest window (degrees) a followed leg may frame: a short leg must not
#: zoom the camera in until borders become thick bands.
FOLLOW_MIN_SPAN = 5.0
#: Counter values a leg's total passes through.
KM_STEPS = 12

Point = tuple[float, float]


class JourneyError(ValueError):
    """A journey the scene cannot draw."""


@dataclass(frozen=True)
class Stop:
    text: str
    lon: float
    lat: float
    cue: str
    km: float | None = None
    via: tuple[Point, ...] = ()
    #: ``heart`` draws the leg through a heart; "" is the plain leg.
    shape: str = ""
    #: The leg's own line colour ("" uses the design's route colour).
    color: str = ""
    #: Trace this river's course from the previous stop ("" = no river).
    along: str = ""


@dataclass(frozen=True)
class Leg:
    origin: Stop
    destination: Stop
    km_from: int
    km_to: int

    @property
    def points(self) -> tuple[Point, ...]:
        return (
            (self.origin.lon, self.origin.lat),
            *self.destination.via,
            (self.destination.lon, self.destination.lat),
        )

    @property
    def cue(self) -> str:
        return self.destination.cue


@dataclass(frozen=True)
class Journey:
    stops: tuple[Stop, ...]
    legs: tuple[Leg, ...]
    icon: str = "car"
    show_km: bool = True
    curve: float = DEFAULT_CURVE
    follow: bool = False


@dataclass(frozen=True)
class ProjectedLeg:
    """A leg on the plate, with its reveal schedule."""

    path: str
    length: float
    delay: float
    draw: float
    km_from: int
    km_to: int
    label_at: Point
    #: When the next leg starts (the icon hands over); the last runs to the end.
    until: float = field(default=math.inf)
    color: str = ""


def _number(entry: Mapping[str, Any], key: str, what: str) -> float:
    try:
        value = float(entry[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise JourneyError(f"journey stop {what!r} needs a numeric `{key}`") from exc
    if not math.isfinite(value):
        raise JourneyError(f"journey stop {what!r} has a non-finite `{key}`")
    return value


def _via(raw: object, what: str) -> tuple[Point, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise JourneyError(f"journey stop {what!r}: `via` must be a list of [lon, lat]")
    points: list[Point] = []
    for item in raw:
        if not isinstance(item, Sequence) or isinstance(item, str) or len(item) != 2:
            raise JourneyError(f"journey stop {what!r}: each `via` point is [lon, lat]")
        points.append((float(item[0]), float(item[1])))
    return tuple(points)


def _stop(entry: object) -> Stop:
    if not isinstance(entry, Mapping):
        raise JourneyError(f"a journey stop must be a mapping, got {entry!r}")
    text = str(entry.get("text") or "").strip()
    if not text:
        raise JourneyError("every journey stop needs `text`")
    km = entry.get("km")
    if km is not None and (isinstance(km, bool) or not isinstance(km, int | float) or km <= 0):
        raise JourneyError(f"journey stop {text!r}: `km` must be a positive number")
    shape = str(entry.get("shape") or "").strip().lower()
    if shape not in SHAPES:
        raise JourneyError(f"journey stop {text!r}: shape must be one of {sorted(SHAPES - {''})}")
    return Stop(
        text=text,
        lon=_number(entry, "lon", text),
        lat=_number(entry, "lat", text),
        cue=declared_cue(entry.get("cue"), text),
        km=float(km) if km is not None else None,
        via=_via(entry.get("via"), text),
        shape=shape,
        color=_leg_color(entry.get("color"), text),
        along=str(entry.get("along") or "").strip(),
    )


def _leg_color(raw: object, text: str) -> str:
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        return safe_css_color(str(raw), f"journey stop {text!r} colour")
    except ValueError as exc:
        raise JourneyError(str(exc)) from exc


def great_circle_km(points: Sequence[Point]) -> float:
    """Haversine length of a lon/lat polyline."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:], strict=False):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = p2 - p1, math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        total += 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
    return total


def leg_km(leg: Leg) -> int:
    """The authored road km, else the great-circle length rounded to 10 km."""
    if leg.destination.km is not None:
        return round(leg.destination.km)
    return int(round(great_circle_km(leg.points) / 10.0) * 10)


def _nearest_index(course: Sequence[Point], point: Point) -> int:
    return min(
        range(len(course)),
        key=lambda i: (course[i][0] - point[0]) ** 2 + (course[i][1] - point[1]) ** 2,
    )


def _trace_river(origin: Stop, destination: Stop, course: Sequence[Point]) -> tuple[Point, ...]:
    """The river's own vertices between the points nearest each stop, in travel order."""
    start = _nearest_index(course, (origin.lon, origin.lat))
    end = _nearest_index(course, (destination.lon, destination.lat))
    forward = start <= end
    between = course[start + 1 : end] if forward else list(reversed(course[end + 1 : start]))
    return tuple(between)


def parse_journey(
    props: Mapping[str, Any],
    *,
    river_course: Callable[[str], Sequence[Point]] | None = None,
) -> Journey | None:
    """Read ``props["journey"]``, or ``None`` when the scene has none.

    ``river_course`` resolves an ``along`` name to its source-to-mouth course;
    a stop with ``along`` needs it. ``total_km`` scales the legs whose km was
    measured (not authored) so the journey adds up to the documented length.
    """
    raw = props.get("journey")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise JourneyError("map journey must be a mapping with `stops`")
    stops_raw = raw.get("stops")
    if not isinstance(stops_raw, Sequence) or isinstance(stops_raw, str) or len(stops_raw) < 2:
        raise JourneyError("a journey needs at least two stops")
    stops = [_stop(entry) for entry in stops_raw]
    for position in range(1, len(stops)):
        stop = stops[position]
        if not stop.along:
            continue
        if river_course is None:
            raise JourneyError(f"journey stop {stop.text!r}: `along` needs a river lookup")
        try:
            course = list(river_course(stop.along))
        except ValueError as exc:
            raise JourneyError(f"journey stop {stop.text!r} along {stop.along!r}: {exc}") from exc
        if len(course) < 2:
            raise JourneyError(f"journey stop {stop.text!r}: river {stop.along!r} has no course")
        via = _trace_river(stops[position - 1], stop, course)
        stops[position] = replace(stop, via=via)
    icon = str(raw.get("icon", "car")).strip().lower()
    if icon not in ICONS:
        raise JourneyError(f"journey icon must be one of {sorted(ICONS)}, got {icon!r}")
    try:
        curve = float(raw.get("curve", DEFAULT_CURVE))
    except (TypeError, ValueError) as exc:
        raise JourneyError("journey curve must be a number") from exc
    if not -1.0 <= curve <= 1.0:
        raise JourneyError(f"journey curve must be between -1 and 1, got {curve}")
    pairs = list(zip(stops, stops[1:], strict=False))
    lengths = [leg_km(Leg(origin=o, destination=d, km_from=0, km_to=0)) for o, d in pairs]
    target = raw.get("total_km")
    if target is not None:
        try:
            target_km = float(target)
        except (TypeError, ValueError) as exc:
            raise JourneyError("journey total_km must be a number") from exc
        authored = sum(n for n, (_o, d) in zip(lengths, pairs, strict=True) if d.km is not None)
        measured = sum(n for n, (_o, d) in zip(lengths, pairs, strict=True) if d.km is None)
        if measured > 0 and target_km > authored:
            factor = (target_km - authored) / measured
            lengths = [
                n if d.km is not None else round(n * factor)
                for n, (_o, d) in zip(lengths, pairs, strict=True)
            ]
            # Rounding must not miss the documented total.
            lengths[-1] += round(target_km) - sum(lengths)
    legs: list[Leg] = []
    total = 0
    for (origin, destination), length in zip(pairs, lengths, strict=True):
        legs.append(
            Leg(origin=origin, destination=destination, km_from=total, km_to=total + length)
        )
        total += length
    return Journey(
        stops=tuple(stops),
        legs=tuple(legs),
        icon=icon,
        show_km=bool(raw.get("km", True)),
        curve=curve,
        follow=bool(raw.get("follow", False)),
    )


def journey_markers(journey: Journey) -> list[dict[str, Any]]:
    """The stops as scene markers, cued by their names."""
    return [
        {"text": stop.text, "lon": stop.lon, "lat": stop.lat, "cue": stop.cue}
        for stop in journey.stops
    ]


def journey_zooms(journey: Journey) -> list[dict[str, Any]]:
    """With ``follow``, one ``to:`` zoom per leg, landing as its destination is spoken."""
    if not journey.follow:
        return []
    return [
        {
            "to": [leg.origin.text, leg.destination.text],
            "cue": leg.cue,
            "pad": FOLLOW_PAD,
            "min_span": FOLLOW_MIN_SPAN,
        }
        for leg in journey.legs
    ]


def km_label(value: int) -> str:
    return f"{indian_format(int(value))} KM"


def curved_leg(start: Point, end: Point, *, curve: float) -> list[Point]:
    """A quadratic bow from ``start`` to ``end`` in plate units.

    The control point sits ``curve x length`` off the chord's midpoint, so the
    curve's own midpoint bows ``curve x length / 2``. ``curve == 0`` is the chord.
    """
    if curve == 0.0:
        return [start, end]
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return [start, end]
    nx, ny = -dy / length, dx / length
    cx = (x0 + x1) / 2 + nx * curve * length
    cy = (y0 + y1) / 2 + ny * curve * length
    points: list[Point] = []
    for step in range(CURVE_SAMPLES + 1):
        t = step / CURVE_SAMPLES
        u = 1 - t
        points.append(
            (u * u * x0 + 2 * u * t * cx + t * t * x1, u * u * y0 + 2 * u * t * cy + t * t * y1)
        )
    points[0], points[-1] = start, end
    return points


def _catmull_rom(points: Sequence[Point]) -> list[Point]:
    """A smooth road through ``points`` (centripetal-free uniform Catmull-Rom)."""
    if len(points) < 3:
        return list(points)
    padded = [points[0], *points, points[-1]]
    out: list[Point] = [points[0]]
    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[i - 1], padded[i], padded[i + 1], padded[i + 2]
        for step in range(1, ROAD_SAMPLES + 1):
            t = step / ROAD_SAMPLES
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(  # type: ignore[arg-type]
                    0.5
                    * (
                        2 * p1[k]
                        + (-p0[k] + p2[k]) * t
                        + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                        + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3
                    )
                    for k in (0, 1)
                )
            )
    out[-1] = points[-1]
    return out


def heart_leg(start: Point, end: Point) -> list[Point]:
    """``start`` to the chord's midpoint, round an upright heart, then on to ``end``.

    The classic heart curve (x = 16 sin^3 t, y = 13 cos t - 5 cos 2t - 2 cos 3t
    - cos 4t), its tip on the midpoint, drawn on the side above the chord
    (screen ``y`` grows downward) and ``HEART_SIZE`` of the leg wide.
    """
    (x0, y0), (x1, y1) = start, end
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return [start, end]
    ux, uy = dx / length, dy / length
    vx, vy = -uy, ux
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    s = HEART_SIZE * length / 32.0
    loop: list[Point] = []
    for step in range(HEART_SAMPLES + 1):
        t = math.pi + 2 * math.pi * step / HEART_SAMPLES
        hx = 16 * math.sin(t) ** 3
        hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) - 17
        loop.append((mx + s * (hx * ux + hy * vx), my + s * (hx * uy + hy * vy)))
    return [start, *loop, end]


def leg_plate_points(
    leg: Leg, project: Callable[[float, float], Point], *, curve: float
) -> list[Point]:
    """The leg on the plate: a heart, a smooth road through ``via``, else a bowed chord."""
    projected = [project(lon, lat) for lon, lat in leg.points]
    if leg.destination.shape == "heart":
        return heart_leg(projected[0], projected[-1])
    if len(projected) > 2:
        return _catmull_rom(projected)
    return curved_leg(projected[0], projected[-1], curve=curve)


def path_and_length(points: Sequence[Point]) -> tuple[str, float]:
    parts: list[str] = []
    length = 0.0
    for position, (x, y) in enumerate(points):
        parts.append(f"{'M' if position == 0 else 'L'}{x:.1f} {y:.1f}")
        if position:
            length += math.hypot(x - points[position - 1][0], y - points[position - 1][1])
    return "".join(parts), length


def project_legs(
    journey: Journey,
    project: Callable[[float, float], Point],
    *,
    delays: Sequence[float],
    seconds: float,
) -> list[ProjectedLeg]:
    """Every leg's path, draw window and counter anchor."""
    legs: list[ProjectedLeg] = []
    starts = list(delays)
    for position, leg in enumerate(journey.legs):
        points = leg_plate_points(leg, project, curve=journey.curve)
        path, length = path_and_length(points)
        start = starts[position]
        following = starts[position + 1] if position + 1 < len(starts) else seconds
        draw = max(0.6, min(2.2, following - start - 0.15, seconds - start - 0.2))
        legs.append(
            ProjectedLeg(
                path=path,
                length=length,
                delay=start,
                draw=draw,
                km_from=leg.km_from,
                km_to=leg.km_to,
                label_at=points[len(points) // 2],
                until=following if position + 1 < len(starts) else math.inf,
                color=leg.destination.color,
            )
        )
    return legs


def _icon_svg(icon: str, size: float, color: str) -> str:
    """A sticker-style icon centred on its own origin."""
    s = size
    if icon == "car":
        return (
            f'<rect x="{-s * 0.62:.1f}" y="{-s * 0.30:.1f}" width="{s * 1.24:.1f}" '
            f'height="{s * 0.46:.1f}" rx="{s * 0.16:.1f}" fill="{color}" stroke="#fff" '
            f'stroke-width="{s * 0.08:.1f}"/>'
            f'<path d="M{-s * 0.36:.1f} {-s * 0.30:.1f} L{-s * 0.20:.1f} {-s * 0.56:.1f} '
            f'L{s * 0.24:.1f} {-s * 0.56:.1f} L{s * 0.40:.1f} {-s * 0.30:.1f} Z" '
            f'fill="{color}" stroke="#fff" stroke-width="{s * 0.08:.1f}"/>'
            f'<circle cx="{-s * 0.34:.1f}" cy="{s * 0.18:.1f}" r="{s * 0.15:.1f}" fill="#1b1b1b" '
            f'stroke="#fff" stroke-width="{s * 0.06:.1f}"/>'
            f'<circle cx="{s * 0.34:.1f}" cy="{s * 0.18:.1f}" r="{s * 0.15:.1f}" fill="#1b1b1b" '
            f'stroke="#fff" stroke-width="{s * 0.06:.1f}"/>'
        )
    if icon == "plane":
        return (
            f'<path d="M{-s * 0.6:.1f} 0 L{s * 0.6:.1f} 0 M{-s * 0.1:.1f} {-s * 0.45:.1f} '
            f'L{s * 0.15:.1f} 0 L{-s * 0.1:.1f} {s * 0.45:.1f}" stroke="{color}" '
            f'stroke-width="{s * 0.16:.1f}" stroke-linecap="round" fill="none"/>'
        )
    return (
        f'<circle r="{s * 0.22:.1f}" fill="{color}" stroke="#fff" stroke-width="{s * 0.06:.1f}"/>'
    )


def legs_svg(
    legs: Sequence[ProjectedLeg],
    *,
    index: int,
    icon: str,
    line_color: str,
    icon_color: str,
    stroke: float,
    icon_size: float,
) -> str:
    """Leg paths (hidden by their dash until drawn) and one icon per leg."""
    parts: list[str] = []
    for position, leg in enumerate(legs):
        draw_var = f"--map-leg-{index}-{position}"
        parts.append(
            f'<path class="map-leg map-leg-{position}" d="{leg.path}" fill="none" '
            f'stroke="{html.escape(leg.color or line_color, quote=True)}" stroke-width="{stroke:.1f}" stroke-linecap="round" '
            f'stroke-linejoin="round" stroke-dasharray="{leg.length:.1f}" '
            f'style="stroke-dashoffset:var({draw_var}, {leg.length:.1f})"/>'
        )
    if icon != "none":
        for position, _leg in enumerate(legs):
            parts.append(
                f'<g class="map-journey-icon map-journey-icon-{position}">'
                f"{_icon_svg(icon, icon_size, icon_color)}</g>"
            )
    return "".join(parts)


def _visibility(start: float, until: float, seconds: float) -> str:
    """0 before ``start``, 1 until ``until`` (or the end), as scene percentages."""
    a = max(0.0, min(100.0, start / seconds * 100.0))
    if math.isinf(until) or until >= seconds:
        return f"0%, {a:.3f}% {{ --v: 0; }} {min(100.0, a + 0.01):.3f}%, 100% {{ --v: 1; }}"
    b = max(a + 0.02, min(100.0, until / seconds * 100.0))
    return (
        f"0%, {a:.3f}% {{ --v: 0; }} {a + 0.01:.3f}%, {b:.3f}% {{ --v: 1; }} "
        f"{b + 0.01:.3f}%, 100% {{ --v: 0; }}"
    )


def legs_css(
    legs: Sequence[ProjectedLeg],
    *,
    index: int,
    seconds: float,
    icon: str,
    width_var: str,
    base: float = 4.0,
) -> tuple[str, list[str]]:
    """Custom properties + keyframes for each leg, and the wrapper animations."""
    blocks: list[str] = []
    animations: list[str] = []
    for position, leg in enumerate(legs):
        draw_var = f"--map-leg-{index}-{position}"
        blocks.append(
            f"      @property {draw_var} {{ syntax: '<number>'; inherits: true; "
            f"initial-value: {leg.length:.1f}; }}\n"
            f"      @keyframes map_leg_{index}_{position} {{ from {{ {draw_var}: {leg.length:.1f}; }} "
            f"to {{ {draw_var}: 0; }} }}\n"
            f"      .map-{index} .map-leg-{position} {{ stroke-width: var({width_var}, 3); }}\n"
        )
        animations.append(
            f"map_leg_{index}_{position} {leg.draw:.3f}s ease-in-out {leg.delay:.3f}s both"
        )
        if icon == "none":
            continue
        pos_var = f"--map-icon-{index}-{position}"
        on_var = f"--map-icon-on-{index}-{position}"
        visibility = _visibility(leg.delay, leg.until, seconds).replace("--v", on_var)
        blocks.append(
            f"      @property {pos_var} {{ syntax: '<percentage>'; inherits: true; initial-value: 0%; }}\n"
            f"      @property {on_var} {{ syntax: '<number>'; inherits: true; initial-value: 0; }}\n"
            f"      @keyframes map_icon_{index}_{position} {{ from {{ {pos_var}: 0%; }} "
            f"to {{ {pos_var}: 100%; }} }}\n"
            f"      @keyframes map_icon_on_{index}_{position} {{ {visibility} }}\n"
            f"      .map-{index} .map-journey-icon-{position} {{\n"
            f'        offset-path: path("{leg.path}"); offset-distance: var({pos_var}, 0%);\n'
            f"        offset-rotate: 0deg; opacity: var({on_var}, 0);\n"
            # The icon lives in the zoomed map layer; the zoom-compensated
            # stroke width doubles as its counter-scale, so it keeps its size.
            f"        transform-box: fill-box; transform-origin: center;\n"
            f"        transform: scale(calc(var({width_var}, {base:g}) / {base:g})); }}\n"
        )
        animations.append(
            f"map_icon_{index}_{position} {leg.draw:.3f}s ease-in-out {leg.delay:.3f}s both"
        )
        animations.append(f"map_icon_on_{index}_{position} {seconds:.3f}s linear 0s both")
    return "".join(blocks), animations


def _km_steps(start: int, end: int) -> list[int]:
    if start == end:
        return [end]
    last = KM_STEPS - 1
    values = [round(start + (end - start) * (1 - (1 - i / last) ** 3)) for i in range(KM_STEPS)]
    values[0], values[-1] = start, end
    return [v for i, v in enumerate(values) if i == 0 or v != values[i - 1]]


def km_markup(legs: Sequence[ProjectedLeg], *, index: int, seconds: float = 0.0) -> tuple[str, str]:
    """One counter per leg, on the camera track ``km_{i}``; it counts during the draw.

    A leg's counter hides when the next leg starts, so one running total is on
    screen at a time; the last one stays.
    """
    markup: list[str] = []
    css: list[str] = []
    for position, leg in enumerate(legs):
        values = _km_steps(leg.km_from, leg.km_to)
        steps = len(values)
        spans = []
        for step, value in enumerate(values):
            name = f"map_km_{index}_{position}_{step}"
            a = 100.0 * step / steps
            b = 100.0 * (step + 1) / steps
            if step == steps - 1:
                frames = f"0%, {a:.3f}% {{ opacity: 0; }} {a + 0.01:.3f}%, 100% {{ opacity: 1; }}"
            elif step == 0:
                frames = f"0%, {b:.3f}% {{ opacity: 1; }} {b + 0.01:.3f}%, 100% {{ opacity: 0; }}"
            else:
                frames = (
                    f"0%, {a:.3f}% {{ opacity: 0; }} {a + 0.01:.3f}%, {b:.3f}% {{ opacity: 1; }} "
                    f"{b + 0.01:.3f}%, 100% {{ opacity: 0; }}"
                )
            css.append(f"      @keyframes {name} {{ {frames} }}\n")
            spans.append(
                f'<span class="map-km-step" style="animation: {name} {leg.draw:.3f}s linear '
                f'{leg.delay:.3f}s both">{html.escape(km_label(value))}</span>'
            )
        hide = ""
        if not math.isinf(leg.until) and seconds > 0:
            name = f"map_km_hide_{index}_{position}"
            css.append(
                f"      @keyframes {name} {{ from {{ opacity: 1; }} to {{ opacity: 0; }} }}\n"
            )
            hide = f' style="animation: {name} 0.25s ease-in {leg.until:.3f}s both"'
        markup.append(
            f'<div class="map-km map-km-{position}" data-camera-track="km_{position}" '
            f'style="left:{leg.label_at[0]:.1f}px;top:{leg.label_at[1]:.1f}px">'
            f'<span class="map-km-box"{hide}><span class="map-km-count">{"".join(spans)}</span>'
            "</span></div>"
        )
    return "".join(markup), "".join(css)
