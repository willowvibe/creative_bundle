"""Compile a :class:`Composition` into a HyperFrames HTML project.

One compiler for every input. The output contract:

* one root ``div[data-composition-id=main][data-no-timeline]`` carrying
  canvas, duration and fps;
* every visual layer is a root-level ``class="clip"`` element with
  ``data-start``, ``data-duration``, ``data-track-index`` and an explicit
  ``z-index`` — HyperFrames does not seek nested media;
* a layer whose markup nests block elements (an editorial plate, an SVG) is
  mounted as a sub-composition: the clip is an empty host carrying
  ``data-composition-id`` and the markup waits in a sibling ``<template>``.
  HyperFrames mounts it at render time, and its lint requires it — a timeline
  row may only contain inline text tags. The template sits outside the root
  composition, so the page still has one row per layer;
* ``@keyframes`` are emitted once per distinct primitive instantiation.

Media must be root children; scenes are a compile-time grouping only. A video
clip may carry ``data-playback-rate`` and ``data-media-start`` from its
``style``; the runtime reads both, so a source can be slowed or trimmed without
an FFmpeg pre-pass. Images ignore them. A media layer may also carry
``data-color-grading`` from the design's grade (or its own ``style["grade"]``);
the payload is sparse and the runtime merges each section over its defaults.
"""

from __future__ import annotations

import json
import math
import re
from html import escape as html_escape
from pathlib import Path
from typing import Any

import structlog

from video_editor_core.composition import Composition, CompositionLayer
from video_editor_core.designs import Grade
from video_editor_core.fonts import font_face_css, font_stack
from video_editor_core.motion import MotionCSS, MotionError, build_motion
from video_editor_core.project_assets import AssetCopier
from video_editor_core.scene_transitions import apply_scene_transitions

logger = structlog.get_logger(__name__)

COMPOSITION_ID = "main"

#: The tags HyperFrames' lint allows inside a timeline row.
_INLINE_TEXT_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "br",
        "code",
        "em",
        "i",
        "mark",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "u",
        "wbr",
    }
)
_OPEN_TAG = re.compile(r"<\s*([a-zA-Z][\w-]*)")


def _has_nested_structure(markup: str) -> bool:
    """Whether ``markup`` nests anything a timeline row may not contain.

    HyperFrames' lint audits every timeline row and warns when a row holds a
    block child; the sanctioned shape is a sub-composition mounted from a
    ``<template>``. Only opening tags matter — ``</div>`` and comments cannot
    open an element.
    """
    return any(
        match.group(1).lower() not in _INLINE_TEXT_TAGS for match in _OPEN_TAG.finditer(markup)
    )


def compile_composition(composition: Composition, project_dir: Path | str) -> Path:
    """Write a HyperFrames project for ``composition``; return the index path."""
    # Scene entrances are layer-agnostic in the IR, so they become per-layer
    # motion before anything is emitted. A composition with no transition is
    # returned unchanged.
    composition = apply_scene_transitions(composition)
    project_dir = Path(project_dir)
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    copier = AssetCopier(assets_dir)

    book = KeyframeBook()
    stylesheet: list[str] = []
    flourishes = sum(
        1 for scene in composition.scenes for layer in scene.layers if layer.style.get("flourish")
    )
    if flourishes > 2:
        # The HyperFrames registry advises at most two flourishes per video.
        # A warning, not an error: a design can still choose a louder look.
        logger.warning(
            "composition_flourishes", job_id=composition.job_id, count=flourishes, limit=2
        )
    templates: list[str] = []
    elements = [
        _layer_html(composition, layer, copier, book, stylesheet, templates)
        for scene in composition.scenes
        for layer in scene.layers
    ]
    elements.extend(_audio_html(composition, copier))

    index_path = project_dir / "index.html"
    index_path.write_text(
        _render_html(
            composition,
            "\n".join(elements),
            "\n".join(templates),
            book.css(),
            "\n".join(part for part in stylesheet if part),
            font_face_css(_font_families(composition), assets_dir / "fonts"),
        ),
        encoding="utf-8",
    )
    logger.info(
        "composition_project_written",
        job_id=composition.job_id,
        project=str(project_dir),
        elements=len(elements),
    )
    return index_path


