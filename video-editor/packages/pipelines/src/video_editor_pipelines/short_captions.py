"""Karaoke caption layers built from the measured alignment.

The captions skill owns grouping words into lines and the karaoke look; this
module is the only short-path code that imports it, because ``video_editor_core``
may not depend on a skill package.
"""

from __future__ import annotations

from captions.models import CaptionStyle, CaptionWord
from captions.timing import generate_segment_caption_lines
from video_editor_core.adapters.from_content_pack import Z_CAPTION
from video_editor_core.composition import (
    Canvas,
    CompositionLayer,
    LayerContent,
    LayerTime,
    WordSpan,
    quantise,
)
from video_editor_core.content_pack import Alignment
from video_editor_core.designs import DesignTokens
from video_editor_core.layer_presets import caption


def caption_style(tokens: DesignTokens) -> CaptionStyle:
    """The design's caption block layered over the named preset's grouping."""
    block = tokens.layers.caption_line
    overrides: dict[str, object] = {}
    if block.get("font"):
        overrides["font"] = str(block["font"])
    if block.get("font_size"):
        overrides["font_size"] = int(block["font_size"])
    if block.get("font_weight"):
        overrides["font_weight"] = int(block["font_weight"])
    if block.get("font_color"):
        overrides["font_color"] = str(block["font_color"])
    if block.get("highlight_color"):
        overrides["highlight_color"] = str(block["highlight_color"])
    if block.get("upcoming_opacity") is not None:
        overrides["upcoming_opacity"] = float(block["upcoming_opacity"])
    return CaptionStyle.preset(tokens.layers.caption_preset or "karaoke", **overrides)


def caption_layers(
    tokens: DesignTokens,
    alignment: Alignment,
    *,
    canvas: Canvas,
    fps: int = 30,
) -> list[CompositionLayer]:
    """One rich_text layer per caption line, spans rebased to the line."""
    words = [
        CaptionWord(text=word.display or word.text, start=word.start, end=word.end)
        for scene in alignment.scenes
        for word in scene.words
    ]
    if not words:
        return []
    style = caption_style(tokens)
    placed = caption(tokens, canvas)
    lines = generate_segment_caption_lines(words, 0.0, alignment.duration, style)
    layers: list[CompositionLayer] = []
    for index, line in enumerate(lines):
        start = quantise(line.start, fps)
        next_start = (
            quantise(lines[index + 1].start, fps)
            if index + 1 < len(lines)
            else quantise(alignment.duration, fps)
        )
        # ``linger`` holds a line after its last word, but never into the next
        # line: two live layers would double every word on screen.
        end = quantise(min(line.end + style.linger, next_start), fps)
        if end <= start:
            end = quantise(start + 1.0 / fps, fps)
        duration = round(end - start, 3)
        spans = [
            WordSpan(
                text=word.text,
                start=round(max(0.0, word.start - start), 3),
                end=round(min(duration, max(1.0 / fps, word.end - start)), 3),
            )
            for word in line.words
        ]
        layers.append(
            CompositionLayer(
                id=f"caption_{index:03d}",
                kind="rich_text",
                preset="caption_line",
                rect=placed.rect,
                time=LayerTime(start=start, end=end),
                content=LayerContent(spans=spans),
                style=dict(placed.style),
                z=Z_CAPTION,
            )
        )
    return layers
