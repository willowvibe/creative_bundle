"""Text that ffmpeg's ``drawtext`` cannot shape, drawn as a PNG and overlaid instead (#322).

``drawtext`` lays glyphs out one by one, so scripts that join letters into conjuncts (Telugu
మంత్ర, Devanagari, Tamil, Arabic, Thai, ...) come out broken even with ``text_shaping=1`` and a
font that covers the script (verified on ffmpeg 6.1 with HarfBuzz). Pillow with libraqm shapes
them correctly, so callers draw such text into a transparent PNG with :func:`render_shaped_text`
and splice it into their filter chain with :func:`linear_graph` and :class:`PngOverlay`.

Latin text keeps using ``drawtext``: :func:`needs_shaping` is true only for complex scripts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

#: Unicode blocks whose letters combine and so need a shaping engine, with the Noto family name.
_COMPLEX_SCRIPTS: tuple[tuple[int, int, str], ...] = (
    (0x0590, 0x05FF, "Hebrew"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic"),
    (0x0900, 0x097F, "Devanagari"),
    (0x0980, 0x09FF, "Bengali"),
    (0x0A00, 0x0A7F, "Gurmukhi"),
    (0x0A80, 0x0AFF, "Gujarati"),
    (0x0B00, 0x0B7F, "Oriya"),
    (0x0B80, 0x0BFF, "Tamil"),
    (0x0C00, 0x0C7F, "Telugu"),
    (0x0C80, 0x0CFF, "Kannada"),
    (0x0D00, 0x0D7F, "Malayalam"),
    (0x0D80, 0x0DFF, "Sinhala"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x1000, 0x109F, "Myanmar"),
    (0x1780, 0x17FF, "Khmer"),
)
_FONT_DIRS = (
    Path(__file__).parent / "fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
)


class ShapedTextError(RuntimeError):
    """No font for the script, or libraqm is unavailable."""


def script_of(text: str) -> str | None:
    """The first complex script in ``text`` (Noto family name), or ``None``."""
    for char in text:
        code = ord(char)
        for low, high, name in _COMPLEX_SCRIPTS:
            if low <= code <= high:
                return name
    return None


def needs_shaping(text: str) -> bool:
    """True when ``text`` contains a script ``drawtext`` would render broken."""
    return script_of(text) is not None


@cache
def font_for(script: str, *, bold: bool = True) -> Path:
    """A Noto font file covering ``script``, bold first when ``bold``."""
    weights = ("Bold", "SemiBold", "Regular") if bold else ("Regular", "Medium", "Bold")
    names = [
        f"Noto{family}{script}-{weight}.{ext}"
        for weight in weights
        for family in ("Sans", "Serif")
        for ext in ("ttf", "otf")
    ]
    if script == "Arabic":
        names += [f"NotoNaskhArabic-{weight}.ttf" for weight in weights]
    for name in names:
        for root in _FONT_DIRS:
            if root.is_dir():
                found = next(root.rglob(name), None)
                if found is not None:
                    return found
    raise ShapedTextError(
        f"no Noto font for {script} text; install fonts-noto (e.g. Noto Sans {script})"
    )


def _rgba(color: str) -> tuple[int, int, int, int]:
    """An ffmpeg-style colour (``white``, ``#RRGGBB``, ``0xRRGGBB``, ``red@0.5``) as RGBA."""
    base, _, alpha = color.partition("@")
    base = "#" + base[2:] if base.lower().startswith("0x") else base
    r, g, b = ImageColor.getrgb(base)[:3]
    a = round(255 * float(alpha)) if alpha else 255
    return r, g, b, max(0, min(255, a))


@dataclass(frozen=True)
class ShapedText:
    """A rendered text PNG and its size in pixels."""

    path: Path
    width: int
    height: int


def render_shaped_text(
    text: str,
    output: Path,
    *,
    font_size: int,
    color: str = "white",
    border_width: int = 0,
    border_color: str = "black",
    font: Path | None = None,
) -> ShapedText:
    """Draw ``text`` with full shaping into a transparent PNG, tightly cropped (plus border)."""
    from PIL import features

    if not features.check("raqm"):
        raise ShapedTextError(
            "Pillow was built without libraqm; complex-script text cannot be shaped"
        )
    script = script_of(text) or "Latin"
    face = ImageFont.truetype(
        str(font or font_for(script)), font_size, layout_engine=ImageFont.Layout.RAQM
    )
    left, top, right, bottom = (int(v) for v in face.getbbox(text, stroke_width=border_width))
    pad = max(2, border_width)
    width, height = right - left + 2 * pad, bottom - top + 2 * pad
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).text(
        (pad - left, pad - top),
        text,
        font=face,
        fill=_rgba(color),
        stroke_width=border_width,
        stroke_fill=_rgba(border_color),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return ShapedText(output, width, height)


@dataclass(frozen=True)
class PngOverlay:
    """One step of a :func:`linear_graph`: overlay a PNG at ``x``/``y`` (ffmpeg expressions)."""

    png: Path
    x: str
    y: str
    enable: str = ""  # an ffmpeg expression such as "between(t,1,3)"; empty = always


def _quoted(path: Path) -> str:
    """A filtergraph-safe single-quoted path."""
    return "'" + str(path).replace("\\", "/").replace("'", "'\\''") + "'"


def linear_graph(steps: Sequence[str | PngOverlay], *, source: str = "", sink: str = "") -> str:
    """Join filters and PNG overlays into one filtergraph that reads ``source`` and writes ``sink``.

    Plain filter strings are chained as usual. A :class:`PngOverlay` loads its PNG with ``movie=``
    and overlays it, so the graph still works with ``-vf`` / ``-filter_script:v`` (leave ``source``
    and ``sink`` empty) or inside a larger ``-filter_complex`` (pass labels such as ``"[base]"``).
    Inside an overlay's ``x``/``y``, ``W``/``H`` are the frame size and ``w``/``h`` the PNG's.
    """
    segments: list[str] = []
    chain: list[str] = []  # filters applied to the current stream
    current = source  # the input label(s) of the segment being built
    for index, step in enumerate(steps):
        if isinstance(step, str):
            chain.append(step)
            continue
        base, png = f"[shp{index}b]", f"[shp{index}t]"
        segments.append(f"{current}{','.join(chain) or 'null'}{base}")
        segments.append(f"movie={_quoted(step.png)}{png}")
        enable = f":enable='{step.enable}'" if step.enable else ""
        chain = [f"overlay=x={step.x}:y={step.y}{enable}"]
        current = f"{base}{png}"
    segments.append(f"{current}{','.join(chain) or 'null'}{sink}")
    return ";".join(segments)