class KeyframeBook:
    """Collect ``@keyframes`` blocks, one per distinct primitive instantiation."""

    def __init__(self) -> None:
        self._blocks: dict[str, None] = {}

    def add(self, css: MotionCSS) -> None:
        if css.keyframes:
            self._blocks.setdefault(css.keyframes, None)

    def css(self, indent: str = "      ") -> str:
        return "\n".join(f"{indent}{block}" for block in self._blocks)


def _layer_html(
    composition: Composition,
    layer: CompositionLayer,
    copier: AssetCopier,
    book: KeyframeBook,
    stylesheet: list[str],
    templates: list[str],
) -> str:
    duration = _layer_duration(composition, layer)
    css: list[str] = [_rect_css(layer), f"z-index:{layer.z};"]
    declarations: list[str] = []
    markup = ""
    background = ""
    hug = False

    if layer.kind in {"text", "rich_text", "html"}:
        if layer.kind != "html":
            # ``html`` layers are engine-generated markup: typographic scenes
            # and placeholders bring their own stylesheet. Caption CSS here
            # would leak into every glyph of the page they render.
            css.append(_text_css(layer))
        background = str(layer.style.get("background") or "")
        hug = bool(background) and bool(layer.style.get("background_hug")) and layer.kind != "html"
        if background and not hug:
            css.append(f"background:{_attr(background)};")
    elif layer.kind == "shape":
        css.append(_shape_css(layer, duration, declarations))
    elif layer.kind == "media":
        css.append(f"object-fit:{_object_fit(layer)};")

    if layer.preset == "placeholder":
        css.append(_placeholder_css(composition))

    for motion in layer.motions:
        built = build_motion(motion, clip_duration=duration, tempo=_tempo(composition))
        if built.markup and layer.kind in {"media", "svg"}:
            # These branches return before the container that would hold the
            # markup, so the motion would animate nothing. Say so instead of
            # emitting a silently dead animation.
            raise MotionError(
                f"layer {layer.id!r}: motion {motion.name!r} produces markup, "
                f"but a {layer.kind!r} layer cannot host it"
            )
        book.add(built)
        if built.props:
            css.append(built.props)
        if built.declaration:
            declarations.append(built.declaration)
        markup += built.markup

    extra_css = str(layer.style.get("extra_css") or "")
    if extra_css:
        stylesheet.append(extra_css)

    if declarations:
        css.append("animation:" + ", ".join(declarations) + ";")

    mask_source = str(layer.style.get("mask") or "")
    if mask_source:
        if layer.kind != "media":
            raise ValueError(f"layer {layer.id!r}: mask is only supported on media layers")
        mask_rel = copier.copy(mask_source)
        mask_url = f"url({_attr(mask_rel)})"
        css.append(
            f"-webkit-mask-image:{mask_url};-webkit-mask-size:cover;"
            "-webkit-mask-position:center;-webkit-mask-repeat:no-repeat;"
        )
        css.append(
            f"mask-image:{mask_url};mask-size:cover;mask-position:center;mask-repeat:no-repeat;"
        )

    style = _style_attr("".join(css))
    common = (
        f'class="clip" data-start="{layer.time.start:.3f}" '
        f'data-duration="{duration:.3f}" data-track-index="{layer.track}"'
    )

    if layer.kind == "media":
        rel = copier.copy(layer.content.source)
        grading = _grading_attribute(composition, layer, copier)
        if layer.media_kind == "video":
            attributes = " muted playsinline"
            if layer.style.get("loop"):
                attributes += " loop"
            attributes += _playback_attributes(layer)
            return (
                f'  <video id="{_attr(layer.id)}" {common} src="{_attr(rel)}" '
                f'style="{style}"{attributes}{grading}></video>'
            )
        return (
            f'  <img id="{_attr(layer.id)}" {common} src="{_attr(rel)}" '
            f'style="{style}" alt="{_attr(layer.content.alt)}"{grading} />'
        )

    if layer.kind == "svg":
        inner = (
            f'<svg viewBox="{_attr(layer.content.view_box)}" width="100%" height="100%">'
            f"{_svg_markup(layer)}</svg>"
        )
        return _row_html(layer, common, style, inner, templates)

    if layer.kind == "rich_text":
        body = _word_spans(layer)
    elif any(motion.name == "type_on" for motion in layer.motions):
        body = _type_on_markup(layer)
    elif layer.kind == "html":
        body = (
            html_escape(layer.content.text) if layer.preset == "placeholder" else layer.content.text
        )
    else:
        body = html_escape(layer.content.text)
    if hug:
        padding = _style_int(layer.style, "padding", 0)
        box_css = (
            f"display:inline-block;background:{_attr(background)};"
            f"padding:{padding}px;box-sizing:border-box;"
        )
        body = f'<span class="ve-box" style="{_style_attr(box_css)}">{body}</span>'
    return _row_html(layer, common, style, f"{body}{markup}", templates)


