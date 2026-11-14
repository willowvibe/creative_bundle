"""Render segment compositions via HyperFrames or an FFmpeg fallback."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import structlog
from video_editor_core.hyperframes_cli import (
    HyperFramesLintError,
    RenderSegmentError,
    hyperframes_available,
    lint_plan,
)
from video_editor_core.hyperframes_cli import (
    render_segment as hyperframes_render,
)
from video_editor_core.render_cache import SegmentCache, segment_cache_key
from video_editor_core.shaped_text import (
    PngOverlay,
    linear_graph,
    needs_shaping,
    render_shaped_text,
)
from video_editor_ffmpeg.captions import _escape_ffmpeg_text
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from graphics.models import SegmentComposition

logger = structlog.get_logger(__name__)


def _build_hyperframes_plan(
    composition: SegmentComposition,
    render_options: RenderOptions | None = None,
) -> dict[str, Any]:
    """Serialize a SegmentComposition to a HyperFrames render plan."""
    options = render_options or RenderOptions()
    return {
        "width": composition.width,
        "height": composition.height,
        "fps": options.fps or 30,
        "duration": composition.duration,
        "layers": [
            {
                "type": layer.type,
                "name": layer.name,
                "x": layer.rect.x,
                "y": layer.rect.y,
                "width": layer.rect.width,
                "height": layer.rect.height,
                "text": layer.text,
                "style": layer.style,
                "source": layer.source,
            }
            for layer in composition.layers
        ],
    }


def _escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter.

    Reuses the v1 captions escaper, which correctly handles ``'``, ``:``,
    ``,``, ``%``, ``{`` and ``}`` (C38).
    """
    return _escape_ffmpeg_text(text)


def _resolve_fontfile() -> str:
    """Find a bundled font for drawtext. Falls back to a system default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return candidates[0]  # ffmpeg will fail later with a clear error if absent


def _drawtext_filter(
    fontfile: str,
    text: str,
    style: dict[str, Any],
    rect_x: int,
    rect_y: int,
    rect_w: int,
    rect_h: int,
    timing: dict[str, Any],
) -> str:
    """Build a single drawtext=... filter string (no leading or trailing comma)."""
    font_size = style.get("font_size", 48)
    font_color = style.get("font_color", "white")
    border_width = style.get("border_width", 4)
    border_color = style.get("border_color", "black")
    x = rect_x + rect_w // 2
    y = rect_y + rect_h // 2
    escaped_text = _escape_drawtext(text)

    enable_expr = ""
    start = timing.get("start")
    end = timing.get("end")
    if start is not None and end is not None:
        enable_expr = f":enable='between(t\\,{start:.3f}\\,{end:.3f})'"
    elif start is not None:
        enable_expr = f":enable='gte(t\\,{start:.3f})'"
    elif end is not None:
        enable_expr = f":enable='lte(t\\,{end:.3f})'"

    return (
        f"drawtext=fontfile={fontfile}:text='{escaped_text}':"
        f"fontsize={font_size}:fontcolor={font_color}:"
        f"borderw={border_width}:bordercolor={border_color}:"
        f"x={x}-text_w/2:y={y}-text_h/2{enable_expr}"
    )


def _enable_window(timing: dict[str, Any]) -> str:
    """The layer's ``enable`` expression (unescaped), or empty when it is always on."""
    start, end = timing.get("start"), timing.get("end")
    if start is not None and end is not None:
        return f"between(t,{start:.3f},{end:.3f})"
    if start is not None:
        return f"gte(t,{start:.3f})"
    if end is not None:
        return f"lte(t,{end:.3f})"
    return ""


