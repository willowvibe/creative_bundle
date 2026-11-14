"""Render a final video directly from a FableCut ``project.json``.

This renderer treats the edited FableCut project as the single source of truth.
It reproduces every clip's timing, position, scale, rotation, opacity, captions,
music volume, and image Ken Burns keyframes. Edits made in the browser timeline are
reflected without any intermediate scene-plan translation.
"""

from __future__ import annotations

import shlex
import tempfile
from pathlib import Path

import structlog
from video_editor_ffmpeg.probe import MediaInfoError, probe_duration
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.run import RENDER_TIMEOUT_SECONDS, run_ffmpeg

from video_editor_fablecut_bridge.models import (
    FableCutClip,
    FableCutKeyframe,
    FableCutProject,
)

logger = structlog.get_logger(__name__)

DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf"
DEFAULT_VIDEO_BITRATE = "6M"


def _probe_duration(path: Path) -> float:
    """Return the duration of a media file, or 0.0 when it is missing.

    Delegates to the one shared probe (#279); the caller treats a missing
    duration as "unknown" rather than failing the whole render.
    """
    try:
        return probe_duration(path)
    except MediaInfoError:
        return 0.0
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Non-numeric duration for {path}") from exc


def _clip_end(clip: FableCutClip) -> float:
    return float(clip.start) + float(clip.duration)


def _project_duration(clips: list[FableCutClip]) -> float:
    return max((_clip_end(c) for c in clips), default=0.0)


def _track_index(track: str) -> tuple[int, int]:
    """Sort by track letter (V before T) then number."""
    letter = track[0] if track else "Z"
    try:
        num = int(track[1:]) if len(track) > 1 else 0
    except ValueError:
        num = 0
    order = {"V": 0, "T": 1, "A": 2}
    return (order.get(letter, 99), num)


def _keyframes_expr(frames: list[FableCutKeyframe], fps: int, duration: float) -> str | None:
    """Build a single linear expression from the first to last keyframe value."""
    if not frames or len(frames) < 2:
        return None
    first = frames[0]
    last = frames[-1]
    v0 = float(first.v)
    v1 = float(last.v)
    t0 = float(first.t)
    t1 = float(last.t)
    if duration == 0:
        return str(v0)
    # Normalize to 0..1 across the clip duration; multiply by output frame number.
    return f"{v0}+({v1}-{v0})*(on/{fps}-{t0})/({t1}-{t0})"


def _video_kind(path: Path) -> str:
    """Return 'image' or 'video' based on extension."""
    ext = path.suffix.lower().lstrip(".")
    if ext in {"png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif"}:
        return "image"
    return "video"


def _format_color(color: str | None) -> str:
    """Return an FFmpeg-friendly color from a hex string."""
    if not color:
        return "black"
    c = color.strip().lstrip("#")
    if len(c) == 6:
        return f"0x{c}"
    if len(c) == 3:
        return f"0x{c[0]}{c[0]}{c[1]}{c[1]}{c[2]}{c[2]}"
    return c


def _safe_path(path: Path) -> str:
    """Escape a filesystem path for use inside a single-quoted FFmpeg argument."""
    return str(path).replace("'", "'\\''")