def _row_html(
    layer: CompositionLayer,
    common: str,
    style: str,
    inner: str,
    templates: list[str],
) -> str:
    """Emit the timeline row, mounting nested markup as a sub-composition.

    The host must stay empty: HyperFrames only mounts a template into a host
    with no children. ``data-no-timeline`` says this composition is CSS-only,
    exactly as the root declares, so the producer does not wait for a timeline
    registration that will never come.
    """
    row = f'  <div id="{_attr(layer.id)}" {common}'
    if not _has_nested_structure(inner):
        return f'{row} style="{style}">{inner}</div>'
    templates.append(f'<template id="{_attr(layer.id)}-template">{inner}</template>')
    return f'{row} data-composition-id="{_attr(layer.id)}" data-no-timeline style="{style}"></div>'


def _rect_css(layer: CompositionLayer) -> str:
    rect = layer.rect
    return (
        f"position:absolute;left:{rect.x}px;top:{rect.y}px;"
        f"width:{rect.width}px;height:{rect.height}px;"
    )


def _text_css(layer: CompositionLayer) -> str:
    style = layer.style
    font_size = _style_int(style, "font_size", 48)
    weight = _style_int(style, "font_weight", 700)
    color = _attr(style.get("font_color", "#FFFFFF"))
    family = font_stack(str(style.get("font") or "") or None)
    align = "flex-start" if style.get("valign") == "top" else "center"
    css = (
        f"display:flex;align-items:{align};justify-content:center;text-align:center;"
        f"font-family:{family};font-weight:{weight};font-size:{font_size}px;color:{color};"
    )
    padding = _style_int(style, "padding", 0)
    if padding and not (style.get("background_hug") and style.get("background")):
        css += f"padding:{padding}px;box-sizing:border-box;"
    if style.get("text_effect") == "shadow":
        # A soft drop shadow keeps glyph shapes intact; a centred stroke eats
        # into thin letters at caption sizes.
        css += "text-shadow:0 4px 18px rgba(0,0,0,0.9),0 2px 3px rgba(0,0,0,0.9);"
    elif style.get("text_effect") == "halo":
        # Dark text on a light backdrop: a soft light glow lifts it off busy
        # paper texture where a dark shadow would smear the glyphs.
        css += "text-shadow:0 0 14px rgba(255,255,255,0.85),0 0 3px rgba(255,255,255,0.9);"
    else:
        border = _style_int(style, "border_width", 4)
        border_color = _attr(style.get("border_color", "#000000"))
        css += (
            f"-webkit-text-stroke:{border}px {border_color};"
            f"text-shadow:0 {border}px {border * 2}px rgba(0,0,0,0.75);"
        )
    if layer.kind == "rich_text":
        css += f"--ve-hl:{_attr(style.get('highlight_color', '#FDB813'))};"
    return css


