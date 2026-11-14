"""Render a geographic scene: a country silhouette, place markers and a route.

The scene is one self-contained HTML block, like a typographic scene: the
compiler wraps it in the root-level ``<div class="clip">`` HyperFrames needs
for seeking, and everything here nests inside it. There is no JavaScript and
no network call — the geometry comes from :mod:`video_editor_maps.geo`, the
motion is CSS keyframes, and a frame seek reproduces any instant.

The layout is the editorial four-zone plate the reference format uses:

* a subject badge top-left,
* the map panel centred-left, neighbourhood ghosted and the subject filled,
* markers for the places the narration names and, optionally, a route drawn
  between them,
* numbered callout cards on the right, each revealed on the word that earns it,
* the title block (eyebrow, display headline, subtitle) bottom-left.

Every zone is optional except the map itself, so a section card or a closing
card can reuse the same renderer without inventing a second one.

The plate is authored twice: 16:9 for a landscape frame and 9:16 for a
portrait one. A canvas at least as tall as it is wide takes the portrait
plate, which stacks the zones (title above the map, callout cards below it)
instead of shrinking the four-column landscape arrangement into type too
small to read. Any other aspect contains the matching plate with one uniform
scale and letterboxes the remainder in paper rather than stretching it.
"""

from __future__ import annotations

import html
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import Any

import structlog
from video_editor_core.composition import MotionSpec
from video_editor_core.cues import declared_cue, find_cue, resolve_cues
from video_editor_core.designs import DesignTokens
from video_editor_core.fonts import font_stack
from video_editor_core.motion.registry import MotionError, build_motion
from video_editor_core.paper import colors_for, paper_css, paper_layers, safe_css_color

from video_editor_maps.geo import (
    Country,
    GeoError,
    Region,
    River,
    View,
    _projected_y,
    countries,
    country_paths,
    find_admin,
    find_country,
    find_place,
    find_region,
    find_river,
    graticule_lines,
    on_land,
    parse_bounds,
    parse_position,
    rivers,
    snap_to_land,
    view_for,
)
from video_editor_maps.journey import (
    JourneyError,
    ProjectedLeg,
    journey_markers,
    journey_zooms,
    km_markup,
    legs_css,
    legs_svg,
    parse_journey,
    project_legs,
)
from video_editor_maps.stickers import (
    StickerError,
    bubble_markup,
    overlay_css,
    parse_overlays,
    sticker_markup,
)

#: The design canvas every position below is authored against.
DESIGN_WIDTH = 1920
DESIGN_HEIGHT = 1080

#: The portrait plate. A phone frame is read at arm's length; the landscape
#: panel's three columns cannot hold a headline and a card stack at a legible
#: size, so the portrait plate reflows them into rows.
PORTRAIT_WIDTH = 1080
PORTRAIT_HEIGHT = 1920

#: Base entry motion, divided by the design's ``motion.tempo`` like the
#: typographic renderer does, so one design moves faster everywhere.
ENTRY_SECONDS = 0.35

#: The Vox-style overlay cadence: ``stutter: true`` reveals the pins, labels
#: and route in twelve steps a second while the camera keeps moving smoothly.
DEFAULT_STUTTER_RATE = 12.0

#: The frame rate an overlay step snaps to when the caller does not pass one.
#: ``render_map`` accepts ``fps``; the scene-plan adapter does not thread its
#: own frame rate through today, so a default keeps the grid deterministic.
DEFAULT_FPS = 30

#: The plate colours are shared with composite scenes through
#: :mod:`video_editor_core.paper`; only the props this scene type may use to
#: recolour them are declared here.

#: The landscape map panel, in design units: (x, y, width, height).
MAP_FRAME = (150.0, 158.0, 950.0, 604.0)

#: The portrait map panel: below the clamped title block, above the callout
#: cards. Its top is 24.2 design px below the worst-case title bottom (491.8)
#: and its bottom leaves 64 px above ``cards`` (1230).
PORTRAIT_MAP_FRAME = (72.0, 516.0, 936.0, 650.0)

#: Title block metrics: the margins between its parts and the line box for the
#: eyebrow and subtitle (the headline sets ``line-height:1`` in CSS). The
#: stylesheet and the map-frame fit check both read these, so a long title is
#: clamped rather than allowed to run into the map.
TITLE_EYEBROW_MARGIN = 18.0
TITLE_HEADLINE_MARGIN = 10.0
TITLE_SUBTITLE_MARGIN = 16.0
TITLE_LINE_HEIGHT = 1.2
TITLE_MAX_LINES = 2

#: How far past the subject's own bounds the window reaches, as a fraction.
MAP_PADDING = 0.22

#: The watermark is texture, but the craft check still holds it to the reading
#: floor (2.6% of the short side, 28 px at both authored plates).
MARK_SIZE = 28.0

#: Horizontal gap between a pin and its place label, in design units.
LABEL_MARGIN = 24.0

#: Estimated glyph advance and tracking of the place label face, in em. The
#: stylesheet reads ``LABEL_TRACKING`` so the QA box estimate follows the text
#: the renderer actually paints.
LABEL_ADVANCE = 0.62
LABEL_TRACKING = 0.13

#: The route stroke, authored in design units and scaled to plate pixels. It
#: cannot use ``non-scaling-stroke`` (its dash draw-in is in user units), so
#: the camera keyframes divide it by the zoom factor instead.
ROUTE_STROKE = 4.6

#: The river palette: a light core over a dark casing so the line reads on
#: both the navy subject and the pale neighbours. A design or scene may set
#: ``river``. The flow layer is near-white.
RIVER_COLOR = "#9FD0FF"

#: River widths in design units. The main course is a 4 px core, an unjoinable
#: piece reads as a tributary at 2.6, and the flow layer is a 2 px dash.
RIVER_STROKE = 4.0
RIVER_TRIBUTARY_STROKE = 2.6
RIVER_CASING_SCALE = 2.2
RIVER_CASING_OPACITY = 0.55
RIVER_FLOW_STROKE = 2.0
RIVER_FLOW_DASH = 10.0
RIVER_FLOW_GAP = 18.0
RIVER_FLOW_COLOR = "#FFFFFF"
RIVER_FLOW_OPACITY = 0.85

#: Region styling. The pattern fills are authored in plate units and clipped
#: to each polygon; the outline is a thin non-scaling stroke.
REGION_OUTLINE_WIDTH = 1.2
REGION_OUTLINE_COLOR = "#9FD0FF"
REGION_OUTLINE_OPACITY = 0.65
REGION_TINT = "#8FA3C4"
REGION_TINT_OPACITY = 0.16
REGION_SAND = "#D9B77A"
REGION_HATCH_OPACITY = 0.35
REGION_RIDGE_OPACITY = 0.55
REGION_RELIEF_OPACITY = 0.42
REGION_STYLES = frozenset(
    {"range", "relief", "desert", "plateau", "plain", "basin", "valley", "delta", "fill", "photo"}
)
#: An image embedded in a ``photo`` region is resized to this longest side.
REGION_PHOTO_MAX_PX = 1000
#: A ``fill`` region's opacity once revealed.
REGION_FILL_OPACITY = 0.88
#: A region's data kind picks its default style; mountain ranges draw soft
#: relief hills by default (#349), and ``style: range`` keeps the ridge hatch.
_DEFAULT_REGION_STYLE = {"range": "relief"}
#: Region styles drawn with a tile pattern (the rest are a flat tint).
_PATTERN_STYLES = frozenset({"range", "relief", "desert", "plateau", "delta"})

#: Minimum great-circle samples per route segment. A long-haul arc also takes
#: one sample per degree so consecutive points never sit more than a degree
#: apart, which keeps the bow smooth without emitting a point every pixel.
GREAT_CIRCLE_SAMPLES = 24

#: Above this camera factor the 110m outline is visibly blocky, so the
#: renderer swaps the subject and neighbour geometry for the bundled 50m set.
ZOOM_DETAIL_FACTOR = 2.5

#: The camera's easing curve. The width keyframes re-sample it so the route
#: stroke stays the same on-screen width while the camera pushes in.
CAMERA_EASE = "cubic-bezier(.4,0,.2,1)"
_CAMERA_EASE_POINTS = (0.4, 0.0, 0.2, 1.0)

#: Width samples per camera move. The width is linear in time between samples,
#: so twelve evenly spaced samples keep ``width * camera scale`` within 5%.
_ROUTE_WIDTH_SAMPLES = 12

#: Props a scene may use to recolour the plate without a whole design.
#: ``label`` is deliberately absent: in a map scene that prop is the
#: place name, not a colour. ``eyebrow`` is absent for the same reason (it is
#: display copy); a scene recolours the eyebrow with ``eyebrow_color``.
_OVERRIDABLE = ("highlight", "pin", "ink", "rule")

#: Props that recolour a plate under a name the display copy does not use.
_PROP_COLOURS = {"eyebrow_color": "eyebrow"}

#: Design-unit rects: (x, y, width) and (x, y, width, height).
Rect3 = tuple[float, float, float]
Rect4 = tuple[float, float, float, float]

logger = structlog.get_logger(__name__)


class MapSceneError(ValueError):
    """A map scene whose props cannot be rendered."""


@dataclass(frozen=True)
class _Geom:
    """One authored map plate: its canvas and its zone rectangles.

    Every measurement is in design units, multiplied by the canvas scale when
    drawn, so both plates share one stylesheet.
    """

    width: float
    height: float
    #: The geographic window the map is fitted to.
    map_frame: Rect4
    badge: tuple[float, float]
    badge_disc: float
    badge_initial: float
    badge_gap: float
    badge_name: float
    badge_sub: float
    #: Callout card stack: (left, top, width), then row metrics.
    cards: Rect3
    cards_gap: float
    card_height: float
    card_num: float
    card_num_width: float
    card_text: float
    card_pad: float
    card_rule_margin: float
    #: Title block: (left, top, width), then the rule and type sizes.
    title: Rect3
    rule: tuple[float, float]
    eyebrow: float
    headline: float
    subtitle: float
    mark: tuple[float, float]
    place: float


_LANDSCAPE = _Geom(
    width=float(DESIGN_WIDTH),
    height=float(DESIGN_HEIGHT),
    map_frame=MAP_FRAME,
    badge=(120.0, 74.0),
    badge_disc=70.0,
    badge_initial=32.0,
    badge_gap=18.0,
    badge_name=28.0,
    badge_sub=28.0,
    cards=(1148.0, 198.0, 672.0),
    cards_gap=28.0,
    card_height=80.0,
    card_num=28.0,
    card_num_width=64.0,
    card_text=28.0,
    card_pad=26.0,
    card_rule_margin=14.0,
    title=(150.0, 800.0, 980.0),
    rule=(72.0, 3.0),
    eyebrow=28.0,
    headline=88.0,
    subtitle=28.0,
    mark=(1500.0, 1006.0),
    place=28.0,
)

_PORTRAIT = _Geom(
    width=float(PORTRAIT_WIDTH),
    height=float(PORTRAIT_HEIGHT),
    map_frame=PORTRAIT_MAP_FRAME,
    badge=(72.0, 96.0),
    badge_disc=76.0,
    badge_initial=34.0,
    badge_gap=18.0,
    badge_name=28.0,
    badge_sub=28.0,
    cards=(72.0, 1230.0, 936.0),
    cards_gap=24.0,
    card_height=84.0,
    card_num=28.0,
    card_num_width=64.0,
    card_text=28.0,
    card_pad=26.0,
    card_rule_margin=14.0,
    title=(72.0, 200.0, 936.0),
    rule=(72.0, 3.0),
    eyebrow=28.0,
    headline=72.0,
    subtitle=28.0,
    mark=(72.0, 1830.0),
    place=28.0,
)


def _geom_for(canvas_w: float, canvas_h: float, *, full: bool = False) -> _Geom:
    """The plate authored for this frame: portrait at least as tall as wide.

    ``full`` lets the map fill the whole plate (titles float over it), the
    look of a map-story reel; the framed default keeps its zones.
    """
    geom = _PORTRAIT if canvas_h >= canvas_w else _LANDSCAPE
    if full:
        return replace(geom, map_frame=(0.0, 0.0, geom.width, geom.height))
    return geom


def _title_max_bottom(geom: _Geom) -> float:
    """The lowest the clamped title block can reach, in design units.

    The eyebrow is one line; the headline and subtitle are clamped to two.
    The headline's CSS line-height is 1, the others use
    :data:`TITLE_LINE_HEIGHT`.
    """
    return (
        geom.title[1]
        + geom.rule[1]
        + TITLE_EYEBROW_MARGIN
        + geom.eyebrow * TITLE_LINE_HEIGHT
        + TITLE_HEADLINE_MARGIN
        + geom.headline * 1 * TITLE_MAX_LINES
        + TITLE_SUBTITLE_MARGIN
        + geom.subtitle * TITLE_LINE_HEIGHT * TITLE_MAX_LINES
    )


@dataclass(frozen=True)
class MapSceneRender:
    """The scene's inner markup plus the CSS/keyframes it needs.

    ``dropped_zooms`` lists the 0-based indexes of every zoom whose move could
    not land before the cut. They are recorded rather than silently skipped: a
    zoom that never plays leaves its cue matching narration that earns nothing.
    """

    markup: str
    css: str
    dropped_zooms: tuple[int, ...] = ()


@dataclass(frozen=True)
class _DroppedZoom:
    """One zoom the camera schedule could not fit before the scene cut."""

    index: int
    needed: float
    available: float


@dataclass(frozen=True)
class MapCallout:
    """One numbered fact and the spoken phrase that reveals it."""

    text: str
    cue: str


@dataclass(frozen=True)
class MapMarker:
    """One place named on the map, at a geographic position."""

    text: str
    lon: float
    lat: float
    cue: str
    callout: bool = False
    #: A sea or ocean label: exempt from snapping and the on-land check.
    sea: bool = False


#: A marker typed this close offshore is moved onto the coast (#342).
MARKER_SNAP_KM = 15.0

#: Point-of-view border sets (``geo`` worldviews) used by default for a subject.
_DEFAULT_WORLDVIEWS = {"IND": "ind"}
_NO_WORLDVIEW = {"none", "default", "de_facto", "off", ""}


@dataclass(frozen=True)
class MapRiver:
    """A named river to draw from its real centreline geometry."""

    name: str
    cue: str
    label: str
    flow: bool
    features: tuple[River, ...]
    callout: bool = False


@dataclass(frozen=True)
class MapRegion:
    """A named geography region to shade from its real polygons."""

    name: str
    cue: str
    label: str
    style: str
    region: Region
    callout: bool | None = None
    #: ``fill``: the solid colour (default: the design highlight).
    color: str = ""
    #: ``photo``: the image as a data URI, cut into the region's shape.
    image: str = ""


@dataclass(frozen=True)
class MapRoute:
    """A line drawn through geographic points, optionally cue-timed."""

    points: tuple[tuple[float, float], ...]
    cue: str


@dataclass(frozen=True)
class MapZoom:
    """A tighter map window the camera pushes in to, optionally cue-timed.

    ``bounds`` is the geographic window the panel fills once the move lands;
    the renderer turns base-view plate coordinates into target-view ones with
    one uniform scale plus a translation, so the move is pure CSS and seeks
    deterministically like every other reveal.

    A zoom authored with ``to:`` names its subject rather than a box. Its
    bounds are the padded union of those features, and ``fit`` asks
    :func:`_zoom_view` to grow the shorter side in projection space until the
    window has the map panel's own aspect ratio. A ``bbox:`` zoom is already
    the authored window, so ``fit`` stays false and its bounds pass through.
    """

    bounds: tuple[float, float, float, float]
    cue: str
    fit: bool = False


@dataclass(frozen=True)
class _ProjectedMarker:
    """A marker placed on the plate, with the delay its reveal starts at."""

    text: str
    x: float
    y: float
    delay: float
    #: The geographic position, kept so a move can tell whether the anchor is
    #: meant to stay in the zoomed window or is deliberately leaving the shot.
    lon: float = 0.0
    lat: float = 0.0
    callout: bool = False


@dataclass(frozen=True)
class _ProjectedRoute:
    """A route path in plate coordinates, its length, and when it draws."""

    path: str
    length: float
    delay: float


@dataclass(frozen=True)
class _RiverPiece:
    """One clipped river polyline projected into the plate."""

    path: str
    length: float
    #: True for the joined main course, False for an unjoinable tributary.
    main: bool


@dataclass(frozen=True)
class _PreparedRiver:
    """A river's projected pieces, its reveal delay and its flow setting."""

    name: str
    label: str
    delay: float
    flow: bool
    pieces: tuple[_RiverPiece, ...]
    label_anchor: _ProjectedMarker | None = None
    label_points: tuple[tuple[float, float] | None, ...] = ()
    callout: bool = False


@dataclass(frozen=True)
class _RegionShape:
    """One projected region polygon and its outline length."""

    path: str
    length: float


@dataclass(frozen=True)
class _PreparedRegion:
    """A region's projected polygons, reveal timing and optional label."""

    name: str
    label: str
    delay: float
    style: str
    shapes: tuple[_RegionShape, ...]
    label_anchor: _ProjectedMarker | None = None
    label_points: tuple[tuple[float, float] | None, ...] = ()
    view_shapes: tuple[tuple[tuple[tuple[float, float], ...], ...], ...] = ()
    callout: bool | None = None
    color: str = ""
    image: str = ""
    #: Plate-space box of the region (photo placement).
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class _LabelSpec:
    """One overlay label before side/nudge layout."""

    kind: str
    position: int
    text: str
    x: float
    y: float
    clamp_row: int = -1
    delay: float = 0.0
    view_points: tuple[tuple[float, float] | None, ...] | None = None
    placement: str = "side"
    key: str = ""
    view_shapes: tuple[tuple[tuple[tuple[float, float], ...], ...], ...] | None = None


@dataclass(frozen=True)
class _LabelPlacement:
    """The placement chosen for one label."""

    side: str = "right"
    nudge: float = 0.0
    mode: str = "side"
    offset: tuple[float, float] = (0.0, 0.0)


@dataclass(frozen=True)
class _Track:
    """A pin or label anchor that follows the camera without being scaled.

    The camera scales its artwork, but place names must keep their reading
    size. Each track therefore gets its own position keyframes computed from
    the same moves, and ``reveal`` stays a plain opacity fade.
    """

    name: str
    x: float
    y: float
    reveal: str
    seconds: float
    delay: float
    #: The reveal's curve; the marker/place fades use the default ``ease-out``.
    ease: str = "ease-out"
    #: Marker pins drop with a settle; their label keeps the plain fade.
    drop: bool = False
    #: Keep a label anchor inside the map frame at every camera landing.
    clamp: bool = False
    #: Extra vertical rows for clamped labels so two cannot stack on the edge.
    row: int = 0
    #: Custom per-view plate points for region/river labels.
    points: Sequence[tuple[float, float] | None] | None = None
    #: Whether the label is visible in each view (region/river labels only).
    visible: Sequence[bool] | None = None


def _entry_seconds(design: DesignTokens) -> float:
    """The tempo-scaled entry motion length, falling back to the base value."""
    try:
        tempo = float(design.motion.tempo)
    except (TypeError, ValueError):
        return ENTRY_SECONDS
    if not math.isfinite(tempo) or tempo <= 0.0:
        return ENTRY_SECONDS
    return ENTRY_SECONDS / tempo


def _seconds(duration: float) -> float:
    """A finite, non-negative scene length; degenerate input becomes ``0.0``."""
    try:
        value = float(duration)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0.0 else 0.0


def _text(props: Mapping[str, Any], key: str) -> str:
    return str(props.get(key) or "").strip()