def _text_steps(text_layers: list[Any], fontfile: str, workdir: Path) -> list[str | PngOverlay]:
    """One filter step per text layer: ``drawtext``, or a shaped PNG overlay for complex scripts (#322)."""
    steps: list[str | PngOverlay] = []
    for index, layer in enumerate(text_layers):
        style, rect = layer.style, layer.rect
        if not needs_shaping(layer.text):
            steps.append(
                _drawtext_filter(
                    fontfile,
                    layer.text,
                    style,
                    rect.x,
                    rect.y,
                    rect.width,
                    rect.height,
                    layer.timing or {},
                )
            )
            continue
        shaped = render_shaped_text(
            layer.text,
            workdir / f"text_{index:03d}.png",
            font_size=int(style.get("font_size", 48)),
            color=str(style.get("font_color", "white")),
            border_width=int(style.get("border_width", 4)),
            border_color=str(style.get("border_color", "black")),
        )
        cx, cy = rect.x + rect.width // 2, rect.y + rect.height // 2
        steps.append(
            PngOverlay(shaped.path, f"{cx}-w/2", f"{cy}-h/2", _enable_window(layer.timing or {}))
        )
    return steps


def _ffmpeg_fallback_render(
    input_video: Path,
    composition: SegmentComposition,
    output: Path,
    render_options: RenderOptions | None = None,
) -> Path:
    """Render with FFmpeg; shaped-text PNGs (#322) live in a temporary directory for the call."""
    with tempfile.TemporaryDirectory(prefix="gfx_text_") as tmp:
        return _ffmpeg_fallback_render_in(
            input_video, composition, output, render_options, Path(tmp)
        )