def _shape_css(layer: CompositionLayer, duration: float, declarations: list[str]) -> str:
    style = layer.style
    color = _attr(style.get("color", "#FDB813"))
    stroke = _style_int(style, "stroke_width", 5)
    radius = "50%" if style.get("shape") == "circle" else f"{_style_int(style, 'radius', 18)}px"
    fill = max(0.0, min(1.0, _style_float(style, "fill_opacity", 0.0)))

    if layer.preset == "scrim" and layer.content.gradient:
        return f"background:{_attr(layer.content.gradient)};"

    css = (
        f"box-sizing:border-box;border:{stroke}px solid {color};border-radius:{radius};"
        f"background:color-mix(in srgb, {color} {fill * 100:.0f}%, transparent);"
    )
    if layer.preset == "spotlight":
        fade_at = max(0.0, duration - 0.3)
        css += (
            f"box-shadow:0 0 28px {color}, "
            f"inset 0 0 18px color-mix(in srgb, {color} 45%, transparent);"
        )
        declarations.extend(
            [
                "ve_spot_in 0.35s cubic-bezier(0.2, 0.8, 0.2, 1) 0s both",
                "ve_spot_breathe 1.6s ease-in-out 0.35s infinite alternate",
                f"ve_spot_out 0.3s ease-in {fade_at:.3f}s forwards",
            ]
        )
    return css


def _word_spans(layer: CompositionLayer) -> str:
    """Per-word spans that take the highlight colour while each word is spoken.

    Three animations per word: a colour hold while spoken (``ve_word_hl``), a
    small lift at onset (``ve_word_pop``; a lift, not a scale, so the word
    never grows over the space beside it, #338), and an opacity step from
    ``upcoming_opacity`` to full once reached (``ve_word_seen``).
    """
    upcoming = _style_float(layer.style, "upcoming_opacity", 1.0)
    spans: list[str] = []
    for word in layer.content.spans:
        duration = max(0.001, word.end - word.start)
        animations = [
            f"ve_word_hl {duration:.3f}s step-end {word.start:.3f}s none",
            f"ve_word_pop {min(0.22, max(0.12, duration)):.3f}s ease-out {word.start:.3f}s none",
        ]
        css = "animation:" + ", ".join(animations)
        if upcoming < 1.0:
            css = f"opacity:{upcoming:.2f};animation:" + ", ".join(
                [*animations, f"ve_word_seen 0.001s linear {word.start:.3f}s forwards"]
            )
        spans.append(f'<span class="ve-word" style="{css}">{html_escape(word.text)}</span>')
    # One inline wrapper: the caption box is a flex container, and bare spans
    # would each become a flex item, collapsing the spaces between words.
    return '<span class="ve-line">' + " ".join(spans) + "</span>"


def _type_on_markup(layer: CompositionLayer) -> str:
    """Split the layer's text into per-character spans with a stagger (P4.7).

    ``step`` (seconds per character) comes from the ``type_on`` motion; the
    keyframes are the shared ``ve_type_char`` opacity flip, so the text keeps
    its layout and only the reveal is animated.
    """
    step = 0.04
    for motion in layer.motions:
        if motion.name != "type_on":
            continue
        raw = motion.params.get("step", step)
        try:
            step = max(0.001, float(raw))
        except (TypeError, ValueError):
            step = 0.04
    pieces: list[str] = []
    for index, char in enumerate(layer.content.text):
        if char == " ":
            pieces.append(" ")
            continue
        delay = index * step
        pieces.append(
            f'<span style="animation:ve_type_char .01s linear {delay:.3f}s both">'
            f"{html_escape(char)}</span>"
        )
    return "".join(pieces)


