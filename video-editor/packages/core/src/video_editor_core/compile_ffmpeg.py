"""Compile a :class:`RenderGraph` into a single-pass FFmpeg command.

The compiler emits one ``filter_complex`` that trims and concatenates the kept
segments, scales/pads to the canvas, applies every overlay (drawtext / image /
logo / b-roll), mixes the audio buses (with speech-aware ducking), applies
loudness normalisation, and encodes once. This replaces the historical chain of
per-stage re-encodes.

The compiler never executes anything: it returns argv lists so callers (and
tests) can inspect or run them. The video and audio chains are built separately
so the HyperFrames renderer can reuse the audio mixer in its final mux pass.
"""

from __future__ import annotations

from pathlib import Path

from video_editor_ffmpeg.captions import _escape_ffmpeg_text
from video_editor_ffmpeg.render_options import (
    RenderOptions,
)
from video_editor_ffmpeg.render_options import _add_audio_encode_args as _audio_encode
from video_editor_ffmpeg.render_options import _add_video_encode_args as _video_encode

from video_editor_core.render_graph import AudioTrack, Layer, RenderGraph

# One pass == one ffmpeg argv. Most graphs compile to a single pass.
Command = list[list[str]]

# Common head for every compiled command: overwrite, don't read stdin (workers
# can otherwise hang waiting on it), and keep stderr to errors only.
_FFMPEG_HEAD = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

# Pixel format for the opt-in linear-light composite. ``gbrapf32le`` is a
# 32-bit float, alpha-capable planar RGBA format, so ``overlay=`` keeps the
# overlay's alpha plane; the bundled FFmpeg advertises it (see
# ``ffmpeg -pix_fmts``). If it were unavailable the 16-bit ``rgba64le`` is the
# alpha-capable fallback. Chosen statically — no runtime probing.
_LINEAR_FLOAT_PIX_FMT = "gbrapf32le"

# Gamma output format of the linear chain, matching the ``-pix_fmt`` that the
# default (gamma) compiler targets.
_LINEAR_OUTPUT_PIX_FMT = "yuv420p"


def _resolve_fontfile() -> str:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return _FONT_CANDIDATES[0]


def _style_int(style: dict[str, object], key: str, default: int) -> int:
    """Coerce a free-form style value to int, falling back to ``default``."""
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


def _enable_expr(layer: Layer) -> str:
    start = layer.timing.start
    end = layer.timing.end
    if end is None:
        return f":enable='gte(t\\,{start:.3f})'"
    return f":enable='between(t\\,{start:.3f}\\,{end:.3f})'"


def _drawtext_filter(layer: Layer) -> str:
    style = layer.style
    fontfile = str(style.get("fontfile") or _resolve_fontfile())
    font_size = _style_int(style, "font_size", 48)
    font_color = str(style.get("font_color", "white"))
    border_width = _style_int(style, "border_width", 4)
    border_color = str(style.get("border_color", "black"))
    rect = layer.rect
    x = rect.x + rect.width // 2
    y = rect.y + rect.height // 2
    text = _escape_ffmpeg_text(layer.text)
    return (
        f"drawtext=fontfile={fontfile}:text='{text}':"
        f"fontsize={font_size}:fontcolor={font_color}:"
        f"borderw={border_width}:bordercolor={border_color}:"
        f"x={x}-text_w/2:y={y}-text_h/2{_enable_expr(layer)}"
    )


def _to_linear(label: str) -> tuple[list[str], str]:
    """Return (steps, label) converting ``label`` to linear-light float.

    ``zscale`` is told the input is bt709 explicitly: decoded streams are often
    untagged, and without the ``*in`` tags zimg cannot pick a conversion path.
    The linear opt-in path therefore assumes a bt709 source.
    """
    out = f"{label}_lin"
    steps = [
        f"[{label}]zscale=transfer=linear:primaries=bt709:matrix=bt709:"
        f"primariesin=bt709:matrixin=bt709:transferin=bt709,"
        f"format={_LINEAR_FLOAT_PIX_FMT}[{out}]"
    ]
    return steps, out