def _ffmpeg_fallback_render_in(
    input_video: Path,
    composition: SegmentComposition,
    output: Path,
    render_options: RenderOptions | None,
    text_dir: Path,
) -> Path:
    """Render a segment composition using FFmpeg when HyperFrames is absent.

    This scales the source segment to the target canvas, then burns any
    text layers as drawtext overlays and any logo layers as PNG overlays
    via filter_complex. The simple ``-vf`` path is used when no logos
    are present (the historical fast path); the ``-filter_complex`` path
    is used when logos are present (it labels streams and chains
    overlays in order).
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    options = render_options or RenderOptions()
    width = composition.width
    height = composition.height
    fontfile = _resolve_fontfile()

    # Detect logo layers up front — they require the complex path.
    logo_layers = [layer for layer in composition.layers if layer.type == "logo" and layer.source]
    text_layers = [layer for layer in composition.layers if layer.type == "text" and layer.text]

    if not logo_layers:
        # Fast path: simple -vf chain. Same as the pre-logo behavior.
        filters: list[str | PngOverlay] = [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            *_text_steps(text_layers, fontfile, text_dir),
        ]
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-vf",
            linear_graph(filters),
        ]
        _video_encode(cmd, options)
        cmd.extend(["-c:a", "copy"])
        cmd.append(str(output))
        logger.info("ffmpeg_fallback_render", segment=composition.segment_id, filter="simple")
        run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
        return output

    # Complex path: build -filter_complex with labeled streams.
    # [0:v] = base video. [1..N:v] = logo sources (deduplicated).
    # Chain: base -> [base] -> drawtext (each text layer) -> [vbase]
    #        -> overlay (each logo layer) -> [vout]
    seen_sources: dict[str, int] = {}
    extra_inputs: list[str] = []
    for layer in logo_layers:
        if layer.source not in seen_sources:
            extra_inputs.append(layer.source)
            seen_sources[layer.source] = len(extra_inputs)  # 1-based

    chain_parts: list[str] = [
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[base]"
    ]

    # Apply text drawtexts in order, chaining off [base] or the previous label.
    prev_label = "base"
    if text_layers:
        chain_parts.append(
            linear_graph(
                _text_steps(text_layers, fontfile, text_dir), source="[base]", sink="[txt]"
            )
        )
        prev_label = "txt"

    # After all text drawtexts, the canvas is at [prev_label].
    # If no drawtexts ran, prev_label is "base".
    for i, layer in enumerate(logo_layers):
        idx = seen_sources[layer.source]
        x = layer.rect.x
        y = layer.rect.y
        w = layer.style.get("width", layer.rect.width)
        h = layer.style.get("height", layer.rect.height)
        # Scale the logo to the brand's specified dimensions.
        chain_parts.append(f"[{idx}:v]scale={w}:{h}[logo{idx}]")
        is_last = i == len(logo_layers) - 1
        out_label = "vout" if is_last else f"mix{idx}"
        chain_parts.append(f"[{prev_label}][logo{idx}]overlay={x}:{y}[{out_label}]")
        prev_label = "vout" if is_last else "mix" + str(idx)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_video),
    ]
    for src in extra_inputs:
        cmd += ["-i", src]
    cmd += [
        "-filter_complex",
        ";".join(chain_parts),
        "-map",
        f"[{prev_label}]",
    ]
    _video_encode(cmd, options)
    cmd.extend(["-c:a", "copy"])
    cmd.append(str(output))
    logger.info(
        "ffmpeg_fallback_render",
        segment=composition.segment_id,
        filter="complex",
        logos=len(extra_inputs),
    )
    run_ffmpeg(cmd, check=True, timeout=RENDER_TIMEOUT_SECONDS)
    return output


def render_composition_segment(
    composition: SegmentComposition,
    source_video: Path | str,
    output_dir: Path | str,
    cache: SegmentCache | None = None,
    preset_digest: str = "",
    render_options: RenderOptions | None = None,
) -> Path:
    """Render a single segment composition, using the cache when available.

    ``preset_digest`` is a short hash of the preset+brand that produced this
    composition. It is mixed into the cache key so that two runs on the same
    source segment with different presets (or different brand assets) cannot
    collide.
    """
    source_video = Path(source_video)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{composition.segment_id}.mp4"

    params = _build_hyperframes_plan(composition, render_options=render_options)
    # Mix the preset digest into the hashed params under a reserved key so
    # changing presets/brand invalidates the cache.
    key = segment_cache_key(
        source_video,
        composition.source_start,
        composition.source_end,
        operation="graphics",
        params={**params, "__preset_digest__": preset_digest},
    )

    if cache:
        cached = cache.get(key)
        if cached:
            shutil.copyfile(cached, output)
            return output

    options = render_options or RenderOptions()
    if not options.skip_hyperframes_lint:
        problems = lint_plan(params)
        if problems:
            raise HyperFramesLintError(
                [
                    f"HyperFrames plan for segment '{composition.segment_id}' failed lint",
                    *problems,
                ]
            )
    if hyperframes_available():
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(params, f, indent=2)
            plan_path = Path(f.name)
        try:
            # The JSON-plan interface is gone from HyperFrames 0.8; this call
            # raises RenderSegmentError. Degrade to FFmpeg rather than failing
            # the whole segment (the skill contract is HF *or* FFmpeg).
            hyperframes_render(
                plan_path, output, extra_args={"source": str(source_video.resolve())}
            )
        except RenderSegmentError as exc:
            logger.warning(
                "hyperframes_segment_fallback",
                segment=composition.segment_id,
                error=str(exc),
            )
            _ffmpeg_fallback_render(
                source_video, composition, output, render_options=render_options
            )
        finally:
            plan_path.unlink(missing_ok=True)
    else:
        _ffmpeg_fallback_render(source_video, composition, output, render_options=render_options)

    if cache:
        cache.put(key, output)

    return output


def render_all_segments(
    compositions: list[SegmentComposition],
    source_video: Path | str,
    output_dir: Path | str,
    cache: SegmentCache | None = None,
    preset_digest: str = "",
    render_options: RenderOptions | None = None,
) -> list[Path]:
    """Render all segment compositions and return their output paths."""
    source_video = Path(source_video)
    rendered: list[Path] = []
    for comp in compositions:
        rendered.append(
            render_composition_segment(
                comp,
                source_video,
                output_dir,
                cache=cache,
                preset_digest=preset_digest,
                render_options=render_options,
            )
        )
    return rendered