def _svg_markup(layer: CompositionLayer) -> str:
    # ``draw`` strokes a path without measuring it, which needs pathLength="1".
    # Its ``stroke-dasharray``/``stroke-dashoffset``/``fill`` props stay on the
    # wrapper ``div`` above: all three are inherited SVG presentation
    # properties, so setting them on the ancestor reaches this path (including
    # while animated) without duplicating them here.
    path_length = ' pathLength="1"' if any(m.name == "draw" for m in layer.motions) else ""
    return f'<path d="{_attr(layer.content.path)}"{path_length} />'


def _placeholder_css(composition: Composition) -> str:
    return (
        "display:flex;align-items:center;justify-content:center;text-align:center;"
        "padding:80px;box-sizing:border-box;color:#f8fafc;background:#0f172a;"
        "font-family:Inter,system-ui,sans-serif;font-weight:700;"
        f"font-size:{max(36, composition.canvas.width // 34)}px;"
    )


def _audio_html(composition: Composition, copier: AssetCopier) -> list[str]:
    # "external" means the caller muxes the audio itself (the render-graph
    # path carries cues for ducking/loudnorm). Embedding them anyway would
    # double up the mix, so the compiler never infers the decision.
    if composition.audio_mode != "embed":
        return []
    out: list[str] = []
    for cue in composition.audio:
        rel = copier.copy(cue.source)
        duration = composition.duration if cue.duration is None else cue.duration
        volume = "" if abs(cue.volume - 1.0) < 1e-9 else f' data-volume="{cue.volume:.2f}"'
        out.append(
            f'    <audio id="{_attr(cue.id)}" src="{_attr(rel)}" '
            f'data-start="{cue.start:.3f}" data-duration="{duration:.3f}" '
            f'data-track-index="{cue.track}"{volume}></audio>'
        )
    return out


def _font_families(composition: Composition) -> list[str]:
    families: list[str] = []
    if composition.design is not None:
        families.extend([composition.design.type.display, composition.design.type.body])
    for scene in composition.scenes:
        for layer in scene.layers:
            if layer.kind in {"text", "rich_text"}:
                families.append(str(layer.style.get("font") or "Inter"))
    return families


def _tempo(composition: Composition) -> float:
    return composition.design.motion.tempo if composition.design else 1.0


def _layer_duration(composition: Composition, layer: CompositionLayer) -> float:
    end = composition.duration if layer.time.end is None else layer.time.end
    return max(0.001, end - layer.time.start)


def _object_fit(layer: CompositionLayer) -> str:
    fallback = "cover" if layer.media_kind == "video" else "contain"
    return str(layer.style.get("object_fit") or fallback)


def _grading_attribute(
    composition: Composition,
    layer: CompositionLayer,
    copier: AssetCopier,
) -> str:
    """The ``data-color-grading`` payload for a media layer, if any.

    A design's grade is the default; ``style["grade"]`` overrides it for one
    layer. The payload stays sparse because the runtime merges each section
    over its own defaults.
    """
    grade = composition.design.grade if composition.design is not None else None
    override = layer.style.get("grade")
    if override is not None:
        if not isinstance(override, dict):
            raise ValueError(f"layer {layer.id!r}: grade must be an object")
        grade = Grade.model_validate(override)
    if grade is None:
        return ""

    payload: dict[str, Any] = {"intensity": grade.intensity}
    if grade.preset:
        payload["preset"] = grade.preset
    if grade.adjust:
        payload["adjust"] = grade.adjust
    if grade.details:
        payload["details"] = grade.details
    if grade.effects:
        payload["effects"] = grade.effects
    if grade.lut:
        if not Path(grade.lut).is_file():
            raise ValueError(f"layer {layer.id!r}: grade LUT not found: {grade.lut}")
        payload["lut"] = {"src": copier.copy(grade.lut), "intensity": grade.lut_intensity}
    encoded = json.dumps(payload, separators=(",", ":"))
    return f' data-color-grading="{html_escape(encoded, quote=True)}"'


