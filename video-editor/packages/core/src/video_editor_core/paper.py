"""The paper plate shared by the editorial scene renderers.

Map scenes and composite scenes draw on the same cream editorial surface: a
warm radial paper fill with faint concentric rings. Keeping the palette and
the ring geometry here means a design change moves both, and a scene type
added later inherits the look for free.

A design declares its plate colours under ``layers.map`` (the original
location) or under a section named after its scene type; the map section is
the fallback so a design written before composite scenes existed still themes
them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from video_editor_core.designs import DesignTokens

#: The cream editorial plate: warm paper, slate subject, saffron counterpoint,
#: gold accents. A design or a scene may override any of these.
DEFAULT_COLORS: dict[str, str] = {
    "paper": "#F3EEE5",
    "paper_light": "#FBF7EE",
    "paper_edge": "#E7DECD",
    "rings": "#DDD0BB",
    "graticule": "#D98C6A",
    "ghost_fill": "#E5DCCB",
    "ghost_stroke": "#FAF6EC",
    "highlight": "#59608A",
    "highlight_stroke": "#FFFFFF",
    "pin": "#B8862F",
    "pin_ring": "#F8EEDC",
    "ink": "#1D2545",
    "muted": "#6E6A63",
    "label": "#1B2340",
    "card_fill": "#FBF8F1",
    "card_border": "#DED4C2",
    "eyebrow": "#B8862F",
    #: Saffron dark enough for gold numerals on paper: ``eyebrow`` is 2.8:1
    #: there, under the 3:1 large-text floor, while this is 3.4:1.
    "gold_ink": "#A9782A",
    "rule": "#C9A15A",
    #: Journey roles. An empty ``route`` draws legs in the pin colour, and an
    #: empty ``glow`` adds no halo, so existing designs keep their look.
    "route": "",
    "vehicle": "#E63946",
    "km_bg": "rgba(10, 30, 28, 0.86)",
    "km_ink": "#E8FFF4",
    "glow": "",
}

#: Ring radii in design units, scaled with the plate.
_RING_RADII = (230.0, 320.0, 420.0, 530.0, 650.0, 790.0)


def colors_for(
    design: DesignTokens,
    props: Mapping[str, Any],
    *,
    section: str = "map",
    overridable: tuple[str, ...] = (),
    aliases: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Defaults, then the design's palette, then the scene's overrides.

    ``section`` names the ``layers`` key a design may use for this scene type
    (e.g. ``"composite"``); the map section is the fallback. ``aliases`` maps a
    scene prop to the colour key it overrides when the two names differ — a
    map or composite scene's ``eyebrow`` prop is display copy, so its colour is
    the separate ``eyebrow_color`` prop. Reading the text as a colour would
    emit an invalid declaration (and a stray apostrophe in the copy would open
    a CSS string and swallow the rest of the stylesheet).
    """
    colors = dict(DEFAULT_COLORS)
    declared = getattr(design.layers, section, None)
    if not isinstance(declared, Mapping):
        declared = getattr(design.layers, "map", None)
    if isinstance(declared, Mapping):
        colors.update(
            {
                str(key): str(value)
                for key, value in declared.items()
                if key in colors and isinstance(value, str) and value.strip()
            }
        )
    for key in overridable:
        override = str(props.get(key) or "").strip()
        if override:
            colors[key] = override
    for prop, key in (aliases or {}).items():
        override = str(props.get(prop) or "").strip()
        if override:
            colors[key] = override
    return colors


def rings_svg(
    prefix: str,
    colors: Mapping[str, str],
    width: float,
    height: float,
    scale: float,
) -> str:
    """The faint concentric rings that give the paper its depth."""
    cx = width * 0.52
    cy = height * 0.44
    circles = "".join(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius * scale:.1f}" fill="none" '
        f'stroke="{colors["rings"]}" stroke-width="1"/>'
        for radius in _RING_RADII
    )
    return (
        # The outer rings deliberately run past the frame; the SVG viewport
        # clips them, which is the intended look. HyperFrames' layout check
        # measures the circles' own geometry, so the wrapper declares the
        # overflow intentional instead of the rings being shrunk.
        f'<svg class="{prefix}-rings-svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" aria-hidden="true" '
        f'data-layout-allow-overflow opacity="0.55">{circles}</svg>'
    )


def paper_layers(
    root: str,
    prefix: str,
    colors: Mapping[str, str],
    width: float,
    height: float,
    scale: float,
) -> str:
    """The paper fill and ring overlay for a scene root such as ``map-2``."""
    return (
        f'<div class="{prefix}-paper"></div>'
        f'<div class="{prefix}-rings">'
        f"{rings_svg(prefix, colors, width, height, scale)}</div>"
    )


def paper_css(root: str, prefix: str, colors: Mapping[str, str], scale: float) -> str:
    """The rules for :func:`paper_layers`, scoped under ``.{root}``."""

    def px(value: float) -> str:
        return f"{value * scale:.1f}px"

    return f"""      .{root} .{prefix}-paper {{ position:absolute; inset:0;
        background:
          radial-gradient(120% 96% at 44% 42%, {colors["paper_light"]} 0%,
            {colors["paper"]} 56%, {colors["paper_edge"]} 100%);
        box-shadow: inset 0 0 {px(180)} {px(40)} rgba(90,70,40,0.10); }}
      .{root} .{prefix}-rings {{ position:absolute; inset:0; }}
      .{root} .{prefix}-rings-svg {{ position:absolute; inset:0; }}
"""


#: A colour an author may type: hex, a plain name, or an rgb/hsl function of numbers.
_SAFE_COLOR = re.compile(
    r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|[a-zA-Z]{3,24}"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.%\s,/]+\)"
)


def safe_css_color(value: str, what: str) -> str:
    """``value`` if it is a plain CSS colour, else ``ValueError``.

    Author colours land in SVG attributes and CSS; anything beyond a colour
    (a quote, ``url(...)``, a ``;``) could break out of the attribute.
    """
    text = value.strip()
    if not _SAFE_COLOR.fullmatch(text):
        raise ValueError(f"{what}: {value!r} is not a colour (use #RRGGBB, a name, or rgb()/hsl())")
    return text