def _from_linear(label: str, pix_fmt: str) -> tuple[list[str], str]:
    """Return (steps, label) converting a linear-light float chain to ``pix_fmt``."""
    out = f"{label}_out"
    steps = [
        f"[{label}]zscale=transfer=bt709:primaries=bt709:matrix=bt709:"
        f"primariesin=bt709:matrixin=bt709:transferin=linear,"
        f"format={pix_fmt}[{out}]"
    ]
    return steps, out


def _use_linear(graph: RenderGraph) -> bool:
    """True when the opt-in linear composite should wrap the overlays.

    Requires ``color_mode == "linear"`` *and* at least one image/logo/video
    overlay. With no such overlay (or in gamma mode) the filtergraph is
    byte-identical to the default path.
    """
    if graph.color_mode != "linear":
        return False
    return any(layer.kind in {"image", "logo", "video"} for layer in graph.overlays)


def _overlay_parts(
    src_label: str,
    base_label: str,
    layer: Layer,
    index: int,
    *,
    linear_fmt: str = "",
) -> tuple[list[str], str]:
    """Return (filtergraph steps, output label) overlaying ``layer`` on ``base_label``.

    ``linear_fmt`` (opt-in) inserts a pixel-format conversion of the scaled
    overlay into the linear float format before the blend, preserving alpha.
    """
    rect = layer.rect
    scaled = f"ov_{index}_scaled"
    out = f"vlayer_{index}"
    steps = [
        f"[{src_label}]scale={rect.width}:{rect.height}:"
        f"force_original_aspect_ratio=decrease[{scaled}]"
    ]
    if linear_fmt:
        converted = f"ov_{index}_linear"
        steps.append(f"[{scaled}]format={linear_fmt}[{converted}]")
        scaled = converted
    steps.append(
        f"[{base_label}][{scaled}]overlay={rect.x}:{rect.y}:"
        f"eof_action=pass{_enable_expr(layer)}[{out}]"
    )
    return steps, out


def _track_parts(track: AudioTrack, src_label: str, index: int) -> tuple[list[str], str]:
    """Return (filtergraph steps, output label) for one extra audio bus."""
    out = f"atrk_{index}"
    chain = f"[{src_label}]volume={track.gain_db:.2f}dB"
    if track.start > 0:
        delay_ms = int(round(track.start * 1000))
        chain += f",adelay={delay_ms}|{delay_ms}"
    chain += f"[{out}]"
    return [chain], out


def _overlay_source_label(layer: Layer, input_idx: int, index: int, parts: list[str]) -> str:
    """Return the label whose frames should be overlaid, shifted onto the timeline.

    Image and logo inputs are looped stills, so they need no timing work. A
    video input is decoded from its own ``t=0``; without a shift it plays (and
    ends) at the start of the composition, and ``enable`` merely hides it --
    so by the time the layer becomes visible the clip is part-way through or
    already finished. ``trim`` cuts the clip to its scheduled length and
    ``setpts`` moves its first frame to ``timing.start``.
    """
    if layer.kind in {"image", "logo"}:
        return f"{input_idx}:v"

    start = layer.timing.start
    end = layer.timing.end
    if start <= 0 and end is None:
        return f"{input_idx}:v"

    out = f"ovpre_{index}"
    chain: list[str] = []
    if end is not None:
        chain.append(f"trim=duration={end - start:.3f}")
    chain.append(f"setpts=PTS-STARTPTS+{start:.3f}/TB")
    parts.append(f"[{input_idx}:v]{','.join(chain)}[{out}]")
    return out