def _playback_attributes(layer: CompositionLayer) -> str:
    """Emit ``data-playback-rate`` / ``data-media-start`` for a video clip.

    Both are read by the HyperFrames runtime for ``<video>`` only; an ``<img>``
    has no playback, so a style block that sets them on a still is ignored
    rather than emitting attributes the renderer never reads. The values are
    validated here because the runtime clamps silently, which would turn a
    mistyped speed into a render that looks wrong with no error.
    """
    attributes = ""
    rate = _media_number(layer, "playback_rate", minimum=0.1, maximum=10.0)
    if rate is not None and abs(rate - 1.0) > 1e-9:
        attributes += f' data-playback-rate="{rate:g}"'
    start = _media_number(layer, "media_start", minimum=0.0)
    if start is not None and abs(start) > 1e-9:
        attributes += f' data-media-start="{start:g}"'
    return attributes


def _media_number(
    layer: CompositionLayer,
    key: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float | None:
    if key not in layer.style:
        return None
    raw = layer.style[key]
    if isinstance(raw, bool):
        raise ValueError(f"layer {layer.id!r}: {key} must be a number, got {raw!r}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"layer {layer.id!r}: {key} must be a number, got {raw!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"layer {layer.id!r}: {key} must be finite, got {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        limit = f"{minimum:g}..{maximum:g}" if maximum is not None else f">= {minimum:g}"
        raise ValueError(f"layer {layer.id!r}: {key} must be {limit}, got {value:g}")
    return value


def _attr(value: Any) -> str:
    return html_escape(str(value), quote=True)


def _style_attr(css: str) -> str:
    """Inline styles sit in double quotes, so a stray ``"`` would end them."""
    return css.replace('"', "&quot;")


def _style_int(style: dict[str, Any], key: str, default: int) -> int:
    value = style.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _style_float(style: dict[str, Any], key: str, default: float) -> float:
    value = style.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _render_html(
    composition: Composition,
    elements: str,
    templates: str,
    keyframes_css: str,
    stylesheet_css: str,
    fonts_css: str,
) -> str:
    canvas = composition.canvas
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={canvas.width}, height={canvas.height}" />
    <title>{html_escape(composition.job_id)}</title>
    <style>
{fonts_css}
      html, body {{ margin: 0; padding: 0; background: #000; }}
      #root {{ position: relative; width: {canvas.width}px; height: {canvas.height}px;
        overflow: hidden; background: #000; }}
      .clip {{ position: absolute; }}
      .ve-line {{ display: inline-block; animation: ve_line_in 0.22s cubic-bezier(0.2, 0.8, 0.2, 1) 0s both; }}
      .ve-word {{ white-space: pre; display: inline-block; }}
      @keyframes ve_word_hl {{ from, to {{ color: var(--ve-hl); }} }}
      @keyframes ve_word_pop {{ 0% {{ transform: none; }} 45% {{ transform: translateY(-0.12em); }} 100% {{ transform: none; }} }}
      @keyframes ve_word_seen {{ to {{ opacity: 1; }} }}
      @keyframes ve_type_char {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
      @keyframes ve_line_in {{ from {{ opacity: 0; transform: translateY(14px) scale(0.96); }} to {{ opacity: 1; transform: none; }} }}
      @keyframes ve_spot_in {{ from {{ opacity: 0; transform: scale(1.12); }} to {{ opacity: 1; transform: scale(1); }} }}
      @keyframes ve_spot_breathe {{ from {{ filter: brightness(1); }} to {{ filter: brightness(1.35); }} }}
      @keyframes ve_spot_out {{ to {{ opacity: 0; }} }}
      #base_video {{ position: absolute; inset: 0; object-fit: cover;
        width: {canvas.width}px; height: {canvas.height}px; }}
{keyframes_css}
{stylesheet_css}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="{COMPOSITION_ID}"
      data-no-timeline
      data-start="0"
      data-width="{canvas.width}"
      data-height="{canvas.height}"
      data-duration="{composition.duration:.3f}"
      data-fps="{canvas.fps}"
    >
{elements}
    </div>
{templates}
  </body>
</html>
"""