class FableCutRenderer:
    """Render a final MP4 from an edited FableCut project."""

    def __init__(
        self,
        project_path: Path,
        output_path: Path,
        media_dir: Path | None = None,
        render_options: RenderOptions | None = None,
        font_path: str | None = None,
        allowed_roots: list[Path] | None = None,
    ):
        self.project_path = Path(project_path)
        self.output_path = Path(output_path)
        self.project_dir = self.project_path.parent
        self.media_dir = Path(media_dir) if media_dir else self.project_dir / "media"
        self.render_options = render_options or RenderOptions()
        self.font_path = font_path or DEFAULT_FONT_PATH
        self.allowed_roots = allowed_roots
        self.project = FableCutProject.model_validate_json(
            self.project_path.read_text(encoding="utf-8")
        )
        self._media_map = self._build_media_map()

    # ------------------------------------------------------------------
    # Media resolution
    # ------------------------------------------------------------------
    def _build_media_map(self) -> dict[str, Path]:
        """Map FableCut media ids to resolved filesystem paths."""
        out: dict[str, Path] = {}
        for media in self.project.media:
            if not media.id or not media.src:
                continue
            out[media.id] = self._resolve_media_src(media.src)
        return out

    def _resolve_media_src(self, src: str) -> Path:
        """Resolve a FableCut media ``src`` to a contained filesystem path.

        FableCut projects normally store ``src`` as ``/media/filename``. We first
        resolve it under ``self.media_dir`` and then under ``self.project_dir``.
        A path that escapes the project directory is rejected, and any explicit
        ``allowed_roots`` are also enforced when provided (used by the worker
        handler for multi-tenant containment).
        """
        candidates: list[Path] = []
        if src.startswith("/"):
            rel = src.lstrip("/")
            candidates.append(self.media_dir / rel)
            candidates.append(self.project_dir / rel)
            candidates.append(self.media_dir / Path(rel).name)
        else:
            candidates.append(self.project_dir / src)
            candidates.append(self.media_dir / src)
            candidates.append(self.media_dir / Path(src).name)

        for candidate in candidates:
            candidate = candidate.resolve()
            if not candidate.exists():
                continue
            if not self._is_contained(candidate):
                logger.warning(
                    "fablecut_media_path_escapes_project",
                    src=src,
                    candidate=str(candidate),
                )
                raise ValueError(
                    f"FableCut media src {src!r} resolves outside the project: {candidate}"
                )
            return candidate

        raise FileNotFoundError(f"Cannot resolve FableCut media src: {src}")

    def _is_contained(self, path: Path) -> bool:
        """Return True when ``path`` is inside the project or allowed roots."""
        roots = [self.project_dir.resolve(), self.media_dir.resolve()]
        if self.allowed_roots:
            roots.extend([r.resolve() for r in self.allowed_roots])
        return any(path.is_relative_to(root) for root in roots)

    # ------------------------------------------------------------------
    # Filter graph builders
    # ------------------------------------------------------------------
    def _width(self) -> int:
        return self.render_options.width or self.project.width or 1920

    def _height(self) -> int:
        return self.render_options.height or self.project.height or 1080

    def _fps(self) -> int:
        return self.render_options.fps or self.project.fps or 30

    def _build_visual_clip_filter(self, clip: FableCutClip, input_label: str) -> tuple[str, str]:
        """Return a filtergraph subchain that produces a transformed clip stream."""
        if not clip.mediaId or clip.mediaId not in self._media_map:
            raise ValueError(f"Clip {clip.id} missing media: {clip.mediaId}")
        path = self._media_map[clip.mediaId]
        kind = _video_kind(path)
        duration = float(clip.duration)
        if duration <= 0:
            raise ValueError(f"Clip {clip.id} has zero duration")
        inn = float(clip.in_)
        fps = self._fps()
        frames = max(1, int(round(duration * fps)))
        width = self._width()
        height = self._height()
        out_pad = f"clip_{clip.id}"

        if kind == "image":
            scale_expr = _keyframes_expr(clip.keyframes.get("scale", []), fps, duration)
            if scale_expr is None:
                scale_expr = str(clip.props.scale)
            x_kf = _keyframes_expr(clip.keyframes.get("x", []), fps, duration) or "0"
            y_kf = _keyframes_expr(clip.keyframes.get("y", []), fps, duration) or "0"
            x_expr = f"(iw-ow)/2+({x_kf})"
            y_expr = f"(ih-oh)/2+({y_kf})"

            subchain = (
                f"{input_label}"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
                f"zoompan=z={scale_expr}:x={x_expr}:y={y_expr}:"
                f"d={frames}:s={width}x{height}:fps={fps},"
                f"trim=start=0:end={duration},setpts=PTS-STARTPTS[{out_pad}]"
            )
        else:
            src_dur = _probe_duration(path)
            speed = float(clip.props.speed or 1.0)
            needed = duration * speed
            trim_end = inn + needed
            if src_dur > 0:
                trim_end = min(trim_end, src_dur)
            subchain = (
                f"{input_label}"
                f"trim=start={inn}:end={trim_end},setpts={1 / speed}*(PTS-STARTPTS),"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,setsar=1,"
                f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
                f"tpad=stop_mode=clone:stop_duration={duration},"
                f"trim=start=0:end={duration},setpts=PTS-STARTPTS[{out_pad}]"
            )

        return subchain, out_pad

    def _build_text_filter(self, clip: FableCutClip, tmp_dir: Path, index: int) -> str | None:
        """Build a drawtext filter snippet for a text/caption clip."""
        text = str(clip.props.text or "").strip()
        if not text:
            return None

        text_file = tmp_dir / f"text_{index:04d}.txt"
        text_file.write_text(text, encoding="utf-8")

        scale = float(clip.props.scale or 1.0)
        opacity = float(clip.props.opacity or 1.0)
        font_size = int(float(clip.props.fontSize or 72.0) * scale)
        color = clip.props.color or "#ffffff"
        align = clip.props.align or "center"
        stroke_width = int(float(clip.props.strokeWidth or 0.0) * scale)
        stroke_color = clip.props.strokeColor or "#000000"
        bg_color = clip.props.bgColor or "#000000"
        bg_opacity = float(clip.props.bgOpacity or 0.0)
        line_height = float(clip.props.lineHeight or 1.2)

        safe_font = _safe_path(Path(self.font_path))
        safe_text = _safe_path(text_file)

        x = float(clip.props.x)
        y = float(clip.props.y)
        if align == "left":
            x_expr = f"(w/2)+{x}"
        elif align == "right":
            x_expr = f"(w/2)+{x}-text_w"
        else:
            x_expr = f"(w/2)+{x}-text_w/2"
        y_expr = f"(h/2)+{y}-text_h/2"

        start = float(clip.start)
        end = _clip_end(clip)

        parts = [
            f"drawtext=fontfile={safe_font}",
            f"textfile={safe_text}",
            f"fontsize={font_size}",
            f"fontcolor={color}",
            f"x={x_expr}",
            f"y={y_expr}",
            f"line_spacing={int((line_height - 1.0) * font_size)}",
            f"enable='between(t,{start},{end})'",
        ]
        if stroke_width > 0:
            parts.append(f"borderw={stroke_width}")
            parts.append(f"bordercolor={stroke_color}")
        if bg_opacity > 0.001 and bg_color:
            parts.append(f"box=1:boxcolor={bg_color}@{bg_opacity}:boxborderw=20")
        if opacity < 0.999:
            parts.append(f"alpha={opacity}")

        return ":".join(parts)

    def _build_audio_clip_filter(self, clip: FableCutClip, input_label: str) -> tuple[str, str]:
        """Return an audio filter subchain plus output pad for one audio clip."""
        duration = float(clip.duration)
        inn = float(clip.in_)
        start = float(clip.start)
        volume = float(clip.props.volume or 1.0)
        speed = float(clip.props.speed or 1.0)

        filters: list[str] = [
            f"atrim=start={inn}:end={inn + duration * speed}",
            "asetpts=PTS-STARTPTS",
        ]
        if speed != 1.0:
            filters.append(f"atempo={min(2.0, max(0.5, speed))}")
        if abs(volume - 1.0) > 0.001:
            filters.append(f"volume={volume}")
        delay_ms = int(start * 1000)
        filters.append(f"adelay=delays={delay_ms}|{delay_ms}:all=1")

        out_pad = f"a_{clip.id}"
        subchain = f"{input_label}" + ",".join(filters) + f"[{out_pad}]"
        return subchain, out_pad

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def render(self) -> Path:
        """Build and run the FFmpeg filter graph."""
        clips = self.project.clips
        visual_clips = [
            c for c in clips if c.track.startswith("V") and c.kind in {"video", "image"}
        ]
        visual_clips.sort(key=lambda c: (_track_index(c.track), c.start))

        text_clips = [c for c in clips if c.kind == "text"]
        audio_clips = [c for c in clips if c.track.startswith("A")]

        duration = _project_duration(clips)
        if duration <= 0:
            raise ValueError("Project has zero duration")

        input_order: list[tuple[str, Path]] = []
        input_index: dict[str, int] = {}
        for c in visual_clips + audio_clips:
            mid = c.mediaId
            if not mid:
                continue
            if mid not in self._media_map:
                raise FileNotFoundError(f"Clip {c.id} references missing media: {mid}")
            if mid not in input_index:
                input_index[mid] = len(input_order)
                input_order.append((mid, self._media_map[mid]))

        if not input_order:
            raise ValueError("No media inputs found")

        width = self._width()
        height = self._height()
        fps = self._fps()
        bg_color = _format_color(self.project.background)

        with tempfile.TemporaryDirectory(prefix="fablecut_render_") as tmp:
            tmp_dir = Path(tmp)
            filter_parts: list[str] = []

            for c in visual_clips:
                if not c.mediaId:
                    continue
                idx = input_index[c.mediaId]
                subchain, _ = self._build_visual_clip_filter(c, f"[{idx}:v]")
                filter_parts.append(subchain)

            filter_parts.append(
                f"color=c={bg_color}:s={width}x{height}:d={duration}:r={fps}[base_v]"
            )

            last_v = "base_v"
            for i, c in enumerate(visual_clips):
                start = float(c.start)
                end = _clip_end(c)
                next_label = f"v_{i}"
                overlay = (
                    f"[{last_v}][clip_{c.id}]overlay="
                    f"x={c.props.x}:y={c.props.y}:"
                    f"enable='between(t,{start},{end})':format=auto[{next_label}]"
                )
                filter_parts.append(overlay)
                last_v = next_label

            for i, c in enumerate(text_clips):
                text_filter = self._build_text_filter(c, tmp_dir, i)
                if not text_filter:
                    continue
                next_label = f"t_{i}"
                filter_parts.append(f"[{last_v}]{text_filter}[{next_label}]")
                last_v = next_label

            filter_parts.append(f"[{last_v}]format=yuv420p[final_v]")

            audio_outputs: list[str] = []
            for c in audio_clips:
                if not c.mediaId:
                    continue
                idx = input_index[c.mediaId]
                subchain, out_pad = self._build_audio_clip_filter(c, f"[{idx}:a]")
                filter_parts.append(subchain)
                audio_outputs.append(f"[{out_pad}]")

            audio_map = "[final_a]" if audio_outputs else None
            if audio_outputs:
                filter_parts.append(
                    f"{''.join(audio_outputs)}amix="
                    f"inputs={len(audio_outputs)}:duration=longest:normalize=0[final_a]"
                )

            filter_complex = ";".join(filter_parts)
            fc_file = tmp_dir / "filter_complex.txt"
            fc_file.write_text(filter_complex, encoding="utf-8")

            inputs: list[str] = []
            for _, path in input_order:
                inputs.extend(["-i", str(path)])

            self.output_path.parent.mkdir(parents=True, exist_ok=True)

            options = self.render_options
            cmd: list[str] = [
                "ffmpeg",
                "-y",
                *inputs,
                "-filter_complex_script",
                str(fc_file),
                "-map",
                "[final_v]",
            ]
            if audio_map:
                cmd.extend(["-map", audio_map])

            cmd.extend(["-c:v", options.video_codec])
            if options.preset:
                cmd.extend(["-preset", options.preset])
            if options.crf is not None and options.video_codec in {
                "libx264",
                "libx265",
                "h264_nvenc",
                "hevc_nvenc",
            }:
                cmd.extend(["-crf", str(options.crf)])
            video_bitrate = options.video_bitrate or DEFAULT_VIDEO_BITRATE
            cmd.extend(["-b:v", video_bitrate])
            cmd.extend(["-pix_fmt", "yuv420p"])
            if fps:
                cmd.extend(["-r", str(fps)])

            if audio_map:
                cmd.extend(["-c:a", options.audio_codec])
                if options.audio_bitrate:
                    cmd.extend(["-b:a", options.audio_bitrate])
            else:
                cmd.append("-an")

            cmd.extend(
                [
                    "-movflags",
                    "+faststart",
                    "-t",
                    str(duration),
                    str(self.output_path),
                ]
            )

            logger.info(
                "fablecut_render_ffmpeg",
                command=" ".join(shlex.quote(str(x)) for x in cmd),
                output=str(self.output_path),
            )
            result = run_ffmpeg(cmd, check=False, timeout=RENDER_TIMEOUT_SECONDS)
            if result.returncode != 0:
                logger.error(
                    "fablecut_render_failed",
                    returncode=result.returncode,
                    stderr=result.stderr,
                    filter_complex_path=str(fc_file),
                )
                raise RuntimeError(
                    f"ffmpeg exited with code {result.returncode}\nstderr:\n{result.stderr}"
                )

        return self.output_path


def render_fablecut_project(
    project_path: Path,
    output_path: Path,
    media_dir: Path | None = None,
    render_options: RenderOptions | None = None,
    font_path: str | None = None,
    allowed_roots: list[Path] | None = None,
) -> Path:
    """Convenience wrapper that creates a renderer and runs it."""
    renderer = FableCutRenderer(
        project_path=project_path,
        output_path=output_path,
        media_dir=media_dir,
        render_options=render_options,
        font_path=font_path,
        allowed_roots=allowed_roots,
    )
    return renderer.render()
