"""Platform safe areas: keep text clear of the edges and of app UI.

On TikTok, Shorts and Reels the app draws the username, description and
action buttons over the bottom ~20% of a 9:16 video, so captions placed
there are hidden. Landscape TV/YouTube frames get the classic 5% title-safe
margin. This module gives every renderer and QA the same answer:

* :func:`safe_insets` / :func:`safe_box` — the safe area for a canvas.
* :func:`fit_box` — move (and, if needed, shrink) a box so it lies inside.
* :func:`safe_area_violations` — text layers of a render graph that do not.

Boxes are plain ``(x, y, width, height)`` tuples so both the render-graph
``Rect`` and the graphics-plan ``Rect`` can use them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from video_editor_core.render_graph import Layer

if TYPE_CHECKING:
    from video_editor_core.composition import Composition

Box = tuple[int, int, int, int]


class SafeInsets(NamedTuple):
    """Fractions of the canvas to keep clear on each side."""

    top: float
    right: float
    bottom: float
    left: float


# Vertical short-form: the bottom 20% holds the caption/username bar, the top
# 8% the status bar and tabs, and the sides the action-button rail.
VERTICAL_INSETS = SafeInsets(top=0.08, right=0.08, bottom=0.20, left=0.08)
# Landscape and square: the broadcast title-safe margin.
TITLE_SAFE_INSETS = SafeInsets(top=0.05, right=0.05, bottom=0.05, left=0.05)

# A canvas counts as vertical when it is clearly taller than wide (4:5 and
# 9:16 both qualify; 1:1 does not).
_VERTICAL_RATIO = 1.2

#: Named profiles. ``"auto"`` (the default everywhere) picks by orientation,
#: which is what every existing caller gets.
PROFILES: Mapping[str, SafeInsets] = MappingProxyType(
    {
        "vertical_short": VERTICAL_INSETS,
        # 220 px top / 480 px bottom at 1080x1920: the Cosmic Saju series'
        # burned-in caption and platform-UI reserve.
        "vertical_short_strict": SafeInsets(top=0.1146, right=0.08, bottom=0.25, left=0.08),
        "title_safe": TITLE_SAFE_INSETS,
    }
)


#: Top of the burned-caption band as a share of the height. Landscape sits low,
#: portrait stays above the Shorts/Reels interface. The explainer pipeline
#: places its captions with these, so map layouts can keep clear of them.
LANDSCAPE_CAPTION_TOP = 0.84
PORTRAIT_CAPTION_TOP = 0.72
#: Top of the Shorts/Reels interface on a portrait frame: with no burned
#: captions, content may extend down to here (landscape keeps caption_top).
SHORTS_UI_SAFE_BOTTOM = 0.80


def caption_top(width: int, height: int) -> int:
    """The top edge (px) of the zone the explainer burns its captions into."""
    return int(height * (LANDSCAPE_CAPTION_TOP if width > height else PORTRAIT_CAPTION_TOP))


def content_bottom(width: int, height: int, *, captions_burned: bool = True) -> int:
    """The lowest edge (px) free content may reach.

    Burned captions (the default) own the band from :func:`caption_top`. Without
    them a portrait frame is only limited by the Shorts UI
    (:data:`SHORTS_UI_SAFE_BOTTOM`); landscape is unchanged.
    """
    if captions_burned or width > height:
        return caption_top(width, height)
    return int(height * SHORTS_UI_SAFE_BOTTOM)


def safe_insets(width: int, height: int, *, profile: str = "auto") -> SafeInsets:
    """Return the safe-area insets for a ``width`` x ``height`` canvas.

    ``profile="auto"`` (or ``""``) picks by orientation; any other name must
    be a key of :data:`PROFILES`.
    """
    if profile in {"", "auto"}:
        return VERTICAL_INSETS if height >= width * _VERTICAL_RATIO else TITLE_SAFE_INSETS
    try:
        return PROFILES[profile]
    except KeyError:
        raise ValueError(
            f"unknown safe-area profile {profile!r}; known: {sorted(PROFILES)}"
        ) from None


def safe_box(width: int, height: int, *, profile: str = "auto") -> Box:
    """Return the safe area of a canvas as ``(x, y, width, height)``."""
    insets = safe_insets(width, height, profile=profile)
    left = round(width * insets.left)
    top = round(height * insets.top)
    right = width - round(width * insets.right)
    bottom = height - round(height * insets.bottom)
    return left, top, right - left, bottom - top


def fit_box(box: Box, width: int, height: int, *, profile: str = "auto") -> Box:
    """Return ``box`` moved, and shrunk only if it is too big, into the safe area.

    A box already inside is returned unchanged. A full-width caption band
    becomes the safe width, centred text stays centred.
    """
    sx, sy, sw, sh = safe_box(width, height, profile=profile)
    x, y, w, h = box
    w = min(w, sw)
    h = min(h, sh)
    x = min(max(x, sx), sx + sw - w)
    y = min(max(y, sy), sy + sh - h)
    return x, y, w, h


def is_inside(box: Box, width: int, height: int, *, profile: str = "auto") -> bool:
    """Return True when ``box`` lies entirely inside the safe area."""
    return fit_box(box, width, height, profile=profile) == box


def safe_area_violations(
    layers: Iterable[Layer], width: int, height: int, *, profile: str = "auto"
) -> list[dict[str, object]]:
    """List the text layers whose rect leaves the safe area.

    Each entry names the layer, its rect and the safe area, so QA and logs can
    say exactly what to move.
    """
    safe = safe_box(width, height, profile=profile)
    out: list[dict[str, object]] = []
    for layer in layers:
        if layer.kind != "text":
            continue
        box = (layer.rect.x, layer.rect.y, layer.rect.width, layer.rect.height)
        if not is_inside(box, width, height, profile=profile):
            out.append({"name": layer.name, "rect": box, "safe": safe, "text": layer.text[:40]})
    return out


def composition_safe_area_violations(
    composition: Composition, *, profile: str = "auto"
) -> list[dict[str, object]]:
    """Text layers of a Composition whose rect leaves the safe area."""
    safe = safe_box(composition.canvas.width, composition.canvas.height, profile=profile)
    out: list[dict[str, object]] = []
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.kind not in {"text", "rich_text"}:
                continue
            rect = layer.rect
            box = (rect.x, rect.y, rect.width, rect.height)
            if not is_inside(
                box, composition.canvas.width, composition.canvas.height, profile=profile
            ):
                out.append({"name": layer.id, "rect": box, "safe": safe})
    return out
