"""Label AI stills with our own disclosure where Gemini's sparkle sits.

Flow and agy stills carry Google's visible sparkle in the bottom-right corner
(measured on 1376x768 Flow stills: centre ~97 px in from both edges, ~48 px
wide). We do not erase it: it is an AI-provenance mark. Instead a small "AI
image" pill is drawn over that corner, so the frame stays clean and the viewer
is still told which shots are generated. The invisible SynthID mark is
untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from video_editor_asset_ingestion.models import AssetResult, AssetType
from video_editor_core.fonts import FONT_DIR

#: Providers whose stills carry the Gemini sparkle.
LABELLED_PROVIDERS = frozenset({"flow_image", "agy_image"})
DEFAULT_LABEL = "AI image"

#: The mark's geometry on the 768 px short side it was measured on.
_REFERENCE_SHORT_SIDE = 768
_SPARKLE_INSET = 97
_SPARKLE_HALF = 30  # a little past the ~24 px half-width, for its soft edge


def _scale(size: tuple[int, int]) -> float:
    return min(size) / _REFERENCE_SHORT_SIDE


def sparkle_box(size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The region the sparkle occupies on an image of ``size``."""
    width, height = size
    scale = _scale(size)
    cx, cy = width - _SPARKLE_INSET * scale, height - _SPARKLE_INSET * scale
    half = _SPARKLE_HALF * scale
    return round(cx - half), round(cy - half), round(cx + half), round(cy + half)


def label_ai_still(source: Path, output: Path, *, text: str = DEFAULT_LABEL) -> Path:
    """Draw the label pill, centred on the sparkle, into a copy of ``source``."""
    image = Image.open(source).convert("RGB")
    scale = _scale(image.size)
    font = ImageFont.truetype(str(FONT_DIR / "Inter.ttf"), size=max(12, round(22 * scale)))
    font.set_variation_by_axes([600])
    draw = ImageDraw.Draw(image, "RGBA")
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    x0, y0, x1, y1 = sparkle_box(image.size)
    pad_x, pad_y = round(14 * scale), round(10 * scale)
    box_w = max(text_w + 2 * pad_x, x1 - x0)
    box_h = max(text_h + 2 * pad_y, y1 - y0)
    # Right-align the pill to the sparkle's right edge so it never grows off-frame.
    bx1 = x1
    bx0 = bx1 - box_w
    by0 = (y0 + y1) / 2 - box_h / 2
    by1 = by0 + box_h
    draw.rounded_rectangle((bx0, by0, bx1, by1), radius=round(box_h / 2), fill=(12, 14, 18, 215))
    draw.text(
        ((bx0 + bx1) / 2, (by0 + by1) / 2),
        text,
        font=font,
        fill=(255, 255, 255, 235),
        anchor="mm",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def label_ai_stills(
    results: Sequence[AssetResult], out_dir: Path, *, text: str = DEFAULT_LABEL
) -> list[AssetResult]:
    """Labelled copies of the branded stills; every other result is returned as is."""
    labelled: list[AssetResult] = []
    for result in results:
        provider = str(result.metadata.get("provider") or "")
        if result.asset_type is not AssetType.IMAGE or provider not in LABELLED_PROVIDERS:
            labelled.append(result)
            continue
        source = Path(result.local_path)
        target = label_ai_still(source, out_dir / f"{result.asset_id}{source.suffix}", text=text)
        labelled.append(
            result.model_copy(
                update={
                    "local_path": str(target),
                    "metadata": {**result.metadata, "ai_label": text},
                }
            )
        )
    return labelled