def _video_filter_parts(
    graph: RenderGraph, first_index: int = 1
) -> tuple[list[str], list[list[str]], str]:
    """Build the video chain: trim/concat, scale/pad, overlays."""
    parts: list[str] = []
    extra_inputs: list[list[str]] = []
    cuts = graph.ordered_cuts()
    n = len(cuts)

    for i, cut in enumerate(cuts):
        parts.append(
            f"[0:v]trim=start={cut.source_start}:end={cut.source_end},setpts=PTS-STARTPTS[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(n))
    parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vcat]")
    parts.append(
        f"[vcat]scale={graph.width}:{graph.height}:"
        f"force_original_aspect_ratio=decrease,"
        f"pad={graph.width}:{graph.height}:(ow-iw)/2:(oh-ih)/2[vbase]"
    )

    prev = "vbase"
    if _use_linear(graph):
        # Text overlays stay in gamma on the base before the linear wrap, so
        # drawtext is never resolved in float. [linear wrap start]
        for i, layer in enumerate(graph.overlays):
            if layer.kind != "text":
                continue
            out = f"vtxt_{i}"
            parts.append(f"[{prev}]{_drawtext_filter(layer)}[{out}]")
            prev = out
        steps, prev = _to_linear(prev)
        parts.extend(steps)
        for i, layer in enumerate(graph.overlays):
            if layer.kind == "text":
                continue
            if layer.kind in {"image", "logo"}:
                extra_inputs.append(["-loop", "1", "-i", layer.source])
            else:
                extra_inputs.append(["-i", layer.source])
            input_idx = first_index + len(extra_inputs) - 1
            src = _overlay_source_label(layer, input_idx, i, parts)
            steps, out = _overlay_parts(src, prev, layer, i, linear_fmt=_LINEAR_FLOAT_PIX_FMT)
            parts.extend(steps)
            prev = out
        steps, prev = _from_linear(prev, _LINEAR_OUTPUT_PIX_FMT)
        parts.extend(steps)
        # [linear wrap end]
    else:
        for i, layer in enumerate(graph.overlays):
            if layer.kind == "text":
                out = f"vtxt_{i}"
                parts.append(f"[{prev}]{_drawtext_filter(layer)}[{out}]")
                prev = out
                continue
            if layer.kind in {"image", "logo"}:
                extra_inputs.append(["-loop", "1", "-i", layer.source])
            else:
                extra_inputs.append(["-i", layer.source])
            input_idx = first_index + len(extra_inputs) - 1
            src = _overlay_source_label(layer, input_idx, i, parts)
            steps, out = _overlay_parts(src, prev, layer, i)
            parts.extend(steps)
            prev = out
    parts.append(f"[{prev}]null[vout]")
    return parts, extra_inputs, "vout"


