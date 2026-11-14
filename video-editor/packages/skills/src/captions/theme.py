"""Pick caption colours that read against the backdrop behind them (#341).

A caption preset is designed for dark stills: white text, a gold active word
and a dark drop shadow. On a cream paper plate the same look has almost no
contrast. The explainer measures the caption band of the uncaptioned render
and asks :func:`theme_for_backdrop` for per-line overrides.

Thresholds follow WCAG 2.x: captions are large bold text, so 3:1 is the floor
(``MIN_CAPTION_CONTRAST``); the dark theme's body text clears 4.5:1 on paper.
"""

from __future__ import annotations

from captions.models import CaptionStyle

#: WCAG 2.x minimum for large text (>= 18.66px bold); captions are 48-58px.
MIN_CAPTION_CONTRAST = 3.0
#: Charcoal body text and two amber highlights for light backdrops; the darker
#: amber is used when the lighter one would fall under the contrast floor.
DARK_TEXT = "#1C1917"
DARK_HIGHLIGHTS = ("#B45309", "#7C2D12")
#: Lighter golds for mid-bright backdrops where the preset's body text passes
#: but its darker gold active word falls under the floor.
LIGHT_HIGHLIGHTS = ("#FFD966", "#FFF3C4")


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG relative luminance of ``#RGB`` or ``#RRGGBB``."""
    raw = color.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        raise ValueError(f"not a hex colour: {color!r}")
    r, g, b = (int(raw[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first: float, second: float) -> float:
    """WCAG contrast ratio between two relative luminances (1.0 to 21.0)."""
    light, dark = max(first, second), min(first, second)
    return (light + 0.05) / (dark + 0.05)


def theme_for_backdrop(backdrop: float, style: CaptionStyle) -> dict[str, object]:
    """Style overrides for a caption line over ``backdrop`` luminance.

    Empty when the preset's own text and highlight colours both clear the floor,
    so dark stills keep the designed look. When only the body text passes, a
    lighter highlight is chosen so the active word still clears the floor (else
    it shares the text colour and stands out only by its lift). When the text
    fails, charcoal text with a soft light halo (a dark drop shadow muddies text
    on paper) and an amber active word.
    """
    if contrast_ratio(relative_luminance(style.font_color), backdrop) >= MIN_CAPTION_CONTRAST:
        if (
            contrast_ratio(relative_luminance(style.highlight_color), backdrop)
            >= MIN_CAPTION_CONTRAST
        ):
            return {}
        highlight = next(
            (
                color
                for color in LIGHT_HIGHLIGHTS
                if contrast_ratio(relative_luminance(color), backdrop) >= MIN_CAPTION_CONTRAST
            ),
            style.font_color,
        )
        return {"highlight_color": highlight}
    highlight = next(
        (
            color
            for color in DARK_HIGHLIGHTS
            if contrast_ratio(relative_luminance(color), backdrop) >= MIN_CAPTION_CONTRAST
        ),
        DARK_HIGHLIGHTS[-1],
    )
    return {"font_color": DARK_TEXT, "highlight_color": highlight, "text_effect": "halo"}
