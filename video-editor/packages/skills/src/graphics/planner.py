"""Build a per-segment composition plan from an EDL, preset, and transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from video_editor_core.safe_area import fit_box
from video_editor_core.timeline import EDL

from graphics.models import (
    Brand,
    CompositionPlan,
    Layer,
    Preset,
    Rect,
    SegmentComposition,
)

logger = structlog.get_logger(__name__)


def _words_in_segment(
    words: list[dict[str, Any]], start: float, end: float
) -> list[dict[str, Any]]:
    return [w for w in words if w.get("end", 0.0) > start and w.get("start", 0.0) < end]


def _make_text_layer(name: str, text: str, rect: Rect, style: dict[str, Any]) -> Layer:
    return Layer(
        type="text",
        name=name,
        rect=rect,
        text=text,
        style=style,
        timing={"start": 0.0, "end": None},
    )


def _fit_text_to_safe_area(layer: Layer, width: int, height: int) -> Layer:
    """Keep a text layer inside the platform safe area (see ``safe_area``)."""
    if layer.type != "text":
        return layer
    r = layer.rect
    x, y, w, h = fit_box((r.x, r.y, r.width, r.height), width, height)
    if (x, y, w, h) == (r.x, r.y, r.width, r.height):
        return layer
    return layer.model_copy(update={"rect": Rect(x=x, y=y, width=w, height=h)})


def _make_video_layer(rect: Rect) -> Layer:
    return Layer(
        type="video",
        name="base_video",
        rect=rect,
        timing={"start": 0.0, "end": None},
    )


def _make_logo_layer(brand: Brand, preset: Preset) -> Layer | None:
    """Build a logo overlay layer from the brand, or None when not configured.

    A logo is emitted when ``brand.logo.path`` is non-empty. The position
    is taken from ``preset.layout["logo"]`` if present, otherwise from
    ``brand.logo_position`` (default: bottom-right, 32px margin from the
    bottom-right corner, sized to ``brand.logo.width x brand.logo.height``).
    """
    logo_path = (brand.logo or {}).get("path", "")
    if not logo_path:
        return None

    width = int((brand.logo or {}).get("width", 160))
    height = int((brand.logo or {}).get("height", 160))

    if "logo" in preset.layout:
        rect = preset.layout["logo"]
    else:
        # Default bottom-right placement with a 32px margin.
        margin = 32
        rect = Rect(
            x=preset.width - width - margin,
            y=preset.height - height - margin,
            width=width,
            height=height,
        )

    return Layer(
        type="logo",
        name="brand_logo",
        rect=rect,
        source=logo_path,
        style={"width": width, "height": height},
        timing={"start": 0.0, "end": None},
    )


def plan_segments(
    edl: EDL,
    preset: Preset,
    transcript: dict[str, Any],
    source_video: Path | str,
    brand: Brand | None = None,
) -> CompositionPlan:
    """Generate a CompositionPlan for the locked EDL using the preset.

    When ``brand`` is supplied and ``brand.logo.path`` is non-empty, a
    logo overlay layer is appended to every segment.
    """
    source_video = str(source_video)
    words = transcript.get("words", [])

    segment_compositions: list[SegmentComposition] = []
    for seg in edl.segments:
        seg_words = _words_in_segment(words, seg.source_start, seg.source_end)
        " ".join(w.get("word", "") for w in seg_words).strip()

        layers: list[Layer] = []

        # Base video layer.
        if "video" in preset.layout:
            layers.append(_make_video_layer(preset.layout["video"]))
        elif "face" in preset.layout:
            layers.append(_make_video_layer(preset.layout["face"]))
        else:
            # Default full-frame video.
            layers.append(_make_video_layer(Rect(width=preset.width, height=preset.height)))

        # Captions placeholder layer. The captions skill will replace this with
        # per-word/per-line timed text layers using the full transcript.
        if "captions" in preset.layout and seg_words:
            caption_style = {
                "font": preset.captions.get("font", "Montserrat"),
                "font_size": preset.captions.get("font_size", 58),
                "font_color": preset.captions.get("font_color", "#FFFFFF"),
                "border_width": preset.captions.get("border_width", 4),
                "border_color": preset.captions.get("border_color", "#000000"),
                "animation": preset.captions.get("animation", "karaoke"),
            }
            layers.append(
                _make_text_layer("captions", "", preset.layout["captions"], caption_style)
            )

        # Preset-specific overlays.
        if preset.name == "short_explainer" and "graphics" in preset.layout:
            accent = preset.branding.get("accent_color", "#00D8FF")
            layers.append(
                _make_text_layer(
                    "top_graphic",
                    seg.label or "Key Point",
                    preset.layout["graphics"],
                    {
                        "font_size": 60,
                        "font_color": accent,
                        "border_width": 4,
                        "border_color": "#000000",
                    },
                )
            )
        elif preset.name == "tiktok_raw" and "hook" in preset.layout:
            layers.append(
                _make_text_layer(
                    "hook",
                    seg.label or "Hook",
                    preset.layout["hook"],
                    {
                        "font_size": 80,
                        "font_color": "#FFFFFF",
                        "border_width": 6,
                        "border_color": "#000000",
                    },
                )
            )
        elif preset.name == "long_form" and "lower_thirds" in preset.layout:
            layers.append(
                _make_text_layer(
                    "lower_third",
                    seg.label or "Speaker",
                    preset.layout["lower_thirds"],
                    {
                        "font_size": 42,
                        "font_color": "#FFFFFF",
                        "border_width": 3,
                        "border_color": "#000000",
                    },
                )
            )

        # Brand logo overlay (only when the brand defines a path).
        if brand is not None:
            logo_layer = _make_logo_layer(brand, preset)
            if logo_layer is not None:
                layers.append(logo_layer)

        segment_compositions.append(
            SegmentComposition(
                segment_id=seg.id,
                source_start=seg.source_start,
                source_end=seg.source_end,
                timeline_start=seg.timeline_start,
                timeline_end=seg.timeline_end,
                duration=seg.timeline_end - seg.timeline_start,
                width=preset.width,
                height=preset.height,
                layers=[
                    _fit_text_to_safe_area(layer, preset.width, preset.height) for layer in layers
                ],
            )
        )

    plan = CompositionPlan(
        job_id=edl.job_id,
        preset_name=preset.name,
        width=preset.width,
        height=preset.height,
        fps=preset.fps,
        source_video=source_video,
        segments=segment_compositions,
    )
    logger.info("composition_plan_built", job_id=edl.job_id, segments=len(segment_compositions))
    return plan