def _audio_filter_parts(
    graph: RenderGraph, first_index: int = 1, base_input: int = 0
) -> tuple[list[str], list[list[str]], str]:
    """Build the audio chain: cut audio + extra buses + ducking + loudnorm.

    ``base_input`` is the FFmpeg input index that carries the cut's original
    audio (0 for the combined/audio-only compilers, 1 for the mux compiler that
    feeds the composed video on input 0).
    """
    parts: list[str] = []
    extra_inputs: list[list[str]] = []
    if not graph.emits_audio:
        return parts, extra_inputs, ""

    if graph.has_audio:
        cuts = graph.ordered_cuts()
        n = len(cuts)
        for i, cut in enumerate(cuts):
            parts.append(
                f"[{base_input}:a]atrim=start={cut.source_start}:end={cut.source_end},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        audio_concat_in = "".join(f"[a{i}]" for i in range(n))
        parts.append(f"{audio_concat_in}concat=n={n}:v=0:a=1[acat]")
    else:
        # Video-only source: mix the buses over a silent bed spanning the cut.
        parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={graph.duration}[acat]")

    amix_inputs: list[str] = []
    ducked: list[str] = []
    for i, track in enumerate(graph.audio):
        if track.loop:
            extra_inputs.append(["-stream_loop", "-1", "-i", track.source])
        else:
            extra_inputs.append(["-i", track.source])
        input_idx = first_index + len(extra_inputs) - 1
        steps, label = _track_parts(track, f"{input_idx}:a", i)
        parts.extend(steps)
        if track.duck:
            ducked.append(label)
        else:
            amix_inputs.append(f"[{label}]")

    if ducked:
        parts.append("[acat]asplit=2[amix_base][duck_key]")
        amix_inputs.insert(0, "[amix_base]")
        for i, label in enumerate(ducked):
            duck_out = f"duck_{i}"
            parts.append(
                f"[{label}][duck_key]sidechaincompress="
                f"threshold=0.03:ratio=8:attack=5:release=300[{duck_out}]"
            )
            amix_inputs.append(f"[{duck_out}]")
    else:
        amix_inputs.insert(0, "[acat]")

    if len(amix_inputs) > 1:
        parts.append(
            f"{''.join(amix_inputs)}amix=inputs={len(amix_inputs)}:duration=first:"
            f"normalize=0[amixed]"
        )
    else:
        parts.append("[acat]anull[amixed]")
    parts.append("[amixed]loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    return parts, extra_inputs, "aout"


def _build_filter_complex(graph: RenderGraph) -> tuple[list[str], list[list[str]]]:
    """Build the combined video+audio filtergraph and extra input groups."""
    video_parts, video_inputs, _ = _video_filter_parts(graph, first_index=1)
    audio_parts, audio_inputs, _ = _audio_filter_parts(graph, first_index=1 + len(video_inputs))
    return video_parts + audio_parts, video_inputs + audio_inputs


def compile_ffmpeg(graph: RenderGraph, output_path: str | Path) -> Command:
    """Compile ``graph`` into a single-pass FFmpeg command (video + audio)."""
    options = graph.options or RenderOptions()
    parts, extra_inputs = _build_filter_complex(graph)

    cmd: list[str] = list(_FFMPEG_HEAD)
    if options.hwaccel:
        cmd.extend(["-hwaccel", options.hwaccel])
    cmd.extend(["-i", graph.source])
    for group in extra_inputs:
        cmd.extend(group)
    cmd.extend(["-filter_complex", ";".join(parts), "-map", "[vout]"])
    if graph.emits_audio:
        cmd.extend(["-map", "[aout]"])
    else:
        cmd.append("-an")
    _video_encode(cmd, options)
    cmd.extend(["-pix_fmt", "yuv420p"])
    if graph.emits_audio:
        _audio_encode(cmd, options)
    cmd.extend(["-movflags", "+faststart", str(output_path)])
    return [cmd]


def compile_ffmpeg_audio(graph: RenderGraph, output_path: str | Path) -> Command:
    """Compile an audio-only command (cut audio + buses + loudnorm).

    Used by the HyperFrames renderer to produce the mixed audio track that is
    muxed onto the composed video with ``-c:v copy``.
    """
    options = graph.options or RenderOptions()
    parts, extra_inputs, aout = _audio_filter_parts(graph, first_index=1)
    if not aout:
        raise ValueError("compile_ffmpeg_audio requires a graph with audio")

    cmd: list[str] = list(_FFMPEG_HEAD)
    if options.hwaccel:
        cmd.extend(["-hwaccel", options.hwaccel])
    cmd.extend(["-i", graph.source])
    for group in extra_inputs:
        cmd.extend(group)
    cmd.extend(["-filter_complex", ";".join(parts), "-map", f"[{aout}]", "-vn"])
    _audio_encode(cmd, options)
    cmd.append(str(output_path))
    return [cmd]


def compile_ffmpeg_mux_audio(
    graph: RenderGraph,
    composed_video: str | Path,
    output_path: str | Path,
) -> Command:
    """Mux the mixed graph audio onto an already-rendered video (video copied).

    Input 0 is the composed (video-only) render; input 1 is the graph source
    (which carries the cut's original audio); extra audio buses follow. The
    video stream is copied, so this only re-encodes audio.
    """
    options = graph.options or RenderOptions()
    parts, extra_inputs, aout = _audio_filter_parts(graph, first_index=2, base_input=1)
    if not aout:
        raise ValueError("compile_ffmpeg_mux_audio requires a graph with audio")

    cmd: list[str] = [*_FFMPEG_HEAD, "-i", str(composed_video), "-i", graph.source]
    for group in extra_inputs:
        cmd.extend(group)
    cmd.extend(
        ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", f"[{aout}]", "-c:v", "copy"]
    )
    _audio_encode(cmd, options)
    cmd.extend(["-movflags", "+faststart", "-shortest", str(output_path)])
    return [cmd]