def _flag(props: Mapping[str, Any], key: str) -> bool:
    """A boolean prop, accepting the JSON/YAML spellings of true."""
    value = props.get(key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value is True


def _label_text(entry: Mapping[str, Any], name: str) -> str:
    """The label to draw: a non-empty override, the data name for ``true``, else none."""
    value = entry.get("label")
    if value is True:
        return name
    if isinstance(value, str):
        return value.strip()
    return ""


def _stutter_rate(props: Mapping[str, Any]) -> float | None:
    """The overlay step rate, or ``None`` when the scene moves smoothly.

    ``stutter: true`` takes :data:`DEFAULT_STUTTER_RATE`; a number is the rate
    in steps a second. A non-positive or non-finite rate fails closed rather
    than silently falling back to smooth motion.
    """
    value = props.get("stutter")
    if value is None or value is False:
        return None
    if value is True:
        return DEFAULT_STUTTER_RATE
    if isinstance(value, str):
        text = value.strip().lower()
        if not text or text in {"false", "no", "off", "none"}:
            return None
        if text in {"true", "yes", "on"}:
            return DEFAULT_STUTTER_RATE
        value = text
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise MapSceneError(f"map stutter must be a rate or true, got {value!r}") from None
    if not math.isfinite(rate) or rate <= 0.0:
        raise MapSceneError(f"map stutter rate must be positive, got {value!r}")
    return rate


def _fps(value: int) -> float:
    """A usable frame rate for the overlay's step grid."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_FPS)
    return rate if math.isfinite(rate) and rate > 0.0 else float(DEFAULT_FPS)


def _steps_timing(duration: float, *, stutter: float, fps: int) -> str:
    """``steps(N, end)`` for an animation of ``duration`` on the frame grid.

    The step length snaps to whole frames so the stutter lands on frames a
    seek can reproduce: ``step_frames = round(fps / rate)``. ``N`` is then how
    many of those steps the animation's own duration spans.
    """
    frame_rate = _fps(fps)
    step_frames = max(1, round(frame_rate / stutter))
    step_seconds = step_frames / frame_rate
    count = max(1, round(duration / step_seconds))
    return f"steps({count}, end)"


def _colors(design: DesignTokens, props: Mapping[str, Any]) -> dict[str, str]:
    """Defaults, then the design's ``layers.map``, then the scene's overrides."""
    colors = colors_for(
        design,
        props,
        section="map",
        overridable=_OVERRIDABLE,
        aliases=_PROP_COLOURS,
    )
    # ``river`` is new to map plates: the shared defaults predate it, so it is
    # layered here rather than in the shared palette.
    colors.setdefault("river", RIVER_COLOR)
    declared = getattr(design.layers, "map", None)
    if isinstance(declared, Mapping):
        value = declared.get("river")
        if isinstance(value, str) and value.strip():
            colors["river"] = value
    override = str(props.get("river") or "").strip()
    if override:
        colors["river"] = override
    return colors


def _callouts(props: Mapping[str, Any]) -> list[MapCallout]:
    raw = props.get("callouts")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map callouts must be a list of strings or {text, cue} maps")
    callouts: list[MapCallout] = []
    for entry in raw:
        if isinstance(entry, str):
            text = entry.strip()
            cue = text
        elif isinstance(entry, Mapping):
            text = str(entry.get("text") or "").strip()
            cue = declared_cue(entry.get("cue"), text)
        else:
            raise MapSceneError(f"a map callout must be text or a map, got {entry!r}")
        if not text:
            raise MapSceneError("a map callout cannot be empty")
        callouts.append(MapCallout(text=text, cue=cue))
    return callouts


def _coordinate(entry: Mapping[str, Any], key: str, what: str) -> float:
    """Read one finite lon/lat component, or fail with the entry named."""
    try:
        value = float(entry[key])
    except (KeyError, TypeError, ValueError):
        raise MapSceneError(f"{what} needs numeric {key!r}, got {entry!r}") from None
    if not math.isfinite(value):
        raise MapSceneError(f"{what} needs a finite {key!r}, got {entry!r}")
    return value


def _callout_flag(entry: Mapping[str, Any]) -> bool | None:
    """The ``callout`` prop: True/False, or None when the scene did not set it."""
    value = entry.get("callout")
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return value is True


def _journey_river_course(name: str) -> tuple[tuple[float, float], ...]:
    """A river's main source-to-mouth course, for journey legs that follow it."""
    try:
        features = find_river(name)
    except GeoError as exc:
        raise ValueError(str(exc)) from exc
    return _river_course(features)[0]


def _scene_adm0(props: Mapping[str, Any]) -> str:
    """The scene country's ADM0 code, or "" when it does not resolve."""
    try:
        return find_country(_text(props, "country")).adm0
    except GeoError:
        return ""


def _worldview(props: Mapping[str, Any]) -> str:
    """The border point of view for this scene (#343).

    ``worldview: ind`` draws disputed borders as India claims them; a map whose
    subject is India uses it by default, and ``worldview: none`` opts out.
    """
    raw = props.get("worldview")
    if raw is None:
        return _DEFAULT_WORLDVIEWS.get(_scene_adm0(props), "")
    value = str(raw).strip().lower()
    if value in _NO_WORLDVIEW:
        return ""
    if value not in set(_DEFAULT_WORLDVIEWS.values()):
        raise MapSceneError(f"unknown map worldview {raw!r}; choose ind or none")
    return value


def _marker_position(
    entry: Mapping[str, Any], text: str, props: Mapping[str, Any]
) -> tuple[float, float]:
    """Typed lon/lat, or a gazetteer lookup of ``place`` (default: the text)."""
    if entry.get("lon") is not None or entry.get("lat") is not None:
        return _coordinate(entry, "lon", "a map marker"), _coordinate(entry, "lat", "a map marker")
    name = str(entry.get("place") or text).strip()
    country = str(entry.get("country") or "").strip()
    try:
        if country:
            place = find_place(name, country=country)
        else:
            home = _scene_adm0(props)
            try:
                place = find_place(name, country=home) if home else find_place(name)
            except GeoError:
                place = find_place(name)
    except GeoError as exc:
        raise MapSceneError(f"map marker {text!r}: {exc}") from exc
    return place.lon, place.lat


def _markers(props: Mapping[str, Any]) -> list[MapMarker]:
    """Places named on the map, in reveal order.

    Each entry is ``{text, lon, lat, cue}``, or ``{text, place?, country?}``
    resolved from the bundled gazetteer so scripts need not type coordinates.
    A city marker a few kilometres offshore snaps onto the coast; ``sea: true``
    marks a sea label that stays where it is.
    """
    raw = props.get("markers")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map markers must be a list of {text, lon, lat, cue} maps")
    worldview = _worldview(props)
    markers: list[MapMarker] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise MapSceneError(f"a map marker must be a map, got {entry!r}")
        text = str(entry.get("text") or "").strip()
        if not text:
            raise MapSceneError("a map marker needs text")
        lon, lat = _marker_position(entry, text, props)
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise MapSceneError(f"map marker {text!r} is not a lon/lat pair")
        sea = _flag(entry, "sea")
        if not sea:
            snapped = snap_to_land(lon, lat, max_km=MARKER_SNAP_KM, worldview=worldview)
            if snapped is not None and snapped != (lon, lat):
                logger.info("map_marker_snapped", text=text, typed=(lon, lat), snapped=snapped)
                lon, lat = snapped
        markers.append(
            MapMarker(
                text=text,
                lon=lon,
                lat=lat,
                cue=declared_cue(entry.get("cue"), text),
                callout=bool(_callout_flag(entry)),
                sea=sea,
            )
        )
    return markers


def map_scene_marker_problems(scene: Mapping[str, Any]) -> list[str]:
    """Markers that still sit in the sea after snapping (#342)."""
    props = scene.get("props") or {}
    scene_id = str(scene.get("id") or "scene")
    worldview = _worldview(props)
    return [
        f"{scene_id}: marker {marker.text!r} is in the sea (set sea: true for a sea label)"
        for marker in _markers(props)
        if not marker.sea and not on_land(marker.lon, marker.lat, worldview=worldview)
    ]


def _route(props: Mapping[str, Any]) -> MapRoute | None:
    """A route through ``[lon, lat]`` points, drawn between the markers."""
    raw = props.get("route")
    if raw is None:
        return None
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map route must be a list of [lon, lat] points")
    points: list[tuple[float, float]] = []
    for entry in raw:
        try:
            point = parse_position(entry)
        except GeoError as exc:
            raise MapSceneError(f"map route point {entry!r}: {exc}") from exc
        if point is None:
            raise MapSceneError("a map route point cannot be empty")
        points.append(point)
    if len(points) < 2:
        raise MapSceneError("a map route needs at least two points")
    return MapRoute(points=tuple(points), cue=declared_cue(props.get("route_cue"), ""))


def _flow_enabled(entry: Mapping[str, Any]) -> bool:
    """The flow layer is on unless the river or scene explicitly turns it off."""
    value = entry.get("flow")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off", "none"}
    return value is not False


def _rivers(props: Mapping[str, Any]) -> list[MapRiver]:
    """Named rivers to draw: ``{name, cue?, label?, flow?}`` entries."""
    raw = props.get("rivers")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map rivers must be a list of {name, cue, label} maps")
    scene_flow = _flow_enabled(props)
    parsed: list[MapRiver] = []
    for entry in raw:
        if isinstance(entry, Mapping):
            name = str(entry.get("name") or "").strip()
            label = _label_text(entry, name)
            flow = scene_flow and _flow_enabled(entry)
            cue = declared_cue(entry.get("cue"), name)
        elif isinstance(entry, str):
            name = entry.strip()
            label = ""
            flow = scene_flow
            cue = declared_cue(None, name)
        else:
            raise MapSceneError(f"a map river must be a name or a map, got {entry!r}")
        if not name:
            raise MapSceneError("a map river needs a name")
        try:
            features = find_river(name)
        except GeoError as exc:
            raise MapSceneError(str(exc)) from exc
        parsed.append(
            MapRiver(
                name=name,
                cue=cue,
                label=label,
                flow=flow,
                features=features,
                callout=bool(_callout_flag(entry)) if isinstance(entry, Mapping) else False,
            )
        )
    return parsed


def _regions(props: Mapping[str, Any]) -> list[MapRegion]:
    """Named regions to shade: ``{name, cue?, label?, style?}`` entries."""
    raw = props.get("regions")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map regions must be a list of {name, cue, label, style} maps")
    parsed: list[MapRegion] = []
    for entry in raw:
        admin_kind = ""
        if isinstance(entry, Mapping):
            admin_kind = next((k for k in ("state", "district") if entry.get(k)), "")
            name = str(entry.get(admin_kind) if admin_kind else entry.get("name") or "").strip()
            label = _label_text(entry, name)
            cue = declared_cue(entry.get("cue"), name)
            declared_style = str(entry.get("style") or "").strip().lower()
            if not declared_style and entry.get("image"):
                declared_style = "photo"
        elif isinstance(entry, str):
            name = entry.strip()
            label = ""
            cue = declared_cue(None, name)
            declared_style = ""
        else:
            raise MapSceneError(f"a map region must be a name or a map, got {entry!r}")
        if not name:
            raise MapSceneError("a map region needs a name")
        try:
            region = find_admin(name, admin_kind) if admin_kind else find_region(name)
        except GeoError as exc:
            raise MapSceneError(str(exc)) from exc
        style = declared_style or (
            "fill"
            if admin_kind
            else _DEFAULT_REGION_STYLE.get(region.kind.strip().lower(), region.kind.strip().lower())
        )
        if style not in REGION_STYLES:
            raise MapSceneError(
                f"map region {name!r} has unknown style {style!r}; "
                f"choose from {', '.join(sorted(REGION_STYLES))}"
            )
        image = ""
        color = ""
        if isinstance(entry, Mapping):
            if str(entry.get("color") or "").strip():
                try:
                    color = safe_css_color(str(entry["color"]), f"map region {name!r} colour")
                except ValueError as exc:
                    raise MapSceneError(str(exc)) from exc
            if style == "photo":
                image = _photo_data_uri(str(entry.get("image") or ""), name)
        parsed.append(
            MapRegion(
                name=name,
                cue=cue,
                label=label,
                style=style,
                region=region,
                callout=_callout_flag(entry) if isinstance(entry, Mapping) else None,
                color=color,
                image=image,
            )
        )
    return parsed


def _photo_data_uri(path: str, name: str) -> str:
    """A ``photo`` region's image, resized and embedded so no asset copy is needed."""
    import base64
    import io
    from pathlib import Path

    from PIL import Image, UnidentifiedImageError

    if not path:
        raise MapSceneError(f"map region {name!r} has style photo but no image")
    source = Path(path).expanduser()
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise MapSceneError(f"map region {name!r}: cannot read image {path!r}: {exc}") from exc
    image.thumbnail((REGION_PHOTO_MAX_PX, REGION_PHOTO_MAX_PX))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _route_curve(props: Mapping[str, Any]) -> str:
    """How route segments are drawn: ``straight`` (the default) or a great circle."""
    raw = props.get("route_curve")
    if raw is None:
        return "straight"
    value = str(raw).strip().lower().replace("-", "_")
    if value in {"", "straight", "line"}:
        return "straight"
    if value in {"great_circle", "greatcircle"}:
        return "great_circle"
    raise MapSceneError(f"map route_curve must be 'straight' or 'great_circle', got {raw!r}")


def _great_circle_points(
    start: tuple[float, float], end: tuple[float, float]
) -> list[tuple[float, float]]:
    """Spherical interpolation from ``start`` to ``end``, as lon/lat pairs.

    A great circle is the shortest path over the sphere, so a long flight line
    bows towards the pole. Points are the normalised slerp of the two unit
    vectors, sampled at least :data:`GREAT_CIRCLE_SAMPLES` times and at least
    once a degree, so consecutive samples never jump a visible gap. The exact
    endpoints are returned unchanged: the round trip through radians must not
    move a marker's own coordinate.
    """
    lon1, lat1 = (math.radians(value) for value in start)
    lon2, lat2 = (math.radians(value) for value in end)
    first = (
        math.cos(lat1) * math.cos(lon1),
        math.cos(lat1) * math.sin(lon1),
        math.sin(lat1),
    )
    second = (
        math.cos(lat2) * math.cos(lon2),
        math.cos(lat2) * math.sin(lon2),
        math.sin(lat2),
    )
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(first, second, strict=True))))
    angle = math.acos(dot)
    if angle < 1e-9:
        return [start, end]
    sin_angle = math.sin(angle)
    intervals = max(GREAT_CIRCLE_SAMPLES, math.ceil(math.degrees(angle)))
    points: list[tuple[float, float]] = []
    for step in range(intervals + 1):
        t = step / intervals
        wa = math.sin((1.0 - t) * angle) / sin_angle
        wb = math.sin(t * angle) / sin_angle
        x = wa * first[0] + wb * second[0]
        y = wa * first[1] + wb * second[1]
        z = wa * first[2] + wb * second[2]
        norm = math.hypot(x, y, z)
        points.append(
            (
                math.degrees(math.atan2(y / norm, x / norm)),
                math.degrees(math.asin(max(-1.0, min(1.0, z / norm)))),
            )
        )
    points[0] = start
    points[-1] = end
    return points


def _unwrap_longitude(lon: float, reference: float) -> float:
    """Shift ``lon`` by whole turns so it stays within 180 degrees of ``reference``.

    Sampling across the antimeridian can return a longitude on the far side of
    the sphere. Unwrapping keeps the projected segment short instead of
    drawing it across the whole frame.
    """
    while lon - reference > 180.0:
        lon -= 360.0
    while lon - reference < -180.0:
        lon += 360.0
    return lon


#: Two river endpoints within this many degrees are the same joint. Natural
#: Earth rounds to three decimals and splits a course across braids and lakes.
_RIVER_JOIN_TOLERANCE = 0.06

#: A distributary may start this far from the parent's mouth; deltas split.
_RIVER_SEA_TOLERANCE = 0.25


def _same_point(first: tuple[float, float], second: tuple[float, float], tolerance: float) -> bool:
    return abs(first[0] - second[0]) <= tolerance and abs(first[1] - second[1]) <= tolerance


def _grow_river_chain(
    chain: list[tuple[float, float]],
    unused: list[list[tuple[float, float]]],
    tolerance: float,
) -> None:
    """Extend ``chain`` at either end by matching segment endpoints in place."""
    while unused:
        match: tuple[int, str] | None = None
        for position, segment in enumerate(unused):
            if _same_point(chain[-1], segment[0], tolerance):
                match = (position, "tail")
                break
            if _same_point(chain[-1], segment[-1], tolerance):
                match = (position, "tail_reversed")
                break
        if match is None:
            for position, segment in enumerate(unused):
                if _same_point(chain[0], segment[-1], tolerance):
                    match = (position, "head")
                    break
                if _same_point(chain[0], segment[0], tolerance):
                    match = (position, "head_reversed")
                    break
        if match is None:
            return
        position, where = match
        segment = unused.pop(position)
        if where == "tail":
            chain.extend(segment[1:])
        elif where == "tail_reversed":
            chain.extend(list(reversed(segment))[1:])
        elif where == "head":
            chain[0:0] = segment[:-1]
        else:
            chain[0:0] = list(reversed(segment))[1:]


def _river_chains(
    features: Sequence[River],
) -> list[list[tuple[float, float]]]:
    """Join a river's segments into ordered chains, longest first.

    Endpoints are matched, so a river split by a lake or a braid becomes one
    course. A branch that cannot be joined is returned as its own chain and
    drawn thinner by the renderer.
    """
    unused = [list(segment) for river in features for segment in river.segments]
    chains: list[list[tuple[float, float]]] = []
    while unused:
        seed = max(range(len(unused)), key=lambda position: len(unused[position]))
        chain = list(unused.pop(seed))
        _grow_river_chain(chain, unused, _RIVER_JOIN_TOLERANCE)
        chains.append(chain)
    chains.sort(key=len, reverse=True)
    return chains


def _in_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> bool:
    """Ray-cast point-in-ring test in lon/lat space."""
    inside = False
    previous = ring[-1]
    for current in ring:
        x0, y0 = previous
        x1, y1 = current
        if (y1 > lat) != (y0 > lat):
            crossing = (x0 - x1) * (lat - y1) / (y0 - y1) + x1
            if lon < crossing:
                inside = not inside
        previous = current
    return inside


@cache
def _on_land(lon: float, lat: float) -> bool:
    """True when a point falls inside a bundled country polygon (not the sea)."""
    if not -180.0 <= lon <= 180.0:
        lon = ((lon + 180.0) % 360.0) - 180.0
    for country in countries("50m"):
        for polygon in country.polygons:
            if not polygon or not _in_ring(lon, lat, polygon[0]):
                continue
            if any(_in_ring(lon, lat, hole) for hole in polygon[1:]):
                continue
            return True
    return False


def _sea_score(point: tuple[float, float]) -> int:
    """How many of eight points 0.3 degrees around an end fall in the sea."""
    score = 0
    for step in range(8):
        angle = math.tau * step / 8.0
        lon = point[0] + 0.3 * math.cos(angle)
        lat = point[1] + 0.3 * math.sin(angle)
        if not _on_land(lon, lat):
            score += 1
    return score


