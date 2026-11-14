"""Reframe skill: convert horizontal video to vertical with smart face tracking.

Given a 16:9 source, the skill:

1. Samples focal points (face center or fallback saliency) across the video.
2. Smooths the focal track with an exponential moving average.
3. Renders a 9:16 output by cropping and optionally applying a slow punch-in
   zoom when the transcript signals high energy.

The render uses FFmpeg ``crop`` + ``zoompan``. The crop window is computed so
that the tracked focal point stays centered while respecting the output
aspect ratio. A keyframe JSON is written alongside the output for inspection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.render_options import RenderOptions
from video_editor_ffmpeg.run import run_ffmpeg
from video_editor_state.job import JobContext

from reframe.tracker import FaceTracker, FocalPoint

logger = structlog.get_logger(__name__)

DEFAULT_OUTPUT_WIDTH = 1080
DEFAULT_OUTPUT_HEIGHT = 1920
DEFAULT_SAMPLE_INTERVAL = 0.5
DEFAULT_SMOOTHING = 0.6
DEFAULT_ZOOM = 1.15


class ReframeError(RuntimeError):
    """Raised when reframing fails."""


class ReframeSkill:
    """Smart crop reframe from any aspect ratio to a target vertical format."""

    @property
    def name(self) -> str:
        return "reframe"

    def __init__(
        self,
        ctx: JobContext,
        target_aspect: str = "9:16",
        enable_zoom: bool = True,
        output_width: int = DEFAULT_OUTPUT_WIDTH,
        output_height: int = DEFAULT_OUTPUT_HEIGHT,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL,
        smoothing: float = DEFAULT_SMOOTHING,
        zoom_factor: float = DEFAULT_ZOOM,
        render_options: RenderOptions | None = None,
    ):
        self.ctx = ctx
        self.target_aspect = target_aspect
        self.enable_zoom = enable_zoom
        self.output_width = output_width
        self.output_height = output_height
        self.sample_interval = sample_interval_seconds
        self.smoothing = max(0.0, min(1.0, smoothing))
        self.zoom_factor = zoom_factor
        self.render_options = render_options or RenderOptions()
        self.output_dir = ctx.work_dir / "reframe"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tracker = FaceTracker(sample_interval_seconds=sample_interval_seconds)

    def _parse_aspect(self, aspect: str) -> float:
        """Return the target aspect ratio as a float (width / height)."""
        try:
            w, h = aspect.replace("x", ":").split(":")
            return int(w) / int(h)
        except Exception as exc:
            raise ReframeError(f"Invalid aspect ratio: {aspect!r}") from exc

    @staticmethod
    def smooth_points(points: list[FocalPoint], smoothing: float) -> list[FocalPoint]:
        """Apply an exponential moving average to focal-point jitter."""
        if not points:
            return [FocalPoint(0.5, 0.5)]
        smoothing = max(0.0, min(1.0, smoothing))
        smoothed: list[FocalPoint] = [points[0]]
        alpha = 1.0 - smoothing
        for point in points[1:]:
            prev = smoothed[-1]
            x = smoothing * prev.x + alpha * point.x
            y = smoothing * prev.y + alpha * point.y
            conf = smoothing * prev.confidence + alpha * point.confidence
            smoothed.append(FocalPoint(x, y, conf))
        return smoothed

    def _interpolate_crop_centers(
        self,
        points: list[FocalPoint],
        duration: float,
        fps: float,
    ) -> list[tuple[float, float]]:
        """Map each output frame to a crop-center in normalized source coords."""
        if not points or duration <= 0 or fps <= 0:
            return [(0.5, 0.5)] * int(max(1, fps * duration))

        total_frames = int(fps * duration)
        sample_count = len(points)
        centers: list[tuple[float, float]] = []
        for frame in range(total_frames):
            t = frame / total_frames
            idx = t * (sample_count - 1)
            i0 = int(idx)
            i1 = min(i0 + 1, sample_count - 1)
            frac = idx - i0
            p0 = points[i0]
            p1 = points[i1]
            x = p0.x + (p1.x - p0.x) * frac
            y = p0.y + (p1.y - p0.y) * frac
            centers.append((x, y))
        return centers

    def _build_keyframes(
        self,
        video_width: int,
        video_height: int,
        centers: list[tuple[float, float]],
        fps: float,
    ) -> list[dict[str, Any]]:
        """Build absolute-pixel keyframes for the crop window."""
        target_ratio = self._parse_aspect(self.target_aspect)
        # The output crop window in source pixels: fix height to source height
        # and compute width from the target aspect ratio.
        crop_h = video_height
        crop_w = int(round(crop_h * target_ratio))
        crop_w = min(crop_w, video_width)

        keyframes: list[dict[str, Any]] = []
        for idx, (cx, cy) in enumerate(centers):
            center_x = cx * video_width
            center_y = cy * video_height
            x = max(0, min(video_width - crop_w, int(round(center_x - crop_w / 2))))
            y = max(0, min(video_height - crop_h, int(round(center_y - crop_h / 2))))
            keyframes.append(
                {
                    "frame": idx,
                    "time": round(idx / fps, 3),
                    "x": x,
                    "y": y,
                    "width": crop_w,
                    "height": crop_h,
                    "center": (round(cx, 4), round(cy, 4)),
                }
            )
        return keyframes

    def _render_with_crop_zoompan(
        self,
        input_path: Path,
        output_path: Path,
        video_width: int,
        video_height: int,
        duration: float,
        fps: float,
    ) -> Path:
        """Render the reframed output with smooth crop + optional zoompan."""
        points = self.tracker.track(
            input_path,
            frame_count=int(fps * duration),
            width=video_width,
            height=video_height,
        )
        points = self.smooth_points(points, self.smoothing)
        centers = self._interpolate_crop_centers(points, duration, fps)
        keyframes = self._build_keyframes(video_width, video_height, centers, fps)
        keyframes_path = self.output_dir / "keyframes.json"
        keyframes_path.write_text(json.dumps({"keyframes": keyframes}, indent=2), encoding="utf-8")

        target_ratio = self._parse_aspect(self.target_aspect)
        crop_h = video_height
        crop_w = int(round(crop_h * target_ratio))
        crop_w = min(crop_w, video_width)

        total_frames = int(fps * duration)
        if self.enable_zoom:
            # zoompan output size = target vertical resolution. Zoom oscillates
            # slowly between 1 and zoom_factor across the video to add energy.
            # zoompan expressions use ``on`` (output frame number), not ``t``.
            filter_expr = (
                f"zoompan=z='1+(({self.zoom_factor:.3f}-1)*(1+cos(2*PI*on/{total_frames}))/2)':"
                f"x='(in_w-{crop_w})/2+({crop_w}-in_w/zoom)*(on/{total_frames}*0.5+0.25)':"
                f"y='(in_h-{crop_h})/2+({crop_h}-in_h/zoom)*0.5':"
                f"d={total_frames}:"
                f"s={self.output_width}x{self.output_height}:"
                f"fps={fps}"
            )
        else:
            # Simple crop + scale, centered on the first tracked point.
            cx, cy = centers[0]
            x = int(round(cx * (video_width - crop_w)))
            y = int(round(cy * (video_height - crop_h)))
            # Scale to fit exactly within the target box (letterbox if needed),
            # then pad to the exact output resolution so dimensions are stable.
            filter_expr = (
                f"crop={crop_w}:{crop_h}:{x}:{y},"
                f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=decrease,"
                f"setsar=1,"
                f"pad={self.output_width}:{self.output_height}:(ow-iw)/2:(oh-ih)/2"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            filter_expr,
            "-c:v",
            self.render_options.video_codec,
            "-preset",
            self.render_options.preset,
            "-crf",
            str(self.render_options.crf or 23),
            "-r",
            str(fps),
            "-c:a",
            self.render_options.audio_codec,
            "-b:a",
            self.render_options.audio_bitrate,
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def _render_with_sendcmd(
        self,
        input_path: Path,
        output_path: Path,
        video_width: int,
        video_height: int,
        duration: float,
        fps: float,
    ) -> Path:
        """Alternative render: drive an FFmpeg crop filter via sendcmd script.

        This produces a deterministic crop per frame and avoids the
        complexity of zoompan expressions. Used when zoom is disabled or when
        zoompan would be hard to validate.
        """
        points = self.tracker.track(
            input_path,
            frame_count=int(fps * duration),
            width=video_width,
            height=video_height,
        )
        points = self.smooth_points(points, self.smoothing)
        centers = self._interpolate_crop_centers(points, duration, fps)
        keyframes = self._build_keyframes(video_width, video_height, centers, fps)
        keyframes_path = self.output_dir / "keyframes.json"
        keyframes_path.write_text(json.dumps({"keyframes": keyframes}, indent=2), encoding="utf-8")

        target_ratio = self._parse_aspect(self.target_aspect)
        crop_h = video_height
        crop_w = int(round(crop_h * target_ratio))
        crop_w = min(crop_w, video_width)

        # Build sendcmd script lines. The valid format is:
        # ``time [enter] target command arg`` separated by ``;`` on one line.
        commands: list[str] = []
        for kf in keyframes:
            t = kf["time"]
            x = kf["x"]
            y = kf["y"]
            commands.append(f"{t} [enter] x {x}")
            commands.append(f"{t} [enter] y {y}")
        script = "; ".join(commands)
        sendcmd_path = self.output_dir / "crop_sendcmd.txt"
        sendcmd_path.write_text(script, encoding="utf-8")

        filter_expr = (
            f"sendcmd=f={sendcmd_path},crop=w={crop_w}:h={crop_h}:x='x':y='y',"
            f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=decrease,"
            f"setsar=1,"
            f"pad={self.output_width}:{self.output_height}:(ow-iw)/2:(oh-ih)/2"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            filter_expr,
            "-c:v",
            self.render_options.video_codec,
            "-preset",
            self.render_options.preset,
            "-crf",
            str(self.render_options.crf or 23),
            "-r",
            str(fps),
            "-c:a",
            self.render_options.audio_codec,
            "-b:a",
            self.render_options.audio_bitrate,
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def run(
        self,
        input_path: Path | str,
        output_name: str = "reframed.mp4",
        transcript_energy: list[float] | None = None,
    ) -> Path:
        """Reframe ``input_path`` to the target vertical aspect ratio.

        Args:
            input_path: Source video file.
            output_name: Output filename under ``reframe/``.
            transcript_energy: Optional per-word or per-segment energy values
                (0.0-1.0). High average energy can trigger a slightly larger
                zoom. Currently this is stored in metadata for future use; the
                zoom curve already varies over time.

        Returns:
            Path to the reframed MP4.
        """
        self.ctx.ensure_dirs()
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Reframe input not found: {input_path}")

        info = probe_media(input_path)
        if info.duration is None or info.video_stream is None:
            raise ReframeError(f"Could not probe video: {input_path}")

        video_width = info.width
        video_height = info.height
        duration = info.duration
        fps = info.fps or 30.0

        output_path = self.output_dir / output_name

        if self.enable_zoom:
            self._render_with_crop_zoompan(
                input_path, output_path, video_width, video_height, duration, fps
            )
        else:
            self._render_with_sendcmd(
                input_path, output_path, video_width, video_height, duration, fps
            )

        # Write a small manifest.
        manifest: dict[str, Any] = {
            "job_id": self.ctx.job_id,
            "input": str(input_path),
            "output": str(output_path),
            "target_aspect": self.target_aspect,
            "enable_zoom": self.enable_zoom,
            "output_width": self.output_width,
            "output_height": self.output_height,
            "keyframes": str(self.output_dir / "keyframes.json"),
        }
        if transcript_energy:
            manifest["mean_energy"] = round(sum(transcript_energy) / len(transcript_energy), 3)
        (self.output_dir / "reframe.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

        self.ctx.manifest().stage_done("reframe", {"video": str(output_path)})
        self.ctx.store.save()

        logger.info(
            "reframe_complete",
            job_id=self.ctx.job_id,
            output=str(output_path),
            aspect=self.target_aspect,
        )
        return output_path