def _extend_to_sea(course: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Follow endpoint-connected distributaries until the mouth reaches water.

    A delta splits into branches the main centreline stops short of; the Nile
    ends at the delta apex while the Rosetta and Damietta branches continue to
    the Mediterranean. Only a branch whose far end is more sea than the
    current mouth is taken, and the walk is bounded, so an inland-ended river
    keeps its data end.
    """
    extended = list(course)
    for _ in range(4):
        score = _sea_score(extended[-1])
        if score >= 5:
            break
        best: list[tuple[float, float]] | None = None
        best_score = score
        for river in rivers():
            for segment in river.segments:
                if _same_point(segment[0], extended[-1], _RIVER_SEA_TOLERANCE):
                    candidate = list(segment)
                    far = segment[-1]
                elif _same_point(segment[-1], extended[-1], _RIVER_SEA_TOLERANCE):
                    candidate = list(reversed(segment))
                    far = segment[0]
                else:
                    continue
                if _same_point(far, extended[0], 0.01):
                    continue
                candidate_score = _sea_score(far)
                if candidate_score > best_score:
                    best = candidate
                    best_score = candidate_score
        if best is None:
            break
        extended.extend(best[1:])
    return extended


@cache
def _river_course(
    features: tuple[River, ...],
) -> tuple[
    tuple[tuple[float, float], ...],
    tuple[tuple[tuple[float, float], ...], ...],
]:
    """The main source->mouth course and any unjoinable tributary chains."""
    chains = _river_chains(features)
    if not chains:
        return (), ()
    main = list(chains[0])
    tributaries = [tuple(chain) for chain in chains[1:] if len(chain) >= 2]
    # The mouth is the end whose surroundings are mostly sea; a tie keeps the
    # data order (which is source -> mouth for every bundled main course).
    if _sea_score(main[0]) > _sea_score(main[-1]):
        main.reverse()
    main = _extend_to_sea(main)
    return tuple(main), tuple(tributaries)


def _clip_segment(
    first: tuple[float, float],
    second: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Liang-Barsky clip of one lon/lat segment to the map window."""
    west, south, east, north = bounds
    x0, y0 = first
    x1, y1 = second
    dx = x1 - x0
    dy = y1 - y0
    low, high = 0.0, 1.0
    for direction, distance in (
        (-dx, x0 - west),
        (dx, east - x0),
        (-dy, y0 - south),
        (dy, north - y0),
    ):
        if direction == 0.0:
            if distance < 0.0:
                return None
            continue
        ratio = distance / direction
        if direction < 0.0:
            if ratio > high:
                return None
            low = max(low, ratio)
        else:
            if ratio < low:
                return None
            high = min(high, ratio)
    return (
        (x0 + low * dx, y0 + low * dy),
        (x0 + high * dx, y0 + high * dy),
    )


def _clip_polyline(
    points: Sequence[tuple[float, float]],
    bounds: tuple[float, float, float, float],
) -> list[list[tuple[float, float]]]:
    """Every visible run of a polyline inside the map window."""
    pieces: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for first, second in zip(points, points[1:], strict=False):
        clipped = _clip_segment(first, second, bounds)
        if clipped is None:
            if current:
                pieces.append(current)
                current = []
            continue
        start, end = clipped
        if current and _same_point(current[-1], start, 1e-9):
            current.append(end)
        else:
            if current:
                pieces.append(current)
            current = [start, end]
    if current:
        pieces.append(current)
    return [piece for piece in pieces if len(piece) >= 2]


def _river_midpoint(
    course: Sequence[tuple[float, float]], view: View
) -> tuple[float, float] | None:
    """The course point at half its length that is inside the window."""
    cumulative: list[tuple[float, tuple[float, float]]] = []
    total = 0.0
    previous: tuple[float, float] | None = None
    for point in course:
        if previous is not None:
            total += math.hypot(point[0] - previous[0], point[1] - previous[1])
        cumulative.append((total, point))
        previous = point
    if total <= 0.0:
        return None
    target = total / 2.0
    visible = [entry for entry in cumulative if view.contains(*entry[1])]
    if not visible:
        return None
    return min(visible, key=lambda entry: abs(entry[0] - target))[1]


def _clamp_label_point(
    point: tuple[float, float], view: View, *, inset: float = 0.04
) -> tuple[float, float]:
    """Keep a label anchor far enough inside the map frame for its box to fit."""
    x, y = point
    left, top, width, height = view.frame
    margin_x = width * inset
    margin_y = height * inset
    return (
        min(max(x, left + margin_x), left + width - margin_x),
        min(max(y, top + margin_y), top + height - margin_y),
    )


def _zoom_names(value: object) -> list[str]:
    """A ``to:`` name or list of names, each non-empty."""
    if isinstance(value, str):
        names = [value]
    elif isinstance(value, Mapping) or not isinstance(value, Sequence):
        raise MapSceneError("a map zoom 'to' must be a name or a list of names")
    else:
        names = [str(item) for item in value]
    cleaned = [name.strip() for name in names]
    if not cleaned or any(not name for name in cleaned):
        raise MapSceneError("a map zoom 'to' needs at least one non-empty name")
    return cleaned


def _points_bounds(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    """The ``(west, south, east, north)`` union of geographic points."""
    if not points:
        raise MapSceneError("a map zoom target has no geometry")
    lons = [lon for lon, _ in points]
    lats = [lat for _, lat in points]
    return min(lons), min(lats), max(lons), max(lats)


def _named_zoom_bounds(
    name: str,
    markers: Sequence[MapMarker],
) -> tuple[float, float, float, float]:
    """The bounds a ``to:`` name resolves to, in the documented order.

    A scene marker wins over a region, river or country of the same name: the
    zoom is on the place the scene itself already drew. Region, river and
    country lookups reuse :mod:`video_editor_maps.geo`, so their aliases (for
    example ``Ganga`` -> ``Ganges``) work here exactly as they do elsewhere.
    """
    kind, _, plain = name.partition(":")
    if plain and kind.strip().casefold() in {"state", "district"}:
        # ``district:Coimbatore`` frames the shape even when a marker shares the name.
        try:
            admin = find_admin(plain, kind.strip().casefold())
        except GeoError as exc:
            raise MapSceneError(f"map zoom to {name!r}: {exc}") from exc
        points = [point for polygon in admin.polygons for ring in polygon for point in ring]
        return _points_bounds(points)
    wanted = name.casefold()
    for marker in markers:
        if marker.text.casefold() == wanted:
            return marker.lon, marker.lat, marker.lon, marker.lat
    try:
        region = find_region(name)
    except GeoError:
        pass
    else:
        points = [point for polygon in region.polygons for ring in polygon for point in ring]
        return _within_hemisphere(name, _points_bounds(points))
    for admin_kind in ("state", "district"):
        try:
            admin = find_admin(name, admin_kind)
        except GeoError:
            continue
        points = [point for polygon in admin.polygons for ring in polygon for point in ring]
        return _points_bounds(points)
    try:
        features = find_river(name)
    except GeoError:
        pass
    else:
        points = [point for river in features for segment in river.segments for point in segment]
        return _points_bounds(points)
    try:
        country = find_country(name, "50m")
    except GeoError:
        pass
    else:
        return _within_hemisphere(name, country.bounds())
    raise MapSceneError(
        f"map zoom to {name!r}: not a scene marker, region, state, district, river or country"
    )


def _within_hemisphere(
    name: str, bounds: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Fail closed on a feature split at the antimeridian (Russia, Fiji, USA).

    Its bounds span the whole globe, and fitting them would land the camera on
    a wrapped world map instead of the feature.
    """
    if bounds[2] - bounds[0] > 180.0:
        raise MapSceneError(
            f"map zoom to {name!r}: the feature crosses the antimeridian; give a bbox instead"
        )
    return bounds


def _zoom_pad(entry: Mapping[str, Any]) -> float:
    """The ``pad`` fraction of each feature span, defaulting to 0.18."""
    raw = entry.get("pad")
    if raw is None:
        return 0.18
    try:
        pad = float(raw)
    except (TypeError, ValueError):
        raise MapSceneError(f"map zoom pad must be a number, got {raw!r}") from None
    if not math.isfinite(pad) or pad < 0.0:
        raise MapSceneError(f"map zoom pad must be a non-negative number, got {raw!r}")
    return pad


def _padded_bounds(
    entries: Sequence[tuple[float, float, float, float]],
    pad: float,
    *,
    min_span: float = 0.0,
) -> tuple[float, float, float, float]:
    """The union of feature boxes, padded and clamped to the map's latitudes.

    ``min_span`` (degrees) keeps a short subject, such as a brief journey
    leg, from zooming the camera in absurdly far.
    """
    west = min(entry[0] for entry in entries)
    south = min(entry[1] for entry in entries)
    east = max(entry[2] for entry in entries)
    north = max(entry[3] for entry in entries)
    pad_x = max((east - west) * pad, 0.6, (min_span - (east - west)) / 2.0)
    pad_y = max((north - south) * pad, 0.6, (min_span - (north - south)) / 2.0)
    return (
        west - pad_x,
        max(-85.0, south - pad_y),
        east + pad_x,
        min(85.0, north + pad_y),
    )


def _zooms(props: Mapping[str, Any]) -> list[MapZoom]:
    """Camera moves to tighter windows, in order.

    ``zooms`` is a list of ``{bbox, cue?, pad?}`` or ``{to, cue?, pad?}``
    entries. ``bbox`` is ``"west,south,east,north"`` (or a four-item list), the
    window the panel shows once the move lands. ``to`` is a name or list of
    names resolved to a scene marker, region, river or country: their union is
    padded by ``pad`` (default 0.18 of each span, at least 0.6 degrees each
    side) and later fitted to the panel's aspect ratio. Exactly one of
    ``bbox`` and ``to`` is required. The cue defaults to the bounded stagger,
    so a scene can zoom on a rhythm without naming a phrase.
    """
    raw = props.get("zooms")
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise MapSceneError("map zooms must be a list of {bbox, cue} entries")
    zooms: list[MapZoom] = []
    markers: list[MapMarker] | None = None
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise MapSceneError("a map zoom must be a {bbox, cue} mapping")
        has_bbox = entry.get("bbox") is not None
        has_to = entry.get("to") is not None
        if has_bbox == has_to:
            raise MapSceneError("a map zoom needs exactly one of bbox or to")
        cue = declared_cue(entry.get("cue"), "")
        if has_bbox:
            try:
                bounds = parse_bounds(entry.get("bbox"))
            except GeoError as exc:
                raise MapSceneError(f"map zoom bbox {entry.get('bbox')!r}: {exc}") from exc
            if bounds is None:
                raise MapSceneError("a map zoom needs a bbox ('west,south,east,north')")
            zooms.append(MapZoom(bounds=bounds, cue=cue))
            continue
        names = _zoom_names(entry.get("to"))
        if markers is None:
            markers = _markers(props)
        boxes = [_named_zoom_bounds(name, markers) for name in names]
        try:
            min_span = float(entry.get("min_span") or 0.0)
        except (TypeError, ValueError):
            raise MapSceneError(
                f"map zoom min_span must be a number, got {entry.get('min_span')!r}"
            ) from None
        zooms.append(
            MapZoom(
                bounds=_padded_bounds(boxes, _zoom_pad(entry), min_span=min_span),
                cue=cue,
                fit=True,
            )
        )
    return zooms


def _latitude_from_projected(value: float, projection: str) -> float:
    """The latitude whose projected y is ``value``, the inverse of ``_projected_y``."""
    if projection == "mercator":
        return math.degrees(2.0 * math.atan(math.exp(value)) - math.pi / 2.0)
    if projection == "equirectangular":
        return math.degrees(value)
    raise GeoError(f"unknown projection {projection!r}; choose mercator or equirectangular")


def _fitted_bounds(
    bounds: tuple[float, float, float, float],
    frame: Rect4,
    projection: str,
) -> tuple[float, float, float, float]:
    """Grow ``bounds`` in projection space to the frame's aspect, centred on it."""
    west, south, east, north = bounds
    projection = _projection(projection)
    left = math.radians(west)
    right = math.radians(east)
    top = -_projected_y(north, projection)
    bottom = -_projected_y(south, projection)
    centre_lon = (west + east) / 2.0
    centre_lat = (south + north) / 2.0
    centre_x = math.radians(centre_lon)
    centre_y = -_projected_y(centre_lat, projection)
    half_width = max(centre_x - left, right - centre_x)
    half_height = max(centre_y - top, bottom - centre_y)
    aspect = frame[2] / frame[3]
    if half_width < half_height * aspect:
        half_width = half_height * aspect
    else:
        half_height = half_width / aspect
    return (
        math.degrees(centre_x - half_width),
        max(-85.0, _latitude_from_projected(-(centre_y + half_height), projection)),
        math.degrees(centre_x + half_width),
        min(85.0, _latitude_from_projected(-(centre_y - half_height), projection)),
    )


def _zoom_view(
    zoom: MapZoom,
    *,
    scale: float,
    frame: Rect4,
    projection: str,
) -> View:
    """The window a zoom lands on: its bbox fills the plate's map panel.

    The view is built directly rather than through :func:`view_for`, whose
    padding has a one-degree floor: that floor would silently widen a tight
    zoom back out. A ``bbox`` window is used as authored. A ``to`` window is
    fitted by growing its shorter projected side to the panel's aspect ratio,
    so the subject keeps the panel's whole width and height, centred.
    """
    scaled = (frame[0] * scale, frame[1] * scale, frame[2] * scale, frame[3] * scale)
    try:
        bounds = _fitted_bounds(zoom.bounds, frame, projection) if zoom.fit else zoom.bounds
        return View(frame=scaled, bounds=bounds, projection=_projection(projection))
    except GeoError as exc:
        raise MapSceneError(f"map zoom {zoom.bounds!r}: {exc}") from exc


def _projection(name: str) -> str:
    """The projection string, defaulting to the map's own."""
    return name.strip() or "mercator"


def _camera_transform(base: View, target: View) -> tuple[float, float, float]:
    """``(scale, tx, ty)`` mapping base-view plate points to target-view ones.

    Both views fit their bounds into the same frame with one uniform scale, so
    the map from one to the other is a similarity: ``p_target = k p_base + T``.
    Two points shared by both views determine it exactly. Do not compare the
    pixel span of each view's own bounds: a fitted window always fills the
    frame, so those spans are equal and the ratio would read 1.
    """
    west, south, east, north = base.bounds
    lat = (south + north) / 2.0
    base_west = base.project(west, lat)
    base_east = base.project(east, lat)
    target_west = target.project(west, lat)
    target_east = target.project(east, lat)
    base_span = base_east[0] - base_west[0]
    factor = (target_east[0] - target_west[0]) / base_span if base_span else 1.0
    return (
        factor,
        target_west[0] - factor * base_west[0],
        target_west[1] - factor * base_west[1],
    )


def _detail_for(moves: Sequence[tuple[float, float, float, float]]) -> str:
    """The finest bundled geometry the scene's largest camera push needs."""
    largest = max((factor for _, factor, _, _ in moves), default=1.0)
    return "50m" if largest > ZOOM_DETAIL_FACTOR else "110m"


def _bezier_axis(t: float, first: float, second: float) -> float:
    """One axis of a cubic Bezier whose endpoints are 0 and 1."""
    return 3 * (1 - t) ** 2 * t * first + 3 * (1 - t) * t**2 * second + t**3


def _camera_eased(progress: float) -> float:
    """The eased progress of :data:`CAMERA_EASE` at a raw time fraction."""
    x1, y1, x2, y2 = _CAMERA_EASE_POINTS
    low, high = 0.0, 1.0
    for _ in range(32):
        middle = (low + high) / 2.0
        if _bezier_axis(middle, x1, x2) < progress:
            low = middle
        else:
            high = middle
    return _bezier_axis((low + high) / 2.0, y1, y2)


def _camera_css(
    index: int,
    seconds: float,
    moves: Sequence[tuple[float, float, float, float]],
    *,
    move_seconds: float,
) -> str:
    """The camera keyframes; ``moves`` are ``(start, factor, dx, dy)`` in order."""
    if not moves:
        return ""
    frames = ["        0% { transform: none; }"]
    last = "none"
    for start, factor, dx, dy in moves:
        transform = f"translate({dx:.1f}px,{dy:.1f}px) scale({factor:.4f})"
        start_pct = max(0.0, min(seconds, start)) / seconds * 100.0
        end_pct = max(0.0, min(seconds, start + move_seconds)) / seconds * 100.0
        frames.append(
            f"        {start_pct:.3f}% {{ transform: {last}; "
            f"animation-timing-function: {CAMERA_EASE}; }}"
        )
        frames.append(f"        {end_pct:.3f}% {{ transform: {transform}; }}")
        last = transform
    frames.append(f"        100% {{ transform: {last}; }}")
    body = "\n".join(frames)
    return f"      @keyframes map_camera_{index} {{\n{body}\n      }}\n"


def _route_width_css(
    index: int,
    seconds: float,
    moves: Sequence[tuple[float, float, float, float]],
    *,
    move_seconds: float,
    base: float,
) -> str:
    """Compensating route width: ``base / s`` at sampled camera progress.

    The route keeps its dasharray/dashoffset draw-in in user units, so it
    cannot opt out of the camera's scale. The camera interpolates its scale as
    ``1 + (k-1) * eased``, so a single width segment would leave the screen
    width swelling mid-move. The width is instead linear between samples taken
    on the camera's own time fractions, each set to ``base / s`` so the
    on-screen stroke stays at ``base`` before the move, after it lands, and
    throughout it.
    """
    if not moves:
        return ""

    def number(value: float) -> str:
        """A compact number so the base reads ``4.6``, not ``4.6000``."""
        return f"{value:.4f}".rstrip("0").rstrip(".")

    frames = [f"        0% {{ --map-route-width-{index}: {number(base)}; }}"]
    width = base
    previous_factor = 1.0
    for start, factor, _dx, _dy in moves:
        start_pct = max(0.0, min(seconds, start)) / seconds * 100.0
        end_pct = max(0.0, min(seconds, start + move_seconds)) / seconds * 100.0
        frames.append(
            f"        {start_pct:.3f}% {{ --map-route-width-{index}: {number(width)}; "
            "animation-timing-function: linear; }"
        )
        for sample in range(1, _ROUTE_WIDTH_SAMPLES):
            progress = sample / _ROUTE_WIDTH_SAMPLES
            sample_pct = max(0.0, min(seconds, start + progress * move_seconds)) / seconds * 100.0
            scale = previous_factor + (factor - previous_factor) * _camera_eased(progress)
            frames.append(
                f"        {sample_pct:.3f}% {{ --map-route-width-{index}: "
                f"{number(base / scale if scale else base)}; "
                "animation-timing-function: linear; }"
            )
        width = base / factor if factor else base
        frames.append(f"        {end_pct:.3f}% {{ --map-route-width-{index}: {number(width)}; }}")
        previous_factor = factor
    frames.append(f"        100% {{ --map-route-width-{index}: {number(width)}; }}")
    body = "\n".join(frames)
    return f"      @keyframes map_route_width_{index} {{\n{body}\n      }}\n"


def _track_positions(
    x: float, y: float, moves: Sequence[tuple[float, float, float, float]]
) -> list[tuple[float, float]]:
    """Where an anchor at ``(x, y)`` lands after each move, base position first.

    Every move's transform is absolute against the base view (the CSS keyframes
    replace one transform with the next, never compose them), so each landing
    point is computed from the base point, not from the previous landing point.
    """
    points = [(x, y)]
    for _, factor, dx, dy in moves:
        points.append((factor * x + dx, factor * y + dy))
    return points


def _clamp_frame_point(
    point: tuple[float, float],
    frame: tuple[float, float, float, float],
    inset: float = 0.04,
    row: int = 0,
) -> tuple[float, float]:
    """Keep a tracked label point inside the map frame with a small margin."""
    left, top, width, height = frame
    if width <= 0.0 or height <= 0.0:
        return point
    margin_x = width * inset
    margin_y = height * inset + max(0, row) * height * 0.05
    x, y = point
    return (
        min(max(x, left + margin_x), left + width - margin_x),
        min(max(y, top + margin_y), top + height - margin_y),
    )


def _view_starts(
    moves: Sequence[tuple[float, float, float, float]], move_seconds: float
) -> tuple[float, ...]:
    """When each view starts: base at zero, then each landing after its move."""
    if not moves:
        return (0.0,)
    return (0.0, *(move[0] + move_seconds for move in moves))


def _visibility_keyframes(
    name: str,
    seconds: float,
    delay: float,
    reveal_seconds: float,
    view_starts: Sequence[float],
    view_ends: Sequence[float],
    visible: Sequence[bool],
) -> str:
    """Opacity keyframes for a label that appears, hides and returns by view."""
    if seconds <= 0.0:
        return ""
    events: dict[float, float] = {}

    def put(time: float, opacity: float) -> None:
        events[round(max(0.0, min(seconds, time)), 3)] = opacity

    def visible_at(time: float) -> bool:
        for index, (start, end) in enumerate(zip(view_starts, view_ends, strict=True)):
            if start <= time < end:
                return visible[index]
        return visible[-1]

    put(0.0, 0.0)
    reveal_end = min(seconds, delay + reveal_seconds)
    put(delay, 0.0)
    put(reveal_end, 1.0 if visible_at(reveal_end) else 0.0)
    fade = min(0.3, max(0.1, seconds * 0.06))
    for index, boundary in enumerate(view_starts[1:], start=1):
        if index >= len(visible) or visible[index] == visible[index - 1]:
            continue
        put(max(0.0, boundary - fade), 1.0 if visible[index - 1] else 0.0)
        put(boundary, 1.0 if visible[index] else 0.0)
    put(seconds, 1.0 if visible[-1] else 0.0)
    frames = [
        f"        {time / seconds * 100.0:.3f}% {{ opacity:{events[time]:g}; }}"
        for time in sorted(events)
    ]
    body = "\n".join(frames)
    return f"      @keyframes {name} {{\n{body}\n      }}\n"


def _track_css(
    index: int,
    seconds: float,
    moves: Sequence[tuple[float, float, float, float]],
    *,
    move_seconds: float,
    tracks: Sequence[_Track],
    frame: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    view_ends: Sequence[float] = (),
    view_starts: Sequence[float] = (),
    stutter: float | None = None,
    fps: int = DEFAULT_FPS,
) -> str:
    """Reveal rules for every anchor, plus position keyframes when the camera moves.

    A track animates ``left``/``top`` on the same percentages and with the same
    easing as the camera. Because the camera transform is a uniform scale plus
    a translation, linear interpolation of the transformed point is exact, so
    a label stays glued to its pin while its type size does not change.

    ``stutter`` steps only the reveal animation; the ``left``/``top`` keyframes
    stay linear so a tracked anchor keeps following a moving camera smoothly.
    """
    if not tracks:
        return ""
    blocks: list[str] = []
    rules: list[str] = []
    drop_keyframes = ""
    for track in tracks:
        ease = (
            track.ease
            if stutter is None
            else _steps_timing(track.seconds, stutter=stutter, fps=fps)
        )
        animation = f"{track.reveal} {track.seconds:.3f}s {ease} {track.delay:.3f}s both"
        if track.visible is not None:
            visibility_name = f"map_label_visible_{index}_{track.name}"
            blocks.append(
                _visibility_keyframes(
                    visibility_name,
                    seconds,
                    track.delay,
                    track.seconds,
                    view_starts or (0.0,),
                    view_ends or (seconds,),
                    track.visible,
                )
            )
            animation = f"{visibility_name} {seconds:.3f}s linear both"
        if moves:
            if track.points is not None:
                raw_points = list(track.points)
                first = next(
                    (point for point in raw_points if point is not None),
                    (track.x, track.y),
                )
                points = []
                last = first
                for point in raw_points:
                    if point is not None:
                        last = point
                    points.append(last)
                while len(points) < len(moves) + 1:
                    points.append(last)
            else:
                points = _track_positions(track.x, track.y, moves)
                if track.clamp:
                    points = [_clamp_frame_point(point, frame, row=track.row) for point in points]
            frames = [f"        0% {{ left:{points[0][0]:.1f}px; top:{points[0][1]:.1f}px; }}"]
            for position, (start, *_rest) in enumerate(moves):
                start_pct = max(0.0, min(seconds, start)) / seconds * 100.0
                end_pct = max(0.0, min(seconds, start + move_seconds)) / seconds * 100.0
                here = points[position]
                landing = points[position + 1]
                frames.append(
                    f"        {start_pct:.3f}% {{ left:{here[0]:.1f}px; top:{here[1]:.1f}px; "
                    "animation-timing-function: cubic-bezier(.4,0,.2,1); }"
                )
                frames.append(
                    f"        {end_pct:.3f}% {{ left:{landing[0]:.1f}px; top:{landing[1]:.1f}px; }}"
                )
            last = points[-1]
            frames.append(f"        100% {{ left:{last[0]:.1f}px; top:{last[1]:.1f}px; }}")
            body = "\n".join(frames)
            blocks.append(f"      @keyframes map_track_{index}_{track.name} {{\n{body}\n      }}\n")
            animation += f", map_track_{index}_{track.name} {seconds:.3f}s linear both"
        rules.append(
            f'      .map-{index} [data-camera-track="{track.name}"] {{ animation: {animation}; }}\n'
        )
        if track.drop:
            # Only the pin drops; its label keeps the plain fade so the label's
            # own centring transform is not overwritten.
            drop_animation = animation.replace(track.reveal, f"map_marker_drop_{index}", 1)
            rules.append(
                f'      .map-{index} .map-marker[data-camera-track="{track.name}"] '
                f"{{ animation: {drop_animation}; }}\n"
            )
            if not drop_keyframes:
                drop_keyframes = f"""      @keyframes map_marker_drop_{index} {{
        0% {{ opacity:0; transform:translateY(-40px); }}
        70% {{ opacity:1; transform:translateY(4px); }}
        100% {{ opacity:1; transform:translateY(0); }}
      }}
"""
    return drop_keyframes + "".join(blocks) + "".join(rules)


def _subject(props: Mapping[str, Any]) -> Country:
    query = _text(props, "country")
    if not query:
        raise MapSceneError("a map scene needs a country")
    try:
        return find_country(query, worldview=_worldview(props))
    except GeoError as exc:
        raise MapSceneError(str(exc)) from exc


def _view(subject: Country, props: Mapping[str, Any], scale: float, frame: Rect4) -> View:
    """The geographic window fitted to the plate's map panel at ``scale``."""
    scaled = (
        frame[0] * scale,
        frame[1] * scale,
        frame[2] * scale,
        frame[3] * scale,
    )
    try:
        declared = parse_bounds(props.get("bbox"))
        projection = _text(props, "projection") or "mercator"
        return view_for(
            subject,
            scaled,
            padding=MAP_PADDING,
            bounds=declared,
            projection=projection,
        )
    except GeoError as exc:
        raise MapSceneError(str(exc)) from exc


def _neighbours(
    view: View, subject: Country, *, detail: str = "110m", worldview: str = ""
) -> list[Country]:
    """Countries that can draw something inside the window."""
    west, south, east, north = view.bounds
    near: list[Country] = []
    for country in countries(detail, worldview):
        if country.name == subject.name or country.region == "Antarctica":
            continue
        try:
            box = country.bounds()
        except GeoError:
            continue
        if box[2] < west or box[0] > east or box[3] < south or box[1] > north:
            continue
        near.append(country)
    return near


def _region_pattern(style: str, index: int, colors: Mapping[str, str], scale: float) -> str:
    """One SVG tile pattern for a region style, in plate units."""
    if style == "range":
        size = 14.0 * scale
        return (
            f'<pattern id="map-region-range-{index}" width="{size:g}" height="{size:g}" '
            'patternUnits="userSpaceOnUse">'
            f'<path d="M0 {12.0 * scale:g} L{7.0 * scale:g} {3.0 * scale:g} '
            f'L{14.0 * scale:g} {12.0 * scale:g}" fill="none" '
            f'stroke="{colors["ink"]}" stroke-width="{1.1 * scale:g}" '
            f'opacity="{REGION_RIDGE_OPACITY:g}"/></pattern>'
        )
    if style == "relief":
        # Soft staggered hills (#349): rounded humps in offset rows with a
        # light shadow fill read as terrain rather than a zig-zag hatch.
        w, h = 18.0 * scale, 16.0 * scale
        humps = (
            f"M{1.0 * scale:g} {8.0 * scale:g} Q{5.0 * scale:g} {1.5 * scale:g} "
            f"{9.0 * scale:g} {8.0 * scale:g}",
            f"M{10.0 * scale:g} {16.0 * scale:g} Q{14.0 * scale:g} {9.5 * scale:g} "
            f"{18.0 * scale:g} {16.0 * scale:g}",
        )
        shapes = "".join(
            f'<path d="{hump}" fill="{colors["ink"]}" fill-opacity="0.10" '
            f'stroke="{colors["ink"]}" stroke-width="{1.0 * scale:g}" '
            f'stroke-linecap="round" opacity="{REGION_RELIEF_OPACITY:g}"/>'
            for hump in humps
        )
        return (
            f'<pattern id="map-region-relief-{index}" width="{w:g}" height="{h:g}" '
            f'patternUnits="userSpaceOnUse">{shapes}</pattern>'
        )
    if style == "desert":
        size = 10.0 * scale
        return (
            f'<pattern id="map-region-desert-{index}" width="{size:g}" height="{size:g}" '
            'patternUnits="userSpaceOnUse">'
            f'<circle cx="{3.0 * scale:g}" cy="{3.0 * scale:g}" r="{1.3 * scale:g}" '
            f'fill="{REGION_SAND}" opacity="0.55"/>'
            f'<circle cx="{7.5 * scale:g}" cy="{7.5 * scale:g}" r="{0.9 * scale:g}" '
            f'fill="{REGION_SAND}" opacity="0.45"/></pattern>'
        )
    if style == "plateau":
        size = 10.0 * scale
        return (
            f'<pattern id="map-region-plateau-{index}" width="{size:g}" height="{size:g}" '
            'patternUnits="userSpaceOnUse">'
            f'<path d="M0 {10.0 * scale:g} L{10.0 * scale:g} 0" fill="none" '
            f'stroke="{colors["ink"]}" stroke-width="{1.0 * scale:g}" '
            f'opacity="{REGION_HATCH_OPACITY:g}"/></pattern>'
        )
    if style == "delta":
        size = 10.0 * scale
        return (
            f'<pattern id="map-region-delta-{index}" width="{size:g}" height="{size:g}" '
            'patternUnits="userSpaceOnUse">'
            f'<rect width="{size:g}" height="{size:g}" fill="{REGION_TINT}" opacity="0.12"/>'
            f'<path d="M0 {4.0 * scale:g} H{10.0 * scale:g} '
            f'M0 {8.0 * scale:g} H{10.0 * scale:g}" fill="none" '
            f'stroke="{REGION_TINT}" stroke-width="{0.9 * scale:g}" opacity="0.35"/>'
            "</pattern>"
        )
    return ""


def _map_svg(
    subject: Country,
    view: View,
    colors: Mapping[str, str],
    *,
    width: float,
    height: float,
    scale: float,
    route: _ProjectedRoute | None = None,
    rivers: Sequence[_PreparedRiver] = (),
    regions: Sequence[_PreparedRegion] = (),
    index: int,
    detail: str = "110m",
    route_dot: bool = False,
    worldview: str = "",
    extra_svg: str = "",
) -> str:
    """The projected world, clipped to the map panel.

    Every stroked path except the route opts out of the camera's scale so its
    screen width is constant; the route compensates with animated width. A
    closely zoomed scene passes ``detail="50m"`` for its outlines.
    """
    meridians, parallels = graticule_lines(view)
    x, y, w, h = view.frame
    clip = f"map-clip-{index}"
    pattern_styles = sorted({region.style for region in regions if region.style in _PATTERN_STYLES})
    pattern_defs = "".join(_region_pattern(style, index, colors, scale) for style in pattern_styles)
    parts = [
        f'<svg class="map-svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" aria-hidden="true">',
        f'<defs><clipPath id="{clip}"><rect x="{x:.1f}" y="{y:.1f}" '
        f'width="{w:.1f}" height="{h:.1f}"/></clipPath>{pattern_defs}</defs>',
    ]
    strokes = "".join(
        f'<path d="{line}" fill="none" stroke="{colors["graticule"]}" '
        'stroke-width="1" vector-effect="non-scaling-stroke" opacity="0.22"/>'
        for line in (*meridians, *parallels)
    )
    if strokes:
        parts.append(f'<g clip-path="url(#{clip})">{strokes}</g>')
    ghosts = "".join(
        f'<path d="{path}" fill="{colors["ghost_fill"]}" opacity="0.92" '
        f'stroke="{colors["ghost_stroke"]}" stroke-width="1.2" '
        'vector-effect="non-scaling-stroke"/>'
        for country in _neighbours(view, subject, detail=detail, worldview=worldview)
        for path in country_paths(country, view)
    )
    if ghosts:
        parts.append(f'<g class="map-ghost" clip-path="url(#{clip})">{ghosts}</g>')
    drawing = (
        find_country(subject.name, detail=detail, worldview=worldview)
        if detail != "110m"
        else subject
    )
    subject_paths = "".join(
        f'<path d="{path}" fill="{colors["highlight"]}" '
        f'stroke="{colors["highlight_stroke"]}" stroke-width="1.4" '
        'vector-effect="non-scaling-stroke" stroke-linejoin="round"/>'
        for path in country_paths(drawing, view)
    )
    parts.append(f'<g class="map-subject" clip-path="url(#{clip})">{subject_paths}</g>')
    if regions:
        region_fills: list[str] = []
        region_outlines: list[str] = []
        for region_position, region in enumerate(regions):
            fill_var = f"--map-region-fill-{index}-{region_position}"
            outline_color = REGION_OUTLINE_COLOR
            outline_opacity = REGION_OUTLINE_OPACITY
            if region.style in _PATTERN_STYLES:
                fill_attr = f'fill="url(#map-region-{region.style}-{index})"'
            elif region.style == "fill":
                fill_attr = (
                    f'fill="{html.escape(region.color or colors["highlight"], quote=True)}" '
                    f'fill-opacity="{REGION_FILL_OPACITY:g}"'
                )
                outline_color = colors.get("route") or colors["pin"]
                outline_opacity = 1.0
            elif region.style == "photo":
                fill_attr = 'fill="none"'
                outline_color = colors.get("route") or colors["pin"]
                outline_opacity = 1.0
                clip_id = f"map-region-photo-{index}-{region_position}"
                x0, y0, x1, y1 = region.box
                region_fills.append(
                    f'<clipPath id="{clip_id}">'
                    + "".join(f'<path d="{shape.path}"/>' for shape in region.shapes)
                    + "</clipPath>"
                    f'<image class="map-region-photo" href="{region.image}" '
                    f'x="{x0:.1f}" y="{y0:.1f}" width="{max(1.0, x1 - x0):.1f}" '
                    f'height="{max(1.0, y1 - y0):.1f}" preserveAspectRatio="xMidYMid slice" '
                    f'clip-path="url(#{clip_id})" style="opacity:var({fill_var}, 0)"/>'
                )
            else:
                fill_attr = f'fill="{REGION_TINT}" fill-opacity="{REGION_TINT_OPACITY:g}"'
            for polygon_position, shape in enumerate(region.shapes):
                draw_var = f"--map-region-draw-{index}-{region_position}-{polygon_position}"
                region_fills.append(
                    f'<path class="map-region-fill" d="{shape.path}" {fill_attr} '
                    f'style="opacity:var({fill_var}, 0)"/>'
                )
                region_outlines.append(
                    f'<path class="map-region-outline" d="{shape.path}" fill="none" '
                    f'stroke="{outline_color}" '
                    f'stroke-opacity="{outline_opacity:g}" '
                    f'stroke-width="{REGION_OUTLINE_WIDTH * scale:g}" '
                    f'vector-effect="non-scaling-stroke" stroke-linejoin="round" '
                    f'stroke-dasharray="{shape.length:.1f}" '
                    f'stroke-dashoffset="{shape.length:.1f}" '
                    f'style="stroke-dashoffset:var({draw_var}, {shape.length:.1f})"/>'
                )
        parts.append(
            f'<g class="map-regions" clip-path="url(#{clip})">'
            f"{''.join(region_fills)}{''.join(region_outlines)}</g>"
        )
    if rivers:
        river_paths: list[str] = []
        for river_position, river in enumerate(rivers):
            for piece_position, piece in enumerate(river.pieces):
                width = RIVER_STROKE if piece.main else RIVER_TRIBUTARY_STROKE
                draw_var = f"--map-river-draw-{index}-{river_position}-{piece_position}"
                draw_style = f"stroke-dashoffset:var({draw_var}, {piece.length:.1f})"
                classes = "map-river" if piece.main else "map-river map-river-tributary"
                river_paths.append(
                    f'<path class="map-river-casing" d="{piece.path}" fill="none" '
                    f'stroke="{colors["ink"]}" '
                    f'stroke-width="{width * RIVER_CASING_SCALE * scale:g}" '
                    f'stroke-opacity="{RIVER_CASING_OPACITY:g}" '
                    f'stroke-linecap="round" stroke-linejoin="round" '
                    f'vector-effect="non-scaling-stroke" '
                    f'stroke-dasharray="{piece.length:.1f}" '
                    f'stroke-dashoffset="{piece.length:.1f}" '
                    f'style="{draw_style}"/>'
                )
                river_paths.append(
                    f'<path class="{classes}" d="{piece.path}" fill="none" '
                    f'stroke="{colors["river"]}" stroke-width="{width * scale:g}" '
                    f'stroke-linecap="round" stroke-linejoin="round" '
                    f'vector-effect="non-scaling-stroke" '
                    f'stroke-dasharray="{piece.length:.1f}" '
                    f'stroke-dashoffset="{piece.length:.1f}" '
                    f'style="{draw_style}"/>'
                )
                if river.flow:
                    flow_var = f"--map-river-flow-{index}-{river_position}-{piece_position}"
                    on_var = f"--map-river-flow-on-{index}-{river_position}-{piece_position}"
                    river_paths.append(
                        f'<path class="map-river-flow" d="{piece.path}" fill="none" '
                        f'stroke="{RIVER_FLOW_COLOR}" '
                        f'stroke-width="{RIVER_FLOW_STROKE * scale:g}" '
                        f'stroke-linecap="round" stroke-linejoin="round" '
                        f'stroke-opacity="{RIVER_FLOW_OPACITY:g}" '
                        f'vector-effect="non-scaling-stroke" '
                        f'stroke-dasharray="{RIVER_FLOW_DASH * scale:g} '
                        f'{RIVER_FLOW_GAP * scale:g}" '
                        f'style="stroke-dashoffset:var({flow_var}, 0); '
                        f'opacity:var({on_var}, 0)"/>'
                    )
        parts.append(f'<g class="map-rivers" clip-path="url(#{clip})">{"".join(river_paths)}</g>')
    if route is not None:
        # The dash is exactly as long as the path, so an offset of the same
        # length hides it and an offset of zero draws all of it. The offset is
        # driven by a custom property animated on the HTML wrapper: HyperFrames
        # seeks CSS animations on HTML elements only, never on SVG paths.
        parts.append(
            f'<path class="map-route" d="{route.path}" fill="none" '
            f'stroke="{colors["pin"]}" stroke-width="{ROUTE_STROKE * scale:g}" '
            f'opacity="0.85" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'stroke-dasharray="{route.length:.1f}" '
            f'stroke-dashoffset="{route.length:.1f}"/>'
        )
        if route_dot:
            # A small travelling head drawn in the plate's own units, so it
            # scales with the plate rather than the camera. Its position and
            # opacity are driven by the HTML wrapper too.
            parts.append(
                f'<circle class="map-route-dot" cx="0" cy="0" '
                f'r="{5.0 * scale:.1f}" fill="{colors["pin"]}"/>'
            )
    if extra_svg:
        parts.append(extra_svg)
    parts.append("</svg>")
    return "".join(parts)


def _km_label_css(index: int, colors: Mapping[str, str], scale: float) -> str:
    """The boxed monospace counter that rides a journey leg."""
    return (
        f"      .map-{index} .map-km {{ position:absolute; transform:translate(-50%, -150%);\n"
        "        opacity:0; }\n"
        f"      .map-{index} .map-km-box {{ display:inline-block;\n"
        f"        padding:{4 * scale:.1f}px {9 * scale:.1f}px; border-radius:{4 * scale:.1f}px;\n"
        f"        background:{colors.get('km_bg', 'rgba(10, 30, 28, 0.86)')};\n"
        f"        border:{max(1.0, 1.2 * scale):.1f}px solid {colors.get('route') or colors['pin']};\n"
        f"        color:{colors.get('km_ink', '#e8fff4')}; font: 600 {17 * scale:.1f}px/1.1 "
        "monospace; letter-spacing:0.08em;\n"
        "        white-space:nowrap; }\n"
        f"      .map-{index} .map-km-count {{ display:inline-grid; }}\n"
        f"      .map-{index} .map-km-step {{ grid-area:1 / 1; opacity:0; }}\n"
    )


def _marker_pins(markers: Sequence[_ProjectedMarker]) -> str:
    """Three-part pins drawn in the unscaled overlay, so a zoom cannot swell them."""
    parts: list[str] = []
    for position, marker in enumerate(markers):
        parts.append(
            f'<div class="map-marker" data-camera-track="marker_{position}" '
            f'style="left:{marker.x:.1f}px;top:{marker.y:.1f}px">'
            '<span class="map-marker-halo"></span>'
            '<span class="map-marker-ring"></span>'
            '<span class="map-marker-dot"></span>'
            "</div>"
        )
    return "".join(parts)


def _focus_pin(pin: tuple[float, float] | None) -> str:
    """The fallback centre pin, drawn in the unscaled overlay like a marker."""
    if pin is None:
        return ""
    return (
        f'<div class="map-pin-point" data-camera-track="focus" '
        f'style="left:{pin[0]:.1f}px;top:{pin[1]:.1f}px">'
        '<span class="map-pin-halo"></span>'
        '<span class="map-pin-ring"></span>'
        '<span class="map-pin-dot"></span>'
        "</div>"
    )


def _label_side(pin: tuple[float, float] | None, view: View) -> str:
    """Which side of the pin the place label sits on."""
    if pin is None:
        return "right"
    return "left" if pin[0] > view.frame[0] + view.frame[2] * 0.66 else "right"


def _badge(props: Mapping[str, Any]) -> str:
    name = _text(props, "badge")
    if not name:
        return ""
    sub = _text(props, "badge_sub")
    monogram = html.escape(_text(props, "badge_mark") or name[:1].upper())
    sub_markup = f'<div class="map-badge-sub">{html.escape(sub)}</div>' if sub else ""
    return (
        '<div class="map-badge">'
        f'<div class="map-badge-disc"><span>{monogram}</span></div>'
        f'<div class="map-badge-text"><div class="map-badge-name">{html.escape(name)}</div>'
        f"{sub_markup}</div>"
        "</div>"
    )


def _title(props: Mapping[str, Any]) -> str:
    eyebrow = _text(props, "eyebrow")
    headline = _text(props, "headline")
    subtitle = _text(props, "subtitle")
    if not (eyebrow or headline or subtitle):
        return ""
    parts = ['<div class="map-title"><div class="map-title-rule"></div>']
    if eyebrow:
        parts.append(f'<div class="map-eyebrow">{html.escape(eyebrow)}</div>')
    if headline:
        parts.append(f'<div class="map-headline">{html.escape(headline)}</div>')
    if subtitle:
        parts.append(f'<div class="map-subtitle">{html.escape(subtitle)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _cards(callouts: Sequence[MapCallout]) -> str:
    if not callouts:
        return ""
    cards = "".join(
        '<div class="map-card">'
        f'<span class="map-card-num">{position:02d}</span>'
        '<span class="map-card-rule"></span>'
        f'<span class="map-card-text">{html.escape(callout.text)}</span>'
        "</div>"
        for position, callout in enumerate(callouts, start=1)
    )
    return f'<div class="map-cards">{cards}</div>'


def map_scene_cues(scene: Mapping[str, Any]) -> dict[str, str]:
    """``{item_N|marker_N|route|zoom_N: phrase}`` for every cued reveal."""
    props = dict(scene.get("props") or {})
    cues = {
        f"item_{position}": callout.cue
        for position, callout in enumerate(_callouts(props))
        if callout.cue
    }
    cues.update(
        {
            f"marker_{position}": marker.cue
            for position, marker in enumerate(_markers(props))
            if marker.cue
        }
    )
    cues.update(
        {
            f"river_{position}": river.cue
            for position, river in enumerate(_rivers(props))
            if river.cue
        }
    )
    cues.update(
        {
            f"region_{position}": region.cue
            for position, region in enumerate(_regions(props))
            if region.cue
        }
    )
    route = _route(props)
    if route is not None and route.cue:
        cues["route"] = route.cue
    cues.update(
        {f"zoom_{position}": zoom.cue for position, zoom in enumerate(_zooms(props)) if zoom.cue}
    )
    return cues


def silence_unspoken_map_cues(
    props: Mapping[str, Any], words: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """``props`` with every map cue the narration never says switched off.

    A callout or marker's text is its cue by default; display copy the voice
    never says is better read as a label on the plate's stagger than as a cue
    that cannot resolve.
    """
    out = dict(props)

    callouts = props.get("callouts")
    if isinstance(callouts, Sequence) and not isinstance(callouts, str):
        silenced_callouts: list[Any] = []
        for entry in callouts:
            if isinstance(entry, str):
                silenced_callouts.append(
                    {"text": entry, "cue": False} if find_cue(words, entry) is None else entry
                )
            elif isinstance(entry, Mapping):
                silenced_callouts.append(_silenced_entry(entry, words))
            else:
                silenced_callouts.append(entry)
        out["callouts"] = silenced_callouts

    markers = props.get("markers")
    if isinstance(markers, Sequence) and not isinstance(markers, str):
        out["markers"] = [
            _silenced_entry(entry, words) if isinstance(entry, Mapping) else entry
            for entry in markers
        ]

    rivers = props.get("rivers")
    if isinstance(rivers, Sequence) and not isinstance(rivers, str):
        out["rivers"] = [
            _silenced_river(entry, words) if isinstance(entry, Mapping) else entry
            for entry in rivers
        ]

    regions = props.get("regions")
    if isinstance(regions, Sequence) and not isinstance(regions, str):
        out["regions"] = [
            _silenced_river(entry, words) if isinstance(entry, Mapping) else entry
            for entry in regions
        ]

    route_cue = props.get("route_cue")
    if isinstance(route_cue, str) and route_cue.strip() and find_cue(words, route_cue) is None:
        out["route_cue"] = False

    zooms = props.get("zooms")
    if isinstance(zooms, Sequence) and not isinstance(zooms, str):
        silenced_zooms: list[Any] = []
        for entry in zooms:
            if isinstance(entry, Mapping):
                entry = dict(entry)
                cue = entry.get("cue")
                if isinstance(cue, str) and cue.strip() and find_cue(words, cue) is None:
                    entry["cue"] = False
            silenced_zooms.append(entry)
        out["zooms"] = silenced_zooms

    return out


def _silenced_entry(entry: Mapping[str, Any], words: Sequence[Mapping[str, Any]]) -> Any:
    """A marker/callout entry whose default cue the narration never says."""
    entry = dict(entry)
    text = str(entry.get("text") or "")
    if text.strip() and find_cue(words, declared_cue(entry.get("cue"), text)) is None:
        entry["cue"] = False
    return entry


def _silenced_river(entry: Mapping[str, Any], words: Sequence[Mapping[str, Any]]) -> Any:
    """A river entry whose name the narration never says."""
    entry = dict(entry)
    name = str(entry.get("name") or "")
    if name.strip() and find_cue(words, declared_cue(entry.get("cue"), name)) is None:
        entry["cue"] = False
    return entry


def _camera_schedule(
    view: View,
    zoom_delays: Sequence[float],
    zooms: Sequence[MapZoom],
    *,
    scale: float,
    frame: Rect4,
    projection: str,
    seconds: float,
    move_seconds: float,
) -> tuple[
    list[tuple[float, float, float, float]],
    tuple[_DroppedZoom, ...],
    list[View],
]:
    """Fit each zoom before the cut, recording every one that cannot play.

    The original loop ``break`` stops at the first move that cannot land. This
    keeps that behaviour (a later zoom cannot jump ahead of a broken order) but
    walks the rest so every skipped zoom is recorded, not just the first. The
    returned views are the landing windows, aligned with ``camera_moves``.
    """
    camera_moves: list[tuple[float, float, float, float]] = []
    camera_views: list[View] = []
    dropped: list[_DroppedZoom] = []
    previous_end = 0.0
    stopped = False
    for position, (delay, zoom) in enumerate(zip(zoom_delays, zooms, strict=True)):
        delay = max(delay, previous_end + 0.25)
        available = max(0.0, seconds - delay)
        if stopped or delay > seconds - move_seconds:
            dropped.append(_DroppedZoom(index=position, needed=move_seconds, available=available))
            stopped = True
            continue
        target = _zoom_view(zoom, scale=scale, frame=frame, projection=projection)
        factor, dx, dy = _camera_transform(view, target)
        camera_moves.append((delay, factor, dx, dy))
        camera_views.append(target)
        previous_end = delay + move_seconds
    return camera_moves, tuple(dropped), camera_views


@dataclass(frozen=True)
class _Prepared:
    """The scene's geometry and reveal schedule, before any HTML is written.

    Shared by :func:`render_map` and the pure-geometry QA helpers so a gate
    reads exactly the moves, pins and labels the renderer will draw.
    """

    props: Mapping[str, Any]
    seconds: float
    canvas_w: float
    canvas_h: float
    geom: _Geom
    scale: float
    plate_w: float
    plate_h: float
    plate_x: float
    plate_y: float
    entry: float
    colors: Mapping[str, str]
    subject: Country
    view: View
    focus: tuple[float, float]
    pin: tuple[float, float] | None
    label: str
    callouts: list[MapCallout]
    projected: list[_ProjectedMarker]
    drawn_route: _ProjectedRoute | None
    rivers: list[_PreparedRiver]
    river_labels: list[_ProjectedMarker]
    regions: list[_PreparedRegion]
    region_labels: list[_ProjectedMarker]
    callout_delays: list[float]
    move_seconds: float
    camera_moves: list[tuple[float, float, float, float]]
    camera_views: list[View]
    dropped_zooms: tuple[_DroppedZoom, ...]
    #: Journey legs (empty without a ``journey``) and how they are dressed.
    legs: tuple[ProjectedLeg, ...] = ()
    journey_icon: str = "none"
    journey_km: bool = False
    #: Speech bubbles and stickers: (text or sticker, plate x, plate y, delay).
    bubbles: tuple[tuple[Any, float, float, float], ...] = ()
    stickers: tuple[tuple[Any, float, float, float], ...] = ()


def _prepare(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> _Prepared:
    """Resolve a map scene's plate, markers and camera schedule."""
    props = dict(scene.get("props") or {})
    seconds = _seconds(duration)
    canvas_w = float(width) if width > 0 else float(DESIGN_WIDTH)
    canvas_h = float(height) if height and height > 0 else canvas_w * 9.0 / 16.0
    # One uniform scale keeps the plate's proportions on any canvas; the
    # remainder is paper, not a stretched layout. Portrait frames take the
    # portrait plate, which reflows the zones instead of shrinking 16:9.
    geom = _geom_for(canvas_w, canvas_h, full=_text(props, "frame").lower() == "full")
    scale = min(canvas_w / geom.width, canvas_h / geom.height)
    plate_w = geom.width * scale
    plate_h = geom.height * scale
    plate_x = (canvas_w - plate_w) / 2.0
    plate_y = (canvas_h - plate_h) / 2.0
    entry = _entry_seconds(design)
    colors = _colors(design, props)

    subject = _subject(props)
    view = _view(subject, props, scale, geom.map_frame)

    try:
        declared_focus = parse_position(props.get("focus"))
    except GeoError as exc:
        raise MapSceneError(str(exc)) from exc
    focus = declared_focus
    if focus is None:
        focus = (
            (view.bounds[0] + view.bounds[2]) / 2.0,
            (view.bounds[1] + view.bounds[3]) / 2.0,
        )
    pin = view.project(*focus) if view.contains(*focus) else None
    label = _text(props, "label")

    try:
        journey = parse_journey(props, river_course=_journey_river_course)
    except JourneyError as exc:
        raise MapSceneError(str(exc)) from exc
    if journey is not None:
        # Stops are ordinary markers and ``follow`` is ordinary ``to:`` zooms,
        # so labels, pins and the camera need no journey-specific code.
        props["markers"] = [*(props.get("markers") or []), *journey_markers(journey)]
        props["zooms"] = [*(props.get("zooms") or []), *journey_zooms(journey)]

    callouts = _callouts(props)
    markers = _markers(props)
    route = _route(props)
    map_rivers = _rivers(props)
    map_regions = _regions(props)
    zooms = _zooms(props)
    # A map that names places does not need the fallback focus pin. Markers,
    # rivers and regions all name places, so any of them drops it unless the
    # scene asks for the pin with ``focus_pin``.
    if (markers or map_rivers or map_regions) and not _flag(props, "focus_pin"):
        pin = None

    cues = {f"item_{position}": callout.cue for position, callout in enumerate(callouts)}
    cues.update({f"marker_{position}": marker.cue for position, marker in enumerate(markers)})
    cues.update(
        {f"river_{position}": river.cue for position, river in enumerate(map_rivers) if river.cue}
    )
    cues.update(
        {
            f"region_{position}": region.cue
            for position, region in enumerate(map_regions)
            if region.cue
        }
    )
    if route is not None and route.cue:
        cues["route"] = route.cue
    cues.update({f"zoom_{position}": zoom.cue for position, zoom in enumerate(zooms) if zoom.cue})
    if journey is not None:
        cues.update({f"leg_{position}": leg.cue for position, leg in enumerate(journey.legs)})
    places = {marker.text.casefold(): (marker.lon, marker.lat) for marker in markers}
    try:
        overlay_bubbles, overlay_stickers = parse_overlays(props, places)
    except StickerError as exc:
        raise MapSceneError(str(exc)) from exc
    cues.update({f"bubble_{i}": b.cue for i, b in enumerate(overlay_bubbles) if b.cue})
    cues.update({f"sticker_{i}": k.cue for i, k in enumerate(overlay_stickers) if k.cue})

    # Reveals: the word that earns each item, else a bounded stagger.
    times: Mapping[str, float] = (
        resolve_cues(words or [], cues, lead=0.0) if (words and cues) else {}
    )
    latest = max(0.0, seconds - entry)
    callout_stagger = min(0.55, max(0.10, (seconds - entry) / max(len(callouts), 1) / 2.4))
    callout_delays = _reveal_delays(
        times, "item", len(callouts), entry * 1.2, callout_stagger, latest
    )
    # Places are the point of the map, so they land before the cards do.
    marker_stagger = min(0.45, max(0.08, (seconds - entry) / max(len(markers), 1) / 3.4))
    marker_delays = _reveal_delays(
        times, "marker", len(markers), entry * 0.9, marker_stagger, latest
    )

    projected = _project_markers(markers, view, marker_delays)
    drawn_route = _draw_route(
        route,
        view,
        times,
        latest,
        entry,
        curve=_route_curve(props),
        window=parse_bounds(props.get("bbox")),
    )

    # Camera moves: each zoom's cue (or the bounded stagger), then clamped so
    # the moves cannot overlap and the last one lands before the cut.
    move_seconds = max(0.45, entry * 2.4)
    zoom_stagger = min(1.6, max(0.6, (seconds - entry) / max(len(zooms), 1) / 2.0))
    zoom_latest = max(0.0, seconds - move_seconds - 0.35)
    zoom_delays = _reveal_delays(times, "zoom", len(zooms), entry * 1.4, zoom_stagger, zoom_latest)
    projection = _text(props, "projection") or "mercator"
    camera_moves, dropped_zooms, camera_views = _camera_schedule(
        view,
        zoom_delays,
        zooms,
        scale=scale,
        frame=geom.map_frame,
        projection=projection,
        seconds=seconds,
        move_seconds=move_seconds,
    )
    legs: tuple[ProjectedLeg, ...] = ()
    if journey is not None:
        leg_stagger = min(2.0, max(0.6, (seconds - entry) / max(len(journey.legs), 1) / 1.6))
        leg_delays = _reveal_delays(
            times, "leg", len(journey.legs), entry * 1.2, leg_stagger, latest
        )
        legs = tuple(project_legs(journey, view.project, delays=leg_delays, seconds=seconds))
    overlay_stagger = min(0.6, max(0.15, (seconds - entry) / 6.0))
    bubble_delays = _reveal_delays(
        times, "bubble", len(overlay_bubbles), entry * 1.5, overlay_stagger, latest
    )
    sticker_delays = _reveal_delays(
        times, "sticker", len(overlay_stickers), entry * 1.8, overlay_stagger, latest
    )
    placed_bubbles = tuple(
        (bubble, *view.project(bubble.lon, bubble.lat), delay)
        for bubble, delay in zip(overlay_bubbles, bubble_delays, strict=True)
    )
    placed_stickers = tuple(
        (sticker, *view.project(sticker.lon, sticker.lat), delay)
        for sticker, delay in zip(overlay_stickers, sticker_delays, strict=True)
    )
    drawn_rivers, river_labels = _draw_rivers(map_rivers, view, camera_views, times, entry, seconds)
    drawn_regions, region_labels = _draw_regions(
        map_regions, view, camera_views, times, entry, seconds
    )

    return _Prepared(
        props=props,
        seconds=seconds,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        geom=geom,
        scale=scale,
        plate_w=plate_w,
        plate_h=plate_h,
        plate_x=plate_x,
        plate_y=plate_y,
        entry=entry,
        colors=colors,
        subject=subject,
        view=view,
        focus=focus,
        pin=pin,
        label=label,
        callouts=callouts,
        projected=projected,
        drawn_route=drawn_route,
        rivers=drawn_rivers,
        river_labels=river_labels,
        regions=drawn_regions,
        region_labels=region_labels,
        callout_delays=callout_delays,
        move_seconds=move_seconds,
        camera_moves=camera_moves,
        camera_views=camera_views,
        dropped_zooms=dropped_zooms,
        legs=legs,
        journey_icon=journey.icon if journey is not None else "none",
        journey_km=bool(journey and journey.show_km),
        bubbles=placed_bubbles,
        stickers=placed_stickers,
    )


def map_scene_motion_warnings(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Readable warnings for every zoom the cut leaves no time to play."""
    prepared = _prepare(scene, design, width=width, height=height, duration=duration, words=words)
    scene_id = str(scene.get("id") or "scene")
    return [
        f"{scene_id}: zoom {dropped.index} dropped: needs {dropped.needed:.2f}s, "
        f"{dropped.available:.2f}s left before the cut"
        for dropped in prepared.dropped_zooms
    ]


def map_scene_has_camera_moves(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> bool:
    """True when at least one zoom actually lands before the cut."""
    prepared = _prepare(scene, design, width=width, height=height, duration=duration, words=words)
    return bool(prepared.camera_moves)


def _text_label_box(
    text: str,
    side: str,
    x: float,
    y: float,
    *,
    font_size: float,
    gap: float,
) -> Rect4:
    """A label's drawn box in plate coordinates, estimated from the CSS face."""
    width = (LABEL_ADVANCE + LABEL_TRACKING) * font_size * len(text) + gap
    height = font_size * TITLE_LINE_HEIGHT
    if side == "left":
        return (x - gap - width, y - height / 2.0, width, height)
    return (x + gap, y - height / 2.0, width, height)


def _label_box(
    marker: _ProjectedMarker,
    side: str,
    x: float,
    y: float,
    *,
    font_size: float,
    gap: float,
) -> Rect4:
    """The drawn label's box for one marker."""
    return _text_label_box(marker.text, side, x, y, font_size=font_size, gap=gap)


def _outside(box: Rect4, frame: Rect4) -> bool:
    """True when ``box`` is not wholly inside ``frame``."""
    x, y, width, height = box
    return (
        x < frame[0]
        or y < frame[1]
        or x + width > frame[0] + frame[2]
        or y + height > frame[1] + frame[3]
    )


def _overlap(first: Rect4, second: Rect4) -> bool:
    return (
        first[0] < second[0] + second[2]
        and second[0] < first[0] + first[2]
        and first[1] < second[1] + second[3]
        and second[1] < first[1] + first[3]
    )


def _label_fits(
    text: str,
    side: str,
    points: Sequence[tuple[float, float]],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
) -> bool:
    """True when a label on ``side`` stays in the frame at every point."""
    if font_size <= 0.0:
        return True
    return all(
        not _outside(_text_label_box(text, side, x, y, font_size=font_size, gap=margin), frame)
        for x, y in points
    )


def _label_side_for(
    text: str,
    base: tuple[float, float],
    points: Sequence[tuple[float, float]],
    view: View,
    *,
    font_size: float,
    margin: float,
) -> str:
    """The side that keeps a label in frame, preferring the final landing.

    Both sides fitting keeps the base rule's choice. Otherwise the other side
    is taken when it fits every point, and failing that, when it fits the last
    camera landing where the label is most likely to be read.
    """
    side = _label_side(base, view)
    other = "left" if side == "right" else "right"
    if _label_fits(text, side, points, view.frame, font_size=font_size, margin=margin):
        return side
    if _label_fits(text, other, points, view.frame, font_size=font_size, margin=margin):
        return other
    if not _label_fits(
        text, side, points[-1:], view.frame, font_size=font_size, margin=margin
    ) and _label_fits(text, other, points[-1:], view.frame, font_size=font_size, margin=margin):
        return other
    return side


def map_scene_motion_problems(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    width: int = 1920,
    height: int = 1080,
    duration: float,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """Pure-geometry problems visible on the base view and after each move.

    Pins and labels are checked against the map frame; two marker labels are
    also checked for overlap. Only a pin that lands outside the frame is out
    of shot by design. Any pin still in frame must keep its label inside the
    frame too, even when the move's own zoom window does not name it.
    """
    prepared = _prepare(scene, design, width=width, height=height, duration=duration, words=words)
    scene_id = str(scene.get("id") or "scene")
    frame = (
        prepared.geom.map_frame[0] * prepared.scale,
        prepared.geom.map_frame[1] * prepared.scale,
        prepared.geom.map_frame[2] * prepared.scale,
        prepared.geom.map_frame[3] * prepared.scale,
    )
    font_size = prepared.geom.place * prepared.scale
    label_gap = LABEL_MARGIN * prepared.scale
    specs = _overlay_label_specs(prepared)
    placements = _layout_labels(
        specs,
        prepared.view,
        prepared.camera_moves,
        frame,
        font_size=font_size,
        margin=label_gap,
        seconds=prepared.seconds,
        occupied_shapes=_occupied_shapes(prepared),
    )
    problems = _label_problems(
        specs,
        placements,
        prepared.view,
        prepared.camera_moves,
        frame,
        font_size=font_size,
        margin=label_gap,
        scene_id=scene_id,
        seconds=prepared.seconds,
    )

    if prepared.pin is not None:
        focus_points = _track_positions(prepared.pin[0], prepared.pin[1], prepared.camera_moves)
        for move in range(len(prepared.camera_moves)):
            x, y = focus_points[move + 1]
            if _outside((x, y, 0.0, 0.0), frame):
                problems.append(
                    f"{scene_id}: focus pin lands outside the map frame after move {move}"
                )
    return problems


def render_map(
    scene: Mapping[str, Any],
    design: DesignTokens,
    *,
    index: int,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    words: Sequence[Mapping[str, Any]] | None = None,
    fps: int = DEFAULT_FPS,
) -> MapSceneRender:
    """Render ``scene`` for the compiler; ``index`` scopes every selector.

    ``fps`` is the composition frame rate the optional stutter snaps its step
    grid to. It only matters when the scene sets ``stutter``.
    """
    index = int(index)
    prepared = _prepare(scene, design, width=width, height=height, duration=duration, words=words)
    props = prepared.props
    stutter = _stutter_rate(props)
    route_dot = _flag(props, "route_dot")
    geom = prepared.geom
    scale = prepared.scale
    plate_w = prepared.plate_w
    plate_h = prepared.plate_h
    plate_x = prepared.plate_x
    plate_y = prepared.plate_y
    entry = prepared.entry
    colors = prepared.colors
    canvas_w = prepared.canvas_w
    canvas_h = prepared.canvas_h
    seconds = prepared.seconds
    view = prepared.view
    subject = prepared.subject
    pin = prepared.pin
    label = prepared.label
    callouts = prepared.callouts
    projected = prepared.projected
    drawn_route = prepared.drawn_route
    map_rivers = prepared.rivers
    river_labels = prepared.river_labels
    map_regions = prepared.regions
    region_labels = prepared.region_labels
    camera_moves = prepared.camera_moves
    move_seconds = prepared.move_seconds

    scene_id = str(scene.get("id") or "scene")
    for dropped in prepared.dropped_zooms:
        logger.warning(
            "map_zoom_dropped",
            scene_id=scene_id,
            zoom_index=dropped.index,
            needed_seconds=round(dropped.needed, 3),
            available_seconds=round(dropped.available, 3),
        )

    display = font_stack(design.type.display)
    body = font_stack(design.type.body)

    # The finest outlines the largest push needs; a still map stays on 110m.
    detail = _detail_for(camera_moves)

    cards = _cards(callouts)
    for delay in prepared.callout_delays:
        cards = cards.replace(
            '<div class="map-card">',
            f'<div class="map-card" style="animation-delay:{delay:.3f}s">',
            1,
        )

    stage_motion = ""
    try:
        drift = build_motion(
            MotionSpec(
                name="drift",
                params={"from": "scale(1)", "to": f"scale({design.motion.drift:g})"},
                ease="ease-in-out",
            ),
            clip_duration=seconds,
        )
        stage_motion = drift.keyframes
        stage_animation = drift.declaration
    except MotionError:
        stage_animation = "none"

    watermark = _text(props, "watermark")
    watermark_markup = f'<div class="map-mark">{html.escape(watermark)}</div>' if watermark else ""
    legs = prepared.legs
    journey_svg = ""
    journey_css = ""
    journey_animations: list[str] = []
    km_html = ""
    if legs:
        journey_svg = legs_svg(
            legs,
            index=index,
            icon=prepared.journey_icon,
            line_color=colors.get("route") or colors["pin"],
            icon_color=colors.get("vehicle") or "#E63946",
            stroke=ROUTE_STROKE * scale,
            icon_size=34.0 * scale,
        )
        journey_css, journey_animations = legs_css(
            legs,
            index=index,
            seconds=seconds,
            icon=prepared.journey_icon,
            width_var=f"--map-route-width-{index}",
            base=ROUTE_STROKE * scale,
        )
        if prepared.journey_km:
            km_html, km_css = km_markup(legs, index=index, seconds=seconds)
            journey_css += km_css + _km_label_css(index, colors, scale)
    map_svg = _map_svg(
        subject,
        view,
        colors,
        width=plate_w,
        height=plate_h,
        scale=scale,
        route=drawn_route,
        rivers=map_rivers,
        regions=map_regions,
        index=index,
        detail=detail,
        route_dot=route_dot,
        worldview=_worldview(props),
        extra_svg=journey_svg,
    )
    label_frame = (
        geom.map_frame[0] * scale,
        geom.map_frame[1] * scale,
        geom.map_frame[2] * scale,
        geom.map_frame[3] * scale,
    )
    label_font = geom.place * scale
    label_gap = LABEL_MARGIN * scale
    label_specs = _overlay_label_specs(prepared)
    label_placements = _layout_labels(
        label_specs,
        view,
        camera_moves,
        label_frame,
        font_size=label_font,
        margin=label_gap,
        seconds=seconds,
        occupied_shapes=_occupied_shapes(prepared),
    )
    marker_count = len(projected)
    river_count = len(river_labels)
    region_count = len(region_labels)
    marker_placements = label_placements[:marker_count]
    river_placements = label_placements[marker_count : marker_count + river_count]
    region_placements = label_placements[
        marker_count + river_count : marker_count + river_count + region_count
    ]
    marker_specs = label_specs[:marker_count]
    river_specs = label_specs[marker_count : marker_count + river_count]
    region_specs = label_specs[
        marker_count + river_count : marker_count + river_count + region_count
    ]
    tracks = [
        _Track(
            name=f"marker_{position}",
            x=marker.x,
            y=marker.y,
            reveal=f"map_marker_{index}",
            seconds=entry * 1.4,
            delay=marker.delay,
            drop=True,
        )
        for position, marker in enumerate(projected)
    ]
    if pin is not None:
        tracks.append(
            _Track(
                name="focus",
                x=pin[0],
                y=pin[1],
                reveal=f"map_pin_{index}",
                seconds=entry * 1.4,
                delay=0.0,
                ease="cubic-bezier(.2,.8,.2,1)",
            )
        )
    if pin is not None and label:
        tracks.append(
            _Track(
                name="place",
                x=pin[0],
                y=pin[1],
                reveal=f"map_place_{index}",
                seconds=entry * 1.2,
                delay=entry * 1.5,
            )
        )
    for spec, placement in zip(marker_specs, marker_placements, strict=True):
        if placement.mode != "callout":
            continue
        tracks.append(
            _Track(
                name=f"callout_{spec.key}",
                x=spec.x,
                y=spec.y,
                reveal=f"map_marker_{index}",
                seconds=entry * 1.2,
                delay=max(0.0, spec.delay - 0.25),
            )
        )
    for spec, placement in zip(
        [*river_specs, *region_specs],
        [*river_placements, *region_placements],
        strict=True,
    ):
        reveal = f"map_river_label_{index}" if spec.kind == "river" else f"map_region_label_{index}"
        first = next(
            (point for point in (spec.view_points or ()) if point is not None),
            (spec.x, spec.y),
        )
        visible = (
            tuple(point is not None for point in spec.view_points)
            if spec.view_points is not None
            else None
        )
        if placement.mode == "callout":
            tracks.append(
                _Track(
                    name=f"callout_{spec.key}",
                    x=first[0],
                    y=first[1],
                    reveal=reveal,
                    seconds=0.2,
                    delay=max(0.0, spec.delay - 0.25),
                    points=spec.view_points,
                    visible=visible,
                )
            )
        else:
            tracks.append(
                _Track(
                    name=spec.key,
                    x=first[0],
                    y=first[1],
                    reveal=reveal,
                    seconds=entry * 1.2,
                    delay=spec.delay,
                    points=spec.view_points,
                    visible=visible,
                )
            )
    if prepared.journey_km:
        for position, leg in enumerate(legs):
            tracks.append(
                _Track(
                    name=f"km_{position}",
                    x=leg.label_at[0],
                    y=leg.label_at[1],
                    reveal=f"map_marker_{index}",
                    seconds=entry,
                    delay=leg.delay,
                )
            )
    overlay_html = ""
    for position, (bubble, x, y, delay) in enumerate(prepared.bubbles):
        overlay_html += bubble_markup(position, bubble.text, x, y)
        tracks.append(
            _Track(
                name=f"bubble_{position}",
                x=x,
                y=y,
                reveal=f"map_pop_{index}",
                seconds=0.45,
                delay=delay,
            )
        )
    for position, (sticker, x, y, delay) in enumerate(prepared.stickers):
        overlay_html += sticker_markup(position, sticker, x, y, scale)
        tracks.append(
            _Track(
                name=f"sticker_{position}",
                x=x,
                y=y,
                reveal=f"map_pop_{index}",
                seconds=0.45,
                delay=delay,
            )
        )
    if overlay_html:
        journey_css += overlay_css(index, scale)
    if any(region.style == "photo" for region in map_regions):
        # A place name over a bright photo needs its own backing to stay legible.
        journey_css += (
            f"      .map-{index} .map-place {{ background:{colors.get('km_bg') or 'rgba(10, 30, 28, 0.86)'};\n"
            f"        padding:{3 * scale:.1f}px {8 * scale:.1f}px; border-radius:{4 * scale:.1f}px; }}\n"
        )
    labels_markup, label_css = _labels_markup(
        label_specs,
        label_placements,
        index=index,
        font_size=label_font,
        ink=colors["ink"],
        reveal_seconds=entry * 1.2,
    )

    markup = (
        f'<div class="map-{index}">'
        + paper_layers(f"map-{index}", "map", colors, canvas_w, canvas_h, scale)
        + f'<div class="map-plate" style="left:{plate_x:.1f}px;top:{plate_y:.1f}px;'
        f'width:{plate_w:.1f}px;height:{plate_h:.1f}px">'
        f'<div class="map-stage" style="animation:{stage_animation}">'
        '<div class="map-window"><div class="map-fade"><div class="map-camera">'
        f'<div class="map-ghost-wrap">{map_svg}</div>'
        "</div></div>"
        f'<div class="map-overlay">{_marker_pins(projected)}{_focus_pin(pin)}'
        f"{labels_markup}{km_html}{overlay_html}</div>"
        "</div>"
        f"{_badge(props)}{cards}"
        f"{_title(props)}{watermark_markup}"
        "</div>"
        "</div>"
        "</div>"
    )

    css = _map_css(
        index,
        colors,
        display=display,
        body=body,
        geom=geom,
        scale=scale,
        entry=entry,
        seconds=seconds,
        stage_motion=stage_motion,
        route=drawn_route,
        camera_moves=camera_moves,
        move_seconds=move_seconds,
        tracks=tracks,
        stutter=stutter,
        fps=fps,
        route_dot=route_dot,
        rivers=map_rivers,
        regions=map_regions,
        journey_css=journey_css,
        journey_animations=journey_animations,
    )
    css += label_css
    return MapSceneRender(
        markup=markup,
        css=css,
        dropped_zooms=tuple(dropped.index for dropped in prepared.dropped_zooms),
    )


def _reveal_delays(
    times: Mapping[str, float],
    prefix: str,
    count: int,
    fallback_start: float,
    stagger: float,
    latest: float,
) -> list[float]:
    """When each item reveals: its cue, else a bounded stagger from the top."""
    delays: list[float] = []
    for position in range(count):
        fallback = fallback_start + position * stagger
        cued = times.get(f"{prefix}_{position}")
        resolved = fallback if cued is None or not math.isfinite(cued) else cued
        delays.append(min(latest, max(0.0, resolved)))
    return delays


def _project_markers(
    markers: Sequence[MapMarker], view: View, delays: Sequence[float]
) -> list[_ProjectedMarker]:
    """Place every marker on the plate, failing on one the window cannot show."""
    projected: list[_ProjectedMarker] = []
    for marker, delay in zip(markers, delays, strict=True):
        if not view.contains(marker.lon, marker.lat):
            raise MapSceneError(f"map marker {marker.text!r} is outside the map window")
        x, y = view.project(marker.lon, marker.lat)
        projected.append(
            _ProjectedMarker(
                text=marker.text,
                x=x,
                y=y,
                delay=delay,
                lon=marker.lon,
                lat=marker.lat,
                callout=marker.callout,
            )
        )
    return projected


def _draw_route(
    route: MapRoute | None,
    view: View,
    times: Mapping[str, float],
    latest: float,
    entry: float,
    *,
    curve: str = "straight",
    window: tuple[float, float, float, float] | None = None,
) -> _ProjectedRoute | None:
    """Project the route and measure it, so the dash draw can hide then reveal it."""
    if route is None:
        return None
    for lon, lat in route.points:
        if not view.contains(lon, lat):
            raise MapSceneError("a map route leaves the map window")
    sampled: list[tuple[float, float]]
    if curve == "great_circle":
        sampled = [route.points[0]]
        for position in range(1, len(route.points)):
            segment = _great_circle_points(route.points[position - 1], route.points[position])
            for lon, lat in segment[1:]:
                if window is not None:
                    west, south, east, north = window
                    inside = west <= lon <= east and south <= lat <= north
                else:
                    inside = view.contains(lon, lat)
                if not inside:
                    raise MapSceneError("a map route leaves the map window")
                lon = _unwrap_longitude(lon, sampled[-1][0])
                sampled.append((lon, lat))
        points = [view.project(lon, lat) for lon, lat in sampled]
    else:
        points = [view.project(lon, lat) for lon, lat in route.points]
    segments: list[str] = []
    length = 0.0
    for position, (x, y) in enumerate(points):
        segments.append(f"{'M' if position == 0 else 'L'}{x:.1f} {y:.1f}")
        if position:
            previous_x, previous_y = points[position - 1]
            length += math.hypot(x - previous_x, y - previous_y)
    cued = times.get("route")
    delay = entry * 1.2 if cued is None or not math.isfinite(cued) else cued
    return _ProjectedRoute(
        path="".join(segments), length=length, delay=min(latest, max(0.0, delay))
    )


def _path_from_plate_points(points: Sequence[tuple[float, float]]) -> tuple[str, float]:
    """An SVG path and its measured length from projected plate points."""
    segments: list[str] = []
    length = 0.0
    for position, (x, y) in enumerate(points):
        segments.append(f"{'M' if position == 0 else 'L'}{x:.1f} {y:.1f}")
        if position:
            previous_x, previous_y = points[position - 1]
            length += math.hypot(x - previous_x, y - previous_y)
    return "".join(segments), length


def _draw_rivers(
    rivers: Sequence[MapRiver],
    view: View,
    camera_views: Sequence[View],
    times: Mapping[str, float],
    entry: float,
    seconds: float,
) -> tuple[list[_PreparedRiver], list[_ProjectedMarker]]:
    """Project each river's course and schedule its draw and label."""
    if not rivers:
        return [], []
    views = [view, *camera_views]
    draw_seconds = min(2.4, max(0.8, seconds * 0.28))
    stagger = min(0.45, max(0.08, (seconds - entry) / max(len(rivers), 1) / 3.0))
    latest_delay = max(0.0, seconds - draw_seconds - 0.15)
    delays = _reveal_delays(times, "river", len(rivers), entry * 1.0, stagger, latest_delay)
    prepared: list[_PreparedRiver] = []
    labels: list[_ProjectedMarker] = []
    for river, delay in zip(rivers, delays, strict=True):
        course, tributaries = _river_course(river.features)
        pieces: list[_RiverPiece] = []
        for chain, is_main in (
            (course, True),
            *((tributary, False) for tributary in tributaries),
        ):
            for clipped in _clip_polyline(chain, view.bounds):
                path, length = _path_from_plate_points(
                    [view.project(lon, lat) for lon, lat in clipped]
                )
                if length > 0.0:
                    pieces.append(_RiverPiece(path=path, length=length, main=is_main))
        label_points: list[tuple[float, float] | None] = []
        if river.label:
            for label_view in views:
                midpoint = _visible_river_label(course, label_view)
                label_points.append(label_view.project(*midpoint) if midpoint is not None else None)
        anchor: _ProjectedMarker | None = None
        first = next((point for point in label_points if point is not None), None)
        if river.label and first is not None:
            anchor = _ProjectedMarker(
                text=river.label,
                x=first[0],
                y=first[1],
                delay=min(seconds, delay + draw_seconds),
            )
            labels.append(anchor)
        prepared.append(
            _PreparedRiver(
                name=river.name,
                label=river.label,
                delay=delay,
                flow=river.flow,
                pieces=tuple(pieces),
                label_anchor=anchor,
                label_points=tuple(label_points),
                callout=river.callout,
            )
        )
    return prepared, labels


def _region_shapes(region: Region, view: View) -> tuple[_RegionShape, ...]:
    """Every visible region polygon, projected with its outline length."""
    shapes: list[_RegionShape] = []
    for polygon in region.polygons:
        parts: list[str] = []
        length = 0.0
        for ring in polygon:
            if len(ring) < 3 or not any(view.contains(lon, lat, margin=1.0) for lon, lat in ring):
                continue
            # Start the outline at a vertex the current window can see, so the
            # draw-on begins in shot rather than behind the camera.
            visible = [position for position, point in enumerate(ring) if view.contains(*point)]
            start = visible[0] if visible else 0
            ordered = ring[start:] + ring[:start]
            points = [view.project(lon, lat) for lon, lat in ordered]
            parts.append("M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in points) + "Z")
            length += sum(
                math.hypot(right[0] - left[0], right[1] - left[1])
                for left, right in zip(points, points[1:], strict=False)
            )
            length += math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1])
        if parts:
            shapes.append(_RegionShape(path="".join(parts), length=length))
    return tuple(shapes)


def _polygon_area_centroid(
    ring: Sequence[tuple[float, float]],
) -> tuple[float, float, float]:
    """``(area, lon, lat)`` centroid of one lon/lat ring."""
    area = 0.0
    lon_total = 0.0
    lat_total = 0.0
    previous = ring[-1]
    for current in ring:
        x0, y0 = previous
        x1, y1 = current
        cross = x0 * y1 - x1 * y0
        area += cross
        lon_total += (x0 + x1) * cross
        lat_total += (y0 + y1) * cross
        previous = current
    area *= 0.5
    if abs(area) < 1e-9:
        return (
            0.0,
            sum(lon for lon, _ in ring) / len(ring),
            sum(lat for _, lat in ring) / len(ring),
        )
    return (abs(area), lon_total / (6.0 * area), lat_total / (6.0 * area))


def _point_in_polygon(
    lon: float, lat: float, polygon: Sequence[Sequence[tuple[float, float]]]
) -> bool:
    """True when a point is inside the polygon and outside any hole."""
    if not polygon or not _in_ring(lon, lat, polygon[0]):
        return False
    return not any(_in_ring(lon, lat, hole) for hole in polygon[1:])


def _distance_to_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> float:
    """Shortest distance from a point to a ring's outline, in scaled degrees."""
    coslat = math.cos(math.radians(lat))
    best = math.inf
    previous = ring[-1]
    for current in ring:
        ax = (previous[0] - lon) * coslat
        ay = previous[1] - lat
        bx = (current[0] - lon) * coslat
        by = current[1] - lat
        dx = bx - ax
        dy = by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0.0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
        previous = current
    return best


def _polygon_distance(
    lon: float, lat: float, polygon: Sequence[Sequence[tuple[float, float]]]
) -> float:
    return min(_distance_to_ring(lon, lat, ring) for ring in polygon if len(ring) >= 3)


def _sample_poi(
    polygon: Sequence[Sequence[tuple[float, float]]],
    west: float,
    east: float,
    south: float,
    north: float,
    steps: int,
) -> tuple[tuple[float, float] | None, float]:
    """The most interior grid sample in a box, deterministically."""
    best: tuple[float, float] | None = None
    best_distance = -1.0
    for x_index in range(steps + 1):
        lon = west + (east - west) * x_index / steps
        for y_index in range(steps + 1):
            lat = south + (north - south) * y_index / steps
            if not _point_in_polygon(lon, lat, polygon):
                continue
            distance = _polygon_distance(lon, lat, polygon)
            if distance > best_distance:
                best = (lon, lat)
                best_distance = distance
    return best, best_distance


def _polygon_poi(
    polygon: Sequence[Sequence[tuple[float, float]]],
) -> tuple[float, float]:
    """A pole of inaccessibility inside the polygon, by coarse grid + refinement."""
    outer = polygon[0]
    lons = [point[0] for point in outer]
    lats = [point[1] for point in outer]
    west, east = min(lons), max(lons)
    south, north = min(lats), max(lats)
    if east <= west or north <= south:
        return (
            sum(point[0] for point in outer) / len(outer),
            sum(point[1] for point in outer) / len(outer),
        )
    best, _distance = _sample_poi(polygon, west, east, south, north, 32)
    if best is None:
        return (
            sum(point[0] for point in outer) / len(outer),
            sum(point[1] for point in outer) / len(outer),
        )
    step_lon = (east - west) / 32.0
    step_lat = (north - south) / 32.0
    for _ in range(3):
        lon, lat = best
        best, best_distance = _sample_poi(
            polygon,
            lon - step_lon,
            lon + step_lon,
            lat - step_lat,
            lat + step_lat,
            8,
        )
        if best is None:
            break
        step_lon = 2.0 * step_lon / 8.0
        step_lat = 2.0 * step_lat / 8.0
    return best if best is not None else (lon, lat)


@cache
def _region_centre(region: Region) -> tuple[float, float] | None:
    """A point guaranteed inside the region's largest polygon, in lon/lat."""
    best: tuple[float, float] | None = None
    best_area = -1.0
    for polygon in region.polygons:
        if not polygon or len(polygon[0]) < 3:
            continue
        area = _polygon_area_centroid(polygon[0])[0]
        if area > best_area:
            best = _polygon_poi(polygon)
            best_area = area
    return best


def _view_ends(
    moves: Sequence[tuple[float, float, float, float]], seconds: float
) -> tuple[float, ...]:
    """The end time of each view: base, then each camera landing."""
    if not moves:
        return (seconds,)
    return (moves[0][0], *(move[0] for move in moves[1:]), seconds)


def _cluster_ring(
    ring: Sequence[tuple[float, float]], bounds: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Sutherland-Hodgman clip of one ring to a lon/lat rectangle."""
    west, south, east, north = bounds

    def clip(
        polygon: list[tuple[float, float]],
        inside: Callable[[tuple[float, float]], bool],
        intersect: Callable[[tuple[float, float], tuple[float, float]], tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not polygon:
            return []
        output: list[tuple[float, float]] = []
        previous = polygon[-1]
        for current in polygon:
            if inside(current):
                if not inside(previous):
                    output.append(intersect(previous, current))
                output.append(current)
            elif inside(previous):
                output.append(intersect(previous, current))
            previous = current
        return output

    polygon = list(ring)
    polygon = clip(
        polygon,
        lambda point: point[0] >= west,
        lambda first, second: (
            west,
            first[1] + (second[1] - first[1]) * (west - first[0]) / (second[0] - first[0]),
        ),
    )
    polygon = clip(
        polygon,
        lambda point: point[0] <= east,
        lambda first, second: (
            east,
            first[1] + (second[1] - first[1]) * (east - first[0]) / (second[0] - first[0]),
        ),
    )
    polygon = clip(
        polygon,
        lambda point: point[1] >= south,
        lambda first, second: (
            first[0] + (second[0] - first[0]) * (south - first[1]) / (second[1] - first[1]),
            south,
        ),
    )
    polygon = clip(
        polygon,
        lambda point: point[1] <= north,
        lambda first, second: (
            first[0] + (second[0] - first[0]) * (north - first[1]) / (second[1] - first[1]),
            north,
        ),
    )
    return polygon if len(polygon) >= 3 else []


def _clipped_region(
    region: Region, view: View
) -> list[tuple[float, list[list[tuple[float, float]]]]]:
    clipped: list[tuple[float, list[list[tuple[float, float]]]]] = []
    for polygon in region.polygons:
        rings = [ring for ring in (_cluster_ring(r, view.bounds) for r in polygon) if ring]
        if not rings:
            continue
        area = abs(_polygon_area_centroid(rings[0])[0])
        area -= sum(abs(_polygon_area_centroid(hole)[0]) for hole in rings[1:])
        if area > 0.0:
            clipped.append((area, rings))
    return clipped


def _region_view_label(
    region: Region, view: View, *, threshold: float = 0.02
) -> tuple[
    tuple[float, float] | None,
    tuple[tuple[tuple[float, float], ...], ...],
]:
    """The visible-part POI and its projected rings for one view."""
    clipped = _clipped_region(region, view)
    if not clipped:
        return None, ()
    west, south, east, north = view.bounds
    frame_area = max(0.0, (east - west) * (north - south))
    visible_area = sum(area for area, _rings in clipped)
    if frame_area <= 0.0 or visible_area / frame_area < threshold:
        return None, ()
    largest = max(clipped, key=lambda item: item[0])[1]
    rings = tuple(tuple(view.project(lon, lat) for lon, lat in ring) for ring in largest)
    return _polygon_poi(largest), rings


def _visible_region_label(
    region: Region, view: View, *, threshold: float = 0.02
) -> tuple[float, float] | None:
    """The most interior point of the region's visible part, or ``None``."""
    return _region_view_label(region, view, threshold=threshold)[0]


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    return sum(
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(points, points[1:], strict=False)
    )


def _visible_river_label(
    course: Sequence[tuple[float, float]], view: View, *, threshold: float = 0.02
) -> tuple[float, float] | None:
    """The midpoint of the visible river run, or ``None`` when it is tiny."""
    pieces = _clip_polyline(course, view.bounds)
    if not pieces:
        return None
    best = max(pieces, key=_polyline_length)
    west, south, east, north = view.bounds
    if _polyline_length(best) < threshold * ((east - west) + (north - south)):
        return None
    return _river_midpoint(best, view)


def _draw_regions(
    regions: Sequence[MapRegion],
    view: View,
    camera_views: Sequence[View],
    times: Mapping[str, float],
    entry: float,
    seconds: float,
) -> tuple[list[_PreparedRegion], list[_ProjectedMarker]]:
    """Project each region's polygons and schedule its outline and fill."""
    if not regions:
        return [], []
    views = [view, *camera_views]
    draw_seconds = min(1.6, max(0.7, seconds * 0.18))
    stagger = min(0.18, max(0.06, (seconds - entry) / max(len(regions), 1) / 3.0))
    latest_delay = max(0.0, seconds - draw_seconds - 0.15)
    delays = _reveal_delays(times, "region", len(regions), entry * 1.0, stagger, latest_delay)
    prepared: list[_PreparedRegion] = []
    labels: list[_ProjectedMarker] = []
    for region, delay in zip(regions, delays, strict=True):
        shapes = _region_shapes(region.region, view)
        label_points: list[tuple[float, float] | None] = []
        view_shapes: list[tuple[tuple[tuple[float, float], ...], ...]] = []
        for label_view in views:
            # States and districts are small on a country frame, and naming
            # them is the point, so their labels survive a far lower share.
            threshold = 0.0005 if region.region.kind in {"state", "district"} else 0.02
            centre, rings = _region_view_label(region.region, label_view, threshold=threshold)
            view_shapes.append(rings)
            if region.label:
                label_points.append(label_view.project(*centre) if centre is not None else None)
        anchor: _ProjectedMarker | None = None
        first = next((point for point in label_points if point is not None), None)
        if region.label and first is not None:
            anchor = _ProjectedMarker(
                text=region.label,
                x=first[0],
                y=first[1],
                delay=min(seconds, delay),
            )
            labels.append(anchor)
        projected = [
            view.project(lon, lat)
            for polygon in region.region.polygons
            for lon, lat in (polygon[0] if polygon else ())
        ]
        box = (
            (
                min(x for x, _ in projected),
                min(y for _, y in projected),
                max(x for x, _ in projected),
                max(y for _, y in projected),
            )
            if projected
            else (0.0, 0.0, 0.0, 0.0)
        )
        prepared.append(
            _PreparedRegion(
                name=region.name,
                label=region.label,
                delay=delay,
                style=region.style,
                shapes=shapes,
                label_anchor=anchor,
                label_points=tuple(label_points),
                view_shapes=tuple(view_shapes),
                callout=region.callout,
                color=region.color,
                image=region.image,
                box=box,
            )
        )
    return prepared, labels


def _place_label(
    pin: tuple[float, float] | None,
    label: str,
    view: View,
    *,
    moves: Sequence[tuple[float, float, float, float]] = (),
    font_size: float = 0.0,
    margin: float = 0.0,
    side: str | None = None,
    nudge: float = 0.0,
) -> str:
    if pin is None or not label:
        return ""
    if side is None:
        points = _track_positions(pin[0], pin[1], moves)
        side = _label_side_for(label, pin, points, view, font_size=font_size, margin=margin)
    style, attr = _label_style(side, nudge)
    return (
        f'<div class="map-place is-{side}" data-camera-track="place"{attr} '
        f'style="left:{pin[0]:.1f}px;top:{pin[1]:.1f}px{style}">'
        f"{html.escape(label)}</div>"
    )


def _occupied_shapes(
    prepared: _Prepared,
) -> list[tuple[tuple[tuple[float, float], ...], ...]]:
    count = len(prepared.camera_views) + 1
    return [
        tuple(
            ring
            for region in prepared.regions
            for ring in (region.view_shapes[index] if index < len(region.view_shapes) else ())
        )
        for index in range(count)
    ]


def _overlay_label_specs(prepared: _Prepared) -> list[_LabelSpec]:
    """Every overlay label in render order: markers, rivers, regions, focus."""
    specs: list[_LabelSpec] = [
        _LabelSpec(
            "marker",
            position,
            marker.text,
            marker.x,
            marker.y,
            delay=marker.delay,
            placement="callout" if marker.callout else "side",
            key=f"marker_{position}",
        )
        for position, marker in enumerate(prepared.projected)
    ]
    river_row = 0
    for river_position, river in enumerate(prepared.rivers):
        anchor = river.label_anchor
        if anchor is None:
            continue
        specs.append(
            _LabelSpec(
                "river",
                river_row,
                anchor.text,
                anchor.x,
                anchor.y,
                delay=anchor.delay,
                view_points=river.label_points or None,
                placement="callout" if river.callout else "side",
                key=f"river_{river_position}",
            )
        )
        river_row += 1
    region_row = 0
    for region_position, region in enumerate(prepared.regions):
        anchor = region.label_anchor
        if anchor is None:
            continue
        if region.callout is True:
            placement = "callout"
        elif region.callout is False:
            placement = "side"
        else:
            placement = "auto"
        specs.append(
            _LabelSpec(
                "region",
                region_row,
                anchor.text,
                anchor.x,
                anchor.y,
                delay=anchor.delay,
                view_points=region.label_points or None,
                placement=placement,
                key=f"region_{region_position}",
                view_shapes=region.view_shapes or None,
            )
        )
        region_row += 1
    if prepared.pin is not None and prepared.label:
        specs.append(
            _LabelSpec(
                "place",
                0,
                prepared.label,
                prepared.pin[0],
                prepared.pin[1],
                delay=prepared.entry * 1.5,
                key="place",
            )
        )
    return specs


def _label_points(
    spec: _LabelSpec,
    moves: Sequence[tuple[float, float, float, float]],
    frame: Rect4,
) -> list[tuple[float, float] | None]:
    if spec.view_points is not None:
        return list(spec.view_points)
    points: list[tuple[float, float] | None] = list(_track_positions(spec.x, spec.y, moves))
    if spec.clamp_row >= 0:
        points = [
            _clamp_frame_point(point, frame, row=spec.clamp_row)
            for point in points
            if point is not None
        ]
    return points


def _label_size(text: str, font_size: float) -> tuple[float, float]:
    return (
        (LABEL_ADVANCE + LABEL_TRACKING) * font_size * len(text),
        font_size * TITLE_LINE_HEIGHT,
    )


def _label_box_at(
    spec: _LabelSpec,
    placement: _LabelPlacement,
    point: tuple[float, float],
    *,
    font_size: float,
    margin: float,
) -> Rect4:
    x, y = point
    if placement.mode in {"centre", "callout"}:
        width, height = _label_size(spec.text, font_size)
        cx = x + placement.offset[0]
        cy = y + placement.offset[1]
        return (cx - width / 2.0, cy - height / 2.0, width, height)
    return _text_label_box(
        spec.text,
        placement.side,
        x,
        y + placement.nudge,
        font_size=font_size,
        gap=margin,
    )


def _label_view_boxes(
    spec: _LabelSpec,
    placement: _LabelPlacement,
    points: Sequence[tuple[float, float] | None],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
    view_ends: Sequence[float],
) -> list[Rect4 | None]:
    boxes: list[Rect4 | None] = []
    for index, point in enumerate(points):
        revealed = index >= len(view_ends) or spec.delay < view_ends[index]
        if point is None or not revealed:
            boxes.append(None)
            continue
        x, y = point
        if _outside((x, y, 0.0, 0.0), frame):
            boxes.append(None)
            continue
        boxes.append(_label_box_at(spec, placement, (x, y), font_size=font_size, margin=margin))
    return boxes


def _box_points(box: Rect4) -> tuple[tuple[float, float], ...]:
    x, y, width, height = box
    return (
        (x, y),
        (x + width / 2.0, y),
        (x + width, y),
        (x, y + height / 2.0),
        (x + width / 2.0, y + height / 2.0),
        (x + width, y + height / 2.0),
        (x, y + height),
        (x + width / 2.0, y + height),
        (x + width, y + height),
    )


def _centre_points(box: Rect4) -> tuple[tuple[float, float], ...]:
    x, y, width, height = box
    return (
        (x + width / 2.0, y + height / 2.0),
        (x + width / 2.0, y),
        (x + width / 2.0, y + height),
        (x, y + height / 2.0),
        (x + width, y + height / 2.0),
    )


def _point_in_shapes(
    point: tuple[float, float], shapes: Sequence[Sequence[tuple[float, float]]]
) -> bool:
    return any(_in_ring(point[0], point[1], ring) for ring in shapes)


def _nearest_box_point(point: tuple[float, float], box: Rect4) -> tuple[float, float]:
    x, y, width, height = box
    return (
        min(max(point[0], x), x + width),
        min(max(point[1], y), y + height),
    )


def _inside_box(point: tuple[float, float], box: Rect4) -> bool:
    x, y, width, height = box
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def _segments_cross(
    first: tuple[tuple[float, float], tuple[float, float]],
    second: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    (ax, ay), (bx, by) = first
    (cx, cy), (dx, dy) = second

    def orientation(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> float:
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)

    first_side = orientation(ax, ay, bx, by, cx, cy)
    second_side = orientation(ax, ay, bx, by, dx, dy)
    third_side = orientation(cx, cy, dx, dy, ax, ay)
    fourth_side = orientation(cx, cy, dx, dy, bx, by)
    return first_side * second_side <= 0.0 and third_side * fourth_side <= 0.0


def _segment_hits_box(segment: tuple[tuple[float, float], tuple[float, float]], box: Rect4) -> bool:
    start, end = segment
    if _inside_box(start, box) or _inside_box(end, box):
        return True
    x, y, width, height = box
    corners = (
        ((x, y), (x + width, y)),
        ((x + width, y), (x + width, y + height)),
        ((x + width, y + height), (x, y + height)),
        ((x, y + height), (x, y)),
    )
    return any(_segments_cross(segment, edge) for edge in corners)


def _placement_geometry(
    spec: _LabelSpec,
    placement: _LabelPlacement,
    points: Sequence[tuple[float, float] | None],
    view_ends: Sequence[float],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
) -> tuple[list[Rect4 | None], list[tuple[tuple[float, float], tuple[float, float]] | None]]:
    boxes = _label_view_boxes(
        spec,
        placement,
        points,
        frame,
        font_size=font_size,
        margin=margin,
        view_ends=view_ends,
    )
    leaders: list[tuple[tuple[float, float], tuple[float, float]] | None] = []
    for index, box in enumerate(boxes):
        point = points[index] if index < len(points) else None
        if box is None or point is None or placement.mode != "callout":
            leaders.append(None)
            continue
        end = _nearest_box_point(point, box)
        leaders.append((point, end) if math.dist(point, end) > 0.5 else None)
    return boxes, leaders


def _placement_violations(
    spec: _LabelSpec,
    placement: _LabelPlacement,
    points: Sequence[tuple[float, float] | None],
    view_ends: Sequence[float],
    frame: Rect4,
    *,
    placed_boxes: Sequence[Sequence[Rect4 | None]],
    placed_leaders: Sequence[Sequence[tuple[tuple[float, float], tuple[float, float]] | None]],
    occupied_shapes: Sequence[Sequence[Sequence[tuple[float, float]]]],
    font_size: float,
    margin: float,
) -> tuple[int, int]:
    boxes, leaders = _placement_geometry(
        spec, placement, points, view_ends, frame, font_size=font_size, margin=margin
    )
    violations = 0
    coverage = 0
    for index, box in enumerate(boxes):
        if box is None:
            continue
        if _outside(box, frame):
            violations += 100
        for other in placed_boxes[index]:
            if other is not None and _overlap(box, other):
                violations += 100
        for leader in placed_leaders[index]:
            if leader is not None and _segment_hits_box(leader, box):
                violations += 100
        own_leader = leaders[index]
        if placement.mode == "callout":
            if own_leader is None:
                violations += 100
            else:
                for other in placed_boxes[index]:
                    if other is not None and _segment_hits_box(own_leader, other):
                        violations += 100
                for other_leader in placed_leaders[index]:
                    if other_leader is not None and _segments_cross(own_leader, other_leader):
                        violations += 50
        if placement.mode == "callout" and index < len(occupied_shapes):
            coverage += sum(
                1 for point in _box_points(box) if _point_in_shapes(point, occupied_shapes[index])
            )
    return violations, coverage


def _centre_fits(
    spec: _LabelSpec,
    points: Sequence[tuple[float, float] | None],
    view_ends: Sequence[float],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
) -> bool:
    if spec.view_shapes is None:
        return False
    placement = _LabelPlacement("centre", 0.0, "centre", (0.0, 0.0))
    boxes = _label_view_boxes(
        spec,
        placement,
        points,
        frame,
        font_size=font_size,
        margin=margin,
        view_ends=view_ends,
    )
    visible = False
    for index, box in enumerate(boxes):
        if box is None:
            continue
        visible = True
        if index >= len(spec.view_shapes):
            return False
        shapes = spec.view_shapes[index]
        if not shapes:
            return False
        if not all(_point_in_shapes(point, shapes) for point in _centre_points(box)):
            return False
    return visible


def _layout_labels(
    specs: Sequence[_LabelSpec],
    view: View,
    moves: Sequence[tuple[float, float, float, float]],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
    seconds: float,
    occupied_shapes: Sequence[Sequence[Sequence[tuple[float, float]]]] | None = None,
) -> list[_LabelPlacement]:
    """Choose centre, callout or side placement for every overlay label."""
    step = font_size * TITLE_LINE_HEIGHT if font_size > 0.0 else 0.0
    view_ends = _view_ends(moves, seconds)
    occupied = list(occupied_shapes or [()] * len(view_ends))
    placements: list[_LabelPlacement] = []
    placed_boxes: list[list[Rect4 | None]] = [[] for _ in range(len(view_ends))]
    placed_leaders: list[list[tuple[tuple[float, float], tuple[float, float]] | None]] = [
        [] for _ in range(len(view_ends))
    ]
    for spec in specs:
        points = _label_points(spec, moves, frame)
        visible_points = [point for point in points if point is not None]
        primary = _label_side_for(
            spec.text,
            (spec.x, spec.y),
            visible_points or [(spec.x, spec.y)],
            view,
            font_size=font_size,
            margin=margin,
        )
        other = "left" if primary == "right" else "right"
        chosen: _LabelPlacement | None = None
        if spec.placement == "auto":
            centre = _LabelPlacement("centre", 0.0, "centre", (0.0, 0.0))
            if _centre_fits(spec, points, view_ends, frame, font_size=font_size, margin=margin):
                violations, _coverage = _placement_violations(
                    spec,
                    centre,
                    points,
                    view_ends,
                    frame,
                    placed_boxes=placed_boxes,
                    placed_leaders=placed_leaders,
                    occupied_shapes=occupied,
                    font_size=font_size,
                    margin=margin,
                )
                if violations == 0:
                    chosen = centre
        if chosen is None and spec.placement in {"auto", "callout"}:
            width, height = _label_size(spec.text, font_size)
            base = max(width, height) * 0.55 + margin
            distances = (base, base * 1.7, base * 2.6)
            angles = (0, 45, 90, 135, 180, 225, 270, 315)
            best: _LabelPlacement | None = None
            best_score = math.inf
            for distance in distances:
                for degrees in angles:
                    radians = math.radians(degrees)
                    candidate = _LabelPlacement(
                        "right",
                        0.0,
                        "callout",
                        (
                            distance * math.cos(radians),
                            distance * math.sin(radians),
                        ),
                    )
                    violations, coverage = _placement_violations(
                        spec,
                        candidate,
                        points,
                        view_ends,
                        frame,
                        placed_boxes=placed_boxes,
                        placed_leaders=placed_leaders,
                        occupied_shapes=occupied,
                        font_size=font_size,
                        margin=margin,
                    )
                    score = violations * 1000 + coverage * 5
                    if score < best_score:
                        best = candidate
                        best_score = score
                    if score == 0:
                        break
                if best_score == 0:
                    break
            chosen = best
        if chosen is None:
            candidates = (
                _LabelPlacement(primary, 0.0),
                _LabelPlacement(other, 0.0),
                _LabelPlacement(primary, step),
                _LabelPlacement(other, step),
                _LabelPlacement(primary, -step),
                _LabelPlacement(other, -step),
            )
            fallback: _LabelPlacement | None = None
            fallback_violations = 1 << 30
            for candidate in candidates:
                violations, _coverage = _placement_violations(
                    spec,
                    candidate,
                    points,
                    view_ends,
                    frame,
                    placed_boxes=placed_boxes,
                    placed_leaders=placed_leaders,
                    occupied_shapes=occupied,
                    font_size=font_size,
                    margin=margin,
                )
                if violations == 0:
                    chosen = candidate
                    break
                if violations < fallback_violations:
                    fallback = candidate
                    fallback_violations = violations
            chosen = chosen or fallback or _LabelPlacement(primary, 0.0)
        placements.append(chosen)
        boxes, leaders = _placement_geometry(
            spec,
            chosen,
            points,
            view_ends,
            frame,
            font_size=font_size,
            margin=margin,
        )
        for view_index, box in enumerate(boxes):
            placed_boxes[view_index].append(box)
            placed_leaders[view_index].append(leaders[view_index])
    return placements


def _label_name(spec: _LabelSpec) -> str:
    if spec.kind == "marker":
        return f"marker {spec.position} ({spec.text})"
    if spec.kind == "river":
        return f"river label {spec.text!r}"
    if spec.kind == "region":
        return f"region label {spec.text!r}"
    return "focus label"


def _label_problems(
    specs: Sequence[_LabelSpec],
    placements: Sequence[_LabelPlacement],
    view: View,
    moves: Sequence[tuple[float, float, float, float]],
    frame: Rect4,
    *,
    font_size: float,
    margin: float,
    scene_id: str,
    seconds: float,
) -> list[str]:
    """Outside/overlap problems left after the layout attempts."""
    view_ends = _view_ends(moves, seconds)
    boxes_by_spec: list[list[Rect4 | None]] = []
    leaders_by_spec: list[list[tuple[tuple[float, float], tuple[float, float]] | None]] = []
    for spec, placement in zip(specs, placements, strict=True):
        points = _label_points(spec, moves, frame)
        boxes, leaders = _placement_geometry(
            spec,
            placement,
            points,
            view_ends,
            frame,
            font_size=font_size,
            margin=margin,
        )
        boxes_by_spec.append(boxes)
        leaders_by_spec.append(leaders)
    problems: list[str] = []
    for view_index in range(len(view_ends)):
        location = "at the base view" if view_index == 0 else f"after move {view_index - 1}"
        for spec, boxes in zip(specs, boxes_by_spec, strict=True):
            box = boxes[view_index]
            if box is None or not _outside(box, frame):
                continue
            lead = _label_name(spec)
            if spec.kind == "marker":
                lead += " label"
            if view_index == 0:
                problems.append(f"{scene_id}: {lead} outside the map frame {location}")
            else:
                problems.append(f"{scene_id}: {lead} lands outside the map frame {location}")
        for left in range(len(specs)):
            for right in range(left + 1, len(specs)):
                first = boxes_by_spec[left][view_index]
                second = boxes_by_spec[right][view_index]
                if first is None or second is None or not _overlap(first, second):
                    continue
                problems.append(
                    f"{scene_id}: labels {_label_name(specs[left])} and "
                    f"{_label_name(specs[right])} overlap {location}"
                )
        for callout_index, leader in enumerate(leaders_by_spec):
            if leader is None:
                continue
            callout_leader = leader[view_index]
            if callout_leader is None:
                continue
            for other_index, other in enumerate(specs):
                if other_index == callout_index:
                    continue
                other_box = boxes_by_spec[other_index][view_index]
                if other_box is not None and _segment_hits_box(callout_leader, other_box):
                    problems.append(
                        f"{scene_id}: callout {_label_name(specs[callout_index])} "
                        f"crosses {_label_name(other)} {location}"
                    )
            for other_index in range(callout_index + 1, len(leaders_by_spec)):
                other_leader = leaders_by_spec[other_index][view_index]
                if other_leader is not None and _segments_cross(callout_leader, other_leader):
                    problems.append(
                        f"{scene_id}: callouts {_label_name(specs[callout_index])} and "
                        f"{_label_name(specs[other_index])} cross {location}"
                    )
    return problems


def _label_style(side: str, nudge: float) -> tuple[str, str]:
    """Inline style suffix and data attribute for a vertical label nudge."""
    if abs(nudge) < 0.05:
        return "", ""
    delta = f"+ {nudge:.1f}px" if nudge >= 0.0 else f"- {abs(nudge):.1f}px"
    if side == "left":
        transform = f"translate(-100%, calc(-50% {delta}))"
    else:
        transform = f"translateY(calc(-50% {delta}))"
    return f";transform:{transform}", f' data-label-nudge="{nudge:.1f}"'


def _label_sides(
    markers: Sequence[_ProjectedMarker],
    view: View,
    *,
    gap: float,
    moves: Sequence[tuple[float, float, float, float]] = (),
    font_size: float = 0.0,
    margin: float = 0.0,
    clamp_rows: Sequence[int] | None = None,
) -> list[str]:
    """The side of its pin each marker label is drawn on.

    A label must stay inside the map frame at the base view and after every
    camera landing. Both sides fitting keeps today's choice; otherwise the
    other side is taken when it fits, preferring a side that fits the final
    landing. Two close markers still flip one label to the other side, the
    rule that keeps a short route readable, but only when that side also fits.
    """
    frame = view.frame
    sides: list[str] = []
    placed: list[tuple[float, float, str]] = []
    for position, marker in enumerate(markers):
        points = _track_positions(marker.x, marker.y, moves)
        if clamp_rows is not None and clamp_rows[position] >= 0:
            points = [
                _clamp_frame_point(point, frame, row=clamp_rows[position]) for point in points
            ]
        side = _label_side_for(
            marker.text,
            (marker.x, marker.y),
            points,
            view,
            font_size=font_size,
            margin=margin,
        )
        for other_x, other_y, other_side in placed:
            if abs(marker.y - other_y) < gap and abs(marker.x - other_x) < gap * 4.0:
                flipped = "left" if other_side == "right" else "right"
                if _label_fits(
                    marker.text,
                    flipped,
                    points,
                    frame,
                    font_size=font_size,
                    margin=margin,
                ):
                    side = flipped
                break
        placed.append((marker.x, marker.y, side))
        sides.append(side)
    return sides


def _marker_labels(
    markers: Sequence[_ProjectedMarker],
    view: View,
    *,
    gap: float,
    moves: Sequence[tuple[float, float, float, float]] = (),
    font_size: float = 0.0,
    margin: float = 0.0,
    sides: Sequence[str] | None = None,
    nudges: Sequence[float] | None = None,
) -> str:
    """One place label per marker, with a side that keeps it in the frame."""
    if sides is None:
        sides = _label_sides(
            markers,
            view,
            gap=gap,
            moves=moves,
            font_size=font_size,
            margin=margin,
        )
    if nudges is None:
        nudges = [0.0] * len(markers)
    parts: list[str] = []
    for position, (marker, side, nudge) in enumerate(zip(markers, sides, nudges, strict=True)):
        style, attr = _label_style(side, nudge)
        parts.append(
            f'<div class="map-place is-{side}" data-camera-track="marker_{position}"'
            f'{attr} style="left:{marker.x:.1f}px;top:{marker.y:.1f}px{style}">'
            f"{html.escape(marker.text)}</div>"
        )
    return "".join(parts)


def _river_labels(
    rivers: Sequence[_PreparedRiver], sides: Sequence[str], nudges: Sequence[float]
) -> str:
    """One overlay label per labelled river, anchored at its course midpoint."""
    parts: list[str] = []
    side_position = 0
    for position, river in enumerate(rivers):
        anchor = river.label_anchor
        if anchor is None:
            continue
        side = sides[side_position]
        nudge = nudges[side_position]
        style, attr = _label_style(side, nudge)
        side_position += 1
        parts.append(
            f'<div class="map-place is-{side}" data-camera-track="river_{position}"'
            f'{attr} style="left:{anchor.x:.1f}px;top:{anchor.y:.1f}px{style}">'
            f"{html.escape(river.label)}</div>"
        )
    return "".join(parts)


def _region_labels(
    regions: Sequence[_PreparedRegion], sides: Sequence[str], nudges: Sequence[float]
) -> str:
    """One overlay label per labelled region, anchored inside its largest polygon."""
    parts: list[str] = []
    side_position = 0
    for position, region in enumerate(regions):
        anchor = region.label_anchor
        if anchor is None:
            continue
        side = sides[side_position]
        nudge = nudges[side_position]
        style, attr = _label_style(side, nudge)
        side_position += 1
        parts.append(
            f'<div class="map-place is-{side}" data-camera-track="region_{position}"'
            f'{attr} style="left:{anchor.x:.1f}px;top:{anchor.y:.1f}px{style}">'
            f"{html.escape(region.label)}</div>"
        )
    return "".join(parts)


def _callout_markup(
    spec: _LabelSpec,
    placement: _LabelPlacement,
    index: int,
    *,
    left: float,
    top: float,
    font_size: float,
    ink: str,
    reveal_seconds: float,
) -> tuple[str, str]:
    """One HTML callout group: dot, drawing leader line, and offset label."""
    width, height = _label_size(spec.text, font_size)
    offset_x, offset_y = placement.offset
    box = (offset_x - width / 2.0, offset_y - height / 2.0, width, height)
    end = _nearest_box_point((0.0, 0.0), box)
    length = max(1.0, math.hypot(end[0], end[1]))
    angle = math.degrees(math.atan2(end[1], end[0]))
    line_delay = max(0.0, spec.delay - 0.25)
    key = spec.key
    name_line = f"map_callout_line_{index}_{key}"
    name_dot = f"map_callout_dot_{index}_{key}"
    name_label = f"map_callout_label_{index}_{key}"
    markup = (
        f'<div class="map-callout" data-camera-track="callout_{key}" '
        f'style="left:{left:.1f}px;top:{top:.1f}px">'
        f'<span class="map-callout-line" style="width:{length:.1f}px;'
        f"transform:rotate({angle:.2f}deg);"
        f'animation:{name_line} 0.300s ease-out {line_delay:.3f}s both"></span>'
        f'<span class="map-callout-dot" style="animation:{name_dot} '
        f'0.200s ease-out {line_delay:.3f}s both"></span>'
        f'<div class="map-place map-callout-label" style="left:{offset_x:.1f}px;'
        f"top:{offset_y:.1f}px;transform:translate(-50%,-50%);"
        f'animation:{name_label} {reveal_seconds:.3f}s ease-out {spec.delay:.3f}s both">'
        f"{html.escape(spec.text)}</div></div>"
    )
    css = f"""      .map-{index} .map-callout {{ position:absolute; width:0; height:0; }}
      .map-{index} .map-callout-line {{ position:absolute; left:0; top:0;
        height:1.5px; background:{ink}; opacity:0.7; transform-origin:0 50%; }}
      .map-{index} .map-callout-dot {{ position:absolute; left:0; top:0;
        width:5px; height:5px; border-radius:50%; background:{ink};
        transform:translate(-50%,-50%); }}
      .map-{index} .map-callout-label {{ position:absolute; white-space:nowrap; }}
      @keyframes {name_line} {{
        from {{ width:0; }}
        to {{ width:{length:.1f}px; }}
      }}
      @keyframes {name_dot} {{
        from {{ opacity:0; transform:translate(-50%,-50%) scale(.4); }}
        to {{ opacity:0.7; transform:translate(-50%,-50%) scale(1); }}
      }}
      @keyframes {name_label} {{
        from {{ opacity:0; }}
        to {{ opacity:1; }}
      }}
"""
    return markup, css


def _labels_markup(
    specs: Sequence[_LabelSpec],
    placements: Sequence[_LabelPlacement],
    *,
    index: int,
    font_size: float,
    ink: str,
    reveal_seconds: float,
) -> tuple[str, str]:
    """Markup and conditional CSS for every overlay label."""
    parts: list[str] = []
    callout_css: list[str] = []
    centre_css = ""
    order = [position for position, spec in enumerate(specs) if spec.kind == "place"]
    order += [position for position, spec in enumerate(specs) if spec.kind != "place"]
    for position in order:
        spec = specs[position]
        placement = placements[position]
        if spec.view_points is not None:
            first = next(
                (point for point in spec.view_points if point is not None),
                (spec.x, spec.y),
            )
        else:
            first = (spec.x, spec.y)
        if placement.mode == "callout":
            markup, css = _callout_markup(
                spec,
                placement,
                index,
                left=first[0],
                top=first[1],
                font_size=font_size,
                ink=ink,
                reveal_seconds=reveal_seconds,
            )
            parts.append(markup)
            callout_css.append(css)
            continue
        if placement.mode == "centre":
            centre_css = (
                f"      .map-{index} .map-place.is-center {{ transform:translate(-50%,-50%); }}\n"
            )
            parts.append(
                f'<div class="map-place is-center" data-camera-track="{spec.key}" '
                f'style="left:{first[0]:.1f}px;top:{first[1]:.1f}px;'
                f'transform:translate(-50%,-50%)">{html.escape(spec.text)}</div>'
            )
            continue
        style, attr = _label_style(placement.side, placement.nudge)
        parts.append(
            f'<div class="map-place is-{placement.side}" '
            f'data-camera-track="{spec.key}"{attr} '
            f'style="left:{first[0]:.1f}px;top:{first[1]:.1f}px{style}">'
            f"{html.escape(spec.text)}</div>"
        )
    return "".join(parts), centre_css + "".join(callout_css)


def _map_css(
    index: int,
    colors: Mapping[str, str],
    *,
    display: str,
    body: str,
    geom: _Geom,
    scale: float,
    entry: float,
    seconds: float,
    stage_motion: str,
    route: _ProjectedRoute | None,
    camera_moves: Sequence[tuple[float, float, float, float]] = (),
    move_seconds: float = 0.0,
    tracks: Sequence[_Track] = (),
    stutter: float | None = None,
    fps: int = DEFAULT_FPS,
    route_dot: bool = False,
    rivers: Sequence[_PreparedRiver] = (),
    regions: Sequence[_PreparedRegion] = (),
    journey_css: str = "",
    journey_animations: Sequence[str] = (),
) -> str:
    """Every selector scoped to ``.map-{index}``; no global CSS."""

    def px(value: float) -> str:
        return f"{value * scale:.1f}px"

    def px_int(value: float) -> str:
        """A hairline that never disappears on a small canvas."""
        return f"{max(1, round(value * scale))}px"

    map_x = geom.map_frame[0] * scale
    map_y = geom.map_frame[1] * scale
    map_w = geom.map_frame[2] * scale
    map_h = geom.map_frame[3] * scale
    cards_left, cards_top, cards_width = geom.cards
    title_left, title_top, title_width = geom.title
    plate_w = geom.width * scale
    plate_h = geom.height * scale
    camera_css = _camera_css(index, seconds, camera_moves, move_seconds=move_seconds)
    track_css = _track_css(
        index,
        seconds,
        camera_moves,
        move_seconds=move_seconds,
        tracks=tracks,
        frame=(map_x, map_y, map_w, map_h),
        view_ends=_view_ends(camera_moves, seconds),
        view_starts=_view_starts(camera_moves, move_seconds),
        stutter=stutter,
        fps=fps,
    )
    camera_animation = (
        f"animation: map_camera_{index} {seconds:.3f}s linear both;" if camera_moves else ""
    )
    clip_inset = (
        f"inset({map_y:.1f}px {max(0.0, plate_w - map_x - map_w):.1f}px "
        f"{max(0.0, plate_h - map_y - map_h):.1f}px {map_x:.1f}px)"
    )

    route_css = ""
    ghost_animations = [f"map_map_in_{index} {entry * 1.6:.3f}s ease-out both"]
    if route is not None:
        # The draw length is bounded by the scene, not the path: a long route
        # on a short scene must still finish before the card.
        draw = min(2.4, max(0.8, seconds * 0.28))
        width_css = _route_width_css(
            index,
            seconds,
            camera_moves,
            move_seconds=move_seconds,
            base=ROUTE_STROKE * scale,
        )
        draw_ease = (
            "ease-in-out" if stutter is None else _steps_timing(draw, stutter=stutter, fps=fps)
        )
        route_css = f"""      @property --map-route-draw-{index} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @property --map-route-width-{index} {{
        syntax: '<number>'; inherits: true; initial-value: {ROUTE_STROKE * scale:g}; }}
      .map-{index} .map-route {{
        stroke-dashoffset: var(--map-route-draw-{index}, {route.length:.1f});
        stroke-width: var(--map-route-width-{index}, {ROUTE_STROKE * scale:g}); }}
      @keyframes map_route_{index} {{
        from {{ --map-route-draw-{index}: {route.length:.1f}; }}
        to {{ --map-route-draw-{index}: 0; }}
      }}
{width_css}"""
        ghost_animations.append(
            f"map_route_{index} {draw:.3f}s {draw_ease} {route.delay:.3f}s both"
        )
        if camera_moves:
            ghost_animations.append(f"map_route_width_{index} {seconds:.3f}s linear 0s both")
        if route_dot:
            # The dot rides the route's own path, on the draw's clock, then
            # fades once it arrives. Both properties are driven from the HTML
            # wrapper so HyperFrames' seek reaches them.
            fade = 0.3
            fade_seconds = draw + fade
            arrive_pct = draw / fade_seconds * 100.0
            route_css += f"""      @property --map-route-dot-{index} {{
        syntax: '<percentage>'; inherits: true; initial-value: 0%; }}
      @property --map-route-dot-fade-{index} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      .map-{index} .map-route-dot {{
        offset-path: path("{route.path}");
        offset-distance: var(--map-route-dot-{index}, 0%);
        offset-rotate: 0deg;
        opacity: var(--map-route-dot-fade-{index}, 0); }}
      @keyframes map_route_dot_{index} {{
        from {{ --map-route-dot-{index}: 0%; }}
        to {{ --map-route-dot-{index}: 100%; }}
      }}
      @keyframes map_route_dot_fade_{index} {{
        0% {{ --map-route-dot-fade-{index}: 0; }}
        8% {{ --map-route-dot-fade-{index}: 1; }}
        {arrive_pct:.3f}% {{ --map-route-dot-fade-{index}: 1; }}
        100% {{ --map-route-dot-fade-{index}: 0; }}
      }}
"""
            ghost_animations.append(
                f"map_route_dot_{index} {draw:.3f}s {draw_ease} {route.delay:.3f}s both"
            )
            ghost_animations.append(
                f"map_route_dot_fade_{index} {fade_seconds:.3f}s linear {route.delay:.3f}s both"
            )
    river_css = ""
    if rivers:
        draw = min(2.4, max(0.8, seconds * 0.28))
        river_ease = (
            "ease-in-out" if stutter is None else _steps_timing(draw, stutter=stutter, fps=fps)
        )
        blocks: list[str] = [
            f"      .map-{index} .map-river {{ stroke: {colors['river']}; }}\n"
            f"      .map-{index} .map-river-flow "
            f"{{ stroke: {RIVER_FLOW_COLOR}; }}"
        ]
        for river_position, river in enumerate(rivers):
            for piece_position, piece in enumerate(river.pieces):
                draw_var = f"--map-river-draw-{index}-{river_position}-{piece_position}"
                flow_var = f"--map-river-flow-{index}-{river_position}-{piece_position}"
                on_var = f"--map-river-flow-on-{index}-{river_position}-{piece_position}"
                blocks.append(
                    f"""      @property {draw_var} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @keyframes map_river_draw_{index}_{river_position}_{piece_position} {{
        from {{ {draw_var}: {piece.length:.1f}; }}
        to {{ {draw_var}: 0; }}
      }}"""
                )
                ghost_animations.append(
                    f"map_river_draw_{index}_{river_position}_{piece_position} "
                    f"{draw:.3f}s {river_ease} {river.delay:.3f}s both"
                )
                if not river.flow:
                    continue
                flow_start = river.delay + draw
                flow_seconds = max(0.15, seconds - flow_start)
                flow_distance = (
                    (RIVER_FLOW_DASH + RIVER_FLOW_GAP) * scale * max(1, math.ceil(flow_seconds))
                )
                blocks.append(
                    f"""      @property {flow_var} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @property {on_var} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @keyframes map_river_flow_{index}_{river_position}_{piece_position} {{
        from {{ {flow_var}: 0; }}
        to {{ {flow_var}: -{flow_distance:.1f}; }}
      }}
      @keyframes map_river_flow_on_{index}_{river_position}_{piece_position} {{
        from {{ {on_var}: 0; }}
        to {{ {on_var}: 1; }}
      }}"""
                )
                ghost_animations.append(
                    f"map_river_flow_{index}_{river_position}_{piece_position} "
                    f"{flow_seconds:.3f}s linear {flow_start:.3f}s both"
                )
                ghost_animations.append(
                    f"map_river_flow_on_{index}_{river_position}_{piece_position} "
                    f"0.200s linear {flow_start:.3f}s both"
                )
        if any(river.label_anchor is not None for river in rivers):
            blocks.append(
                f"      @keyframes map_river_label_{index} {{ "
                "from { opacity:0; } to { opacity:1; } }"
            )
        river_css = "\n".join(blocks) + "\n"
    region_css = ""
    if regions:
        draw = min(1.6, max(0.7, seconds * 0.18))
        region_ease = (
            "ease-in-out" if stutter is None else _steps_timing(draw, stutter=stutter, fps=fps)
        )
        fill_seconds = 0.4
        fill_ease = (
            "ease-out" if stutter is None else _steps_timing(fill_seconds, stutter=stutter, fps=fps)
        )
        region_blocks: list[str] = []
        for region_position, region in enumerate(regions):
            fill_var = f"--map-region-fill-{index}-{region_position}"
            region_blocks.append(
                f"""      @property {fill_var} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @keyframes map_region_fill_{index}_{region_position} {{
        from {{ {fill_var}: 0; }}
        to {{ {fill_var}: 1; }}
      }}"""
            )
            ghost_animations.append(
                f"map_region_fill_{index}_{region_position} {fill_seconds:.3f}s "
                f"{fill_ease} {region.delay + draw:.3f}s both"
            )
            for polygon_position, shape in enumerate(region.shapes):
                draw_var = f"--map-region-draw-{index}-{region_position}-{polygon_position}"
                region_blocks.append(
                    f"""      @property {draw_var} {{
        syntax: '<number>'; inherits: true; initial-value: 0; }}
      @keyframes map_region_draw_{index}_{region_position}_{polygon_position} {{
        from {{ {draw_var}: {shape.length:.1f}; }}
        to {{ {draw_var}: 0; }}
      }}"""
                )
                ghost_animations.append(
                    f"map_region_draw_{index}_{region_position}_{polygon_position} "
                    f"{draw:.3f}s {region_ease} {region.delay:.3f}s both"
                )
        if any(region.label_anchor is not None for region in regions):
            region_blocks.append(
                f"      @keyframes map_region_label_{index} {{ "
                "from { opacity:0; } to { opacity:1; } }"
            )
        region_css = "\n".join(region_blocks) + "\n"
    if journey_animations:
        ghost_animations.extend(journey_animations)
        if route is None and camera_moves:
            # Legs share the route's zoom-compensated stroke width.
            route_css += _route_width_css(
                index,
                seconds,
                camera_moves,
                move_seconds=move_seconds,
                base=ROUTE_STROKE * scale,
            )
            route_css += (
                f"      @property --map-route-width-{index} {{ syntax: '<number>'; "
                f"inherits: true; initial-value: {ROUTE_STROKE * scale:g}; }}\n"
            )
            ghost_animations.append(f"map_route_width_{index} {seconds:.3f}s linear 0s both")
    # Journey, bubble and sticker rules ride along whether or not legs exist.
    route_css += journey_css
    glow = str(colors.get("glow") or "")
    if glow:
        # A neon halo on coastlines, borders and journey legs.
        route_css += (
            f"      .map-{index} .map-ghost path, .map-{index} .map-subject path,\n"
            f"      .map-{index} .map-region-outline,\n"
            # The halo sits in the zoomed layer: scale its blur by the same
            # zoom compensation as the strokes so it never swells.
            f"      .map-{index} .map-leg {{ filter: drop-shadow(0 0 calc({4.0 * scale:.1f}px * "
            f"var(--map-route-width-{index}, {ROUTE_STROKE * scale:g}) / {ROUTE_STROKE * scale:g}) "
            f"{glow}); }}\n"
        )
    ghost_animation = ", ".join(ghost_animations)

    # The drift keyframes come from the registry so two scenes with the same
    # parameters share one ``@keyframes`` block.
    return f"""      {stage_motion}
{paper_css(f"map-{index}", "map", colors, scale)}      .map-{index} {{ position:absolute; inset:0; background:{colors["paper"]};
        overflow:hidden; }}
      .map-{index} .map-plate {{ position:absolute; overflow:visible; }}
      .map-{index} .map-stage {{ position:absolute; inset:0; transform-origin:center;
         }}
      .map-{index} .map-window {{ position:absolute; inset:0; }}
      .map-{index} .map-fade {{ position:absolute;
        left:{map_x:.1f}px; top:{map_y:.1f}px;
        width:{map_w:.1f}px; height:{map_h:.1f}px;
        -webkit-mask-image:
          linear-gradient(to bottom, transparent, #000 9%, #000 91%, transparent),
          linear-gradient(to right, transparent, #000 9%, #000 91%, transparent);
        -webkit-mask-composite: source-in;
        mask-image:
          linear-gradient(to bottom, transparent, #000 9%, #000 91%, transparent),
          linear-gradient(to right, transparent, #000 9%, #000 91%, transparent);
        mask-composite: intersect; }}
      .map-{index} .map-camera {{ position:absolute;
        left:{-map_x:.1f}px; top:{-map_y:.1f}px;
        width:{plate_w:.1f}px; height:{plate_h:.1f}px;
        transform-origin:0 0;
         {camera_animation} }}
{camera_css}      .map-{index} .map-overlay {{ position:absolute; inset:0;
        -webkit-clip-path: {clip_inset};
        clip-path: {clip_inset}; }}
      .map-{index} .map-ghost-wrap {{ position:absolute;
        left:{map_x:.1f}px; top:{map_y:.1f}px;
        width:{map_w:.1f}px; height:{map_h:.1f}px;
        opacity:0; animation: {ghost_animation}; }}
      .map-{index} .map-svg {{ position:absolute; left:{-map_x:.1f}px; top:{-map_y:.1f}px; }}
      @keyframes map_map_in_{index} {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
      .map-{index} .map-pin-point {{ position:absolute; width:0; height:0;
        transform-origin:center; }}
      .map-{index} .map-pin-point span {{ position:absolute; left:0; top:0;
        border-radius:50%; transform:translate(-50%,-50%); }}
      .map-{index} .map-pin-halo {{ width:{px(52)}; height:{px(52)};
        background:{colors["pin"]}; opacity:.16; }}
      .map-{index} .map-pin-ring {{ width:{px(26)}; height:{px(26)};
        box-sizing:border-box; background:{colors["pin_ring"]};
        border:{px_int(3.2)} solid {colors["pin"]}; }}
      .map-{index} .map-pin-dot {{ width:{px(10.8)}; height:{px(10.8)};
        background:{colors["pin"]}; }}
      @keyframes map_pin_{index} {{
        from {{ opacity:0; transform:scale(.55); }}
        to {{ opacity:1; transform:scale(1); }}
      }}
      .map-{index} .map-marker {{ position:absolute; width:0; height:0; }}
      .map-{index} .map-marker span {{ position:absolute; left:0; top:0;
        border-radius:50%; transform:translate(-50%,-50%); }}
      .map-{index} .map-marker-halo {{ width:{px(48)}; height:{px(48)};
        background:{colors["pin"]}; opacity:.14; }}
      .map-{index} .map-marker-ring {{ width:{px(20)}; height:{px(20)};
        box-sizing:border-box; background:{colors["pin_ring"]};
        border:{px_int(3.0)} solid {colors["pin"]}; }}
      .map-{index} .map-marker-dot {{ width:{px(8.4)}; height:{px(8.4)};
        background:{colors["pin"]}; }}
      @keyframes map_marker_{index} {{ from {{ opacity:0; }} to {{ opacity:1; }} }}
{region_css}{river_css}{route_css}      .map-{index} .map-place {{ position:absolute; transform:translateY(-50%);
        font-family:{body}; font-size:{px(geom.place)}; font-weight:700;
        letter-spacing:{LABEL_TRACKING}em; color:{colors["label"]}; white-space:nowrap;
        text-shadow:0 {px(1)} 0 {colors["paper_light"]}, 0 {px(4)} {px(14)} {colors["paper"]};
        opacity:0; animation: map_place_{index} {entry * 1.2:.3f}s ease-out
          {entry * 1.5:.3f}s both; }}
      .map-{index} .map-place.is-right {{ margin-left:{px(LABEL_MARGIN)}; }}
      .map-{index} .map-place.is-left {{ transform:translate(-100%,-50%);
        margin-left:{px(-LABEL_MARGIN)}; text-align:right; }}
      @keyframes map_place_{index} {{
        from {{ opacity:0; }} to {{ opacity:1; }}
      }}
{track_css}      .map-{index} .map-badge {{ position:absolute;
        left:{px(geom.badge[0])}; top:{px(geom.badge[1])}; display:flex;
        align-items:center; gap:{px(geom.badge_gap)}; opacity:0;
        animation: map_badge_{index} {entry * 1.3:.3f}s ease-out both; }}
      @keyframes map_badge_{index} {{
        from {{ opacity:0; transform:translateX({px(-14)}); }}
        to {{ opacity:1; transform:translateX(0); }}
      }}
      .map-{index} .map-badge-disc {{ width:{px(geom.badge_disc)};
        height:{px(geom.badge_disc)};
        border-radius:50%; border:{px_int(1.6)} solid {colors["highlight"]};
        background:{colors["paper_light"]}; display:flex; align-items:center;
        justify-content:center; font-family:{display}; font-weight:600;
        font-size:{px(geom.badge_initial)}; color:{colors["highlight"]}; }}
      .map-{index} .map-badge-text {{ line-height:1.2; }}
      .map-{index} .map-badge-name {{ font-family:{body}; font-size:{px(geom.badge_name)};
        font-weight:700; letter-spacing:.20em; text-transform:uppercase;
        color:{colors["highlight"]}; }}
      .map-{index} .map-badge-sub {{ font-family:{body}; font-size:{px(geom.badge_sub)};
        letter-spacing:.30em; text-transform:uppercase; color:{colors["muted"]};
        margin-top:{px(4)}; }}
      .map-{index} .map-cards {{ position:absolute; left:{px(cards_left)};
        top:{px(cards_top)}; width:{px(cards_width)}; display:flex;
        flex-direction:column; gap:{px(geom.cards_gap)}; }}
      .map-{index} .map-card {{ display:flex; align-items:center; gap:{px(22)};
        height:{px(geom.card_height)}; padding:0 {px(geom.card_pad)} 0 0;
        background:{colors["card_fill"]};
        border:{px_int(1.2)} solid {colors["card_border"]};
        border-left:0; box-shadow:inset {px_int(4)} 0 0 {colors["highlight"]},
          0 {px(1)} {px(3)} rgba(40,30,10,0.06);
        box-sizing:border-box; opacity:0;
        animation: map_card_{index} {entry * 1.3:.3f}s ease-out both; }}
      .map-{index} .map-card-num {{ font-family:{display}; font-size:{px(geom.card_num)};
        font-weight:600; color:{colors["gold_ink"]}; width:{px(geom.card_num_width)};
        text-align:right; }}
      .map-{index} .map-card-rule {{ width:{px_int(2)};
        align-self:stretch; margin:{px(geom.card_rule_margin)} 0;
        background:{colors["card_border"]}; }}
      .map-{index} .map-card-text {{ font-family:{body}; font-size:{px(geom.card_text)};
        font-weight:600; letter-spacing:.10em; text-transform:uppercase;
        color:{colors["ink"]}; }}
      @keyframes map_card_{index} {{
        from {{ opacity:0; transform:translate3d({px(26)},0,0); }}
        to {{ opacity:1; transform:translate3d(0,0,0); }}
      }}
      .map-{index} .map-title {{ position:absolute; left:{px(title_left)};
        top:{px(title_top)}; width:{px(title_width)}; }}
      .map-{index} .map-title-rule {{ width:{px(geom.rule[0])}; height:{px_int(geom.rule[1])};
        background:{colors["rule"]}; opacity:0;
        animation: map_title_rule_{index} {entry * 1.4:.3f}s ease-out both; }}
      @keyframes map_title_rule_{index} {{
        from {{ opacity:0; transform:scaleX(.2); }} to {{ opacity:1; transform:scaleX(1); }}
      }}
      .map-{index} .map-eyebrow {{ margin-top:{px(TITLE_EYEBROW_MARGIN)}; font-family:{body};
        font-size:{px(geom.eyebrow)}; line-height:{TITLE_LINE_HEIGHT}; font-weight:600;
        letter-spacing:.34em; text-transform:uppercase; color:{colors["eyebrow"]}; opacity:0;
        animation: map_rise_{index} {entry:.3f}s ease-out {entry * 0.25:.3f}s both; }}
      .map-{index} .map-headline {{ margin-top:{px(TITLE_HEADLINE_MARGIN)}; font-family:{display};
        font-size:{px(geom.headline)}; font-weight:600; line-height:1; letter-spacing:-0.005em;
        padding-bottom:0.22em;
        color:{colors["ink"]}; opacity:0;
        display:-webkit-box; -webkit-box-orient:vertical;
        -webkit-line-clamp:{TITLE_MAX_LINES}; overflow:hidden;
        animation: map_rise_{index} {entry * 1.15:.3f}s ease-out {entry * 0.4:.3f}s both; }}
      .map-{index} .map-subtitle {{ margin-top:{px(TITLE_SUBTITLE_MARGIN)}; font-family:{body};
        font-size:{px(geom.subtitle)}; line-height:{TITLE_LINE_HEIGHT}; font-weight:500;
        letter-spacing:.30em; text-transform:uppercase; color:{colors["muted"]}; opacity:0;
        display:-webkit-box; -webkit-box-orient:vertical;
        -webkit-line-clamp:{TITLE_MAX_LINES}; overflow:hidden;
        animation: map_rise_{index} {entry:.3f}s ease-out {entry * 0.6:.3f}s both; }}
      @keyframes map_rise_{index} {{
        from {{ opacity:0; transform:translate3d(0,{px(18)},0); }}
        to {{ opacity:1; transform:translate3d(0,0,0); }}
      }}
      .map-{index} .map-mark {{ position:absolute; left:{px(geom.mark[0])};
        top:{px(geom.mark[1])}; font-family:{body}; font-size:{px(MARK_SIZE)}; font-weight:600;
        letter-spacing:.40em; text-transform:uppercase; color:{colors["muted"]};
        opacity:.6; }}
"""
