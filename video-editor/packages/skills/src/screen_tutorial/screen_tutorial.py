"""Screen tutorial polish skill.

Given a screen recording, the skill:
1. Tracks the cursor (real backend when available, deterministic fallback otherwise).
2. Detects click events from cursor dwell + audio spikes.
3. Renders a smooth zoom-to-cursor effect (ease in/out) using FFmpeg crop + scale.
4. Optionally burns click highlight rings and keystroke overlays.
5. Writes a metadata JSON describing the cursor track, clicks, and keystrokes.

The cursor tracking is designed so a real backend (template matching, mouse
metadata parser, display recorder stream) can be swapped in without changing
the public interface.
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

from screen_tutorial.click_detector import ClickEvent, detect_clicks
from screen_tutorial.cursor_tracker import CursorFrame, CursorTracker
from screen_tutorial.keystroke_overlay import Keystroke, render_keystrokes

logger = structlog.get_logger(__name__)

DEFAULT_ZOOM = 1.6
DEFAULT_EASE_SECONDS = 0.4
DEFAULT_OUTPUT_WIDTH = 1920
DEFAULT_OUTPUT_HEIGHT = 1080


class ScreenTutorialError(RuntimeError):
    """Raised when screen tutorial processing fails."""


def _ease_in_out(t: float) -> float:
    """Smoothstep ease-in-out, normalized to [0, 1]."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _smooth_track(frames: list[CursorFrame], window: int = 3) -> list[CursorFrame]:
    """Smooth cursor positions with a moving average to reduce jitter."""
    if not frames:
        return []
    if window <= 1:
        return frames
    smoothed: list[CursorFrame] = []
    half = window // 2
    for idx in range(len(frames)):
        lo = max(0, idx - half)
        hi = min(len(frames), idx + half + 1)
        xs = [f.x for f in frames[lo:hi]]
        ys = [f.y for f in frames[lo:hi]]
        clicks = [f for f in frames[lo:hi] if f.click]
        x = sum(xs) / len(xs)
        y = sum(ys) / len(ys)
        smoothed.append(CursorFrame(frames[idx].time, x, y, bool(clicks)))
    return smoothed


class ScreenTutorialSkill:
    """Polish a screen recording with zoom-to-cursor, click rings, keystrokes."""

    @property
    def name(self) -> str:
        return "screen_tutorial"

    def __init__(
        self,
        ctx: JobContext,
        *,
        enable_zoom: bool = True,
        enable_clicks: bool = True,
        enable_keystrokes: bool = True,
        zoom_factor: float = DEFAULT_ZOOM,
        ease_seconds: float = DEFAULT_EASE_SECONDS,
        output_width: int = DEFAULT_OUTPUT_WIDTH,
        output_height: int = DEFAULT_OUTPUT_HEIGHT,
        dwell_threshold_seconds: float = 0.15,
        render_options: RenderOptions | None = None,
        tracker: CursorTracker | None = None,
    ):
        self.ctx = ctx
        self.enable_zoom = enable_zoom
        self.enable_clicks = enable_clicks
        self.enable_keystrokes = enable_keystrokes
        self.zoom_factor = max(1.0, zoom_factor)
        self.ease_seconds = max(0.1, ease_seconds)
        self.output_width = output_width
        self.output_height = output_height
        self.dwell_threshold_seconds = dwell_threshold_seconds
        self.render_options = render_options or RenderOptions()
        self.tracker = tracker or CursorTracker()
        self.output_dir = ctx.work_dir / "screen_tutorial"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _detect_events(
        self,
        cursor_frames: list[CursorFrame],
        audio_path: Path | None,
    ) -> list[ClickEvent]:
        """Detect click events from cursor dwell + audio spikes."""
        if not self.enable_clicks:
            return []
        return detect_clicks(
            cursor_frames,
            audio_path=audio_path,
            dwell_threshold_seconds=self.dwell_threshold_seconds,
        )

    def _build_zoom_filter(
        self,
        video_width: int,
        video_height: int,
        cursor_frames: list[CursorFrame],
        duration: float,
        fps: float,
    ) -> str:
        """Build a zoompan filter that eases toward the cursor position.

        When zoom is disabled, return a plain scale filter.
        """
        if not self.enable_zoom or not cursor_frames:
            return (
                f"scale={self.output_width}:{self.output_height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={self.output_width}:{self.output_height}:(ow-iw)/2:(oh-ih)/2"
            )

        total_frames = max(1, int(duration * fps))
        smoothed = _smooth_track(cursor_frames)
        # Average cursor position drives the crop center.
        avg_cx = sum(f.x / video_width for f in smoothed) / len(smoothed)
        avg_cy = sum(f.y / video_height for f in smoothed) / len(smoothed)

        # zoompan expressions use ``on`` (output frame number). The zoom
        # eases in/out (smoothstep-style cosine) and the crop window tracks
        # the running cursor average.
        zoom = self.zoom_factor
        expr_parts = [
            f"z='1+{zoom - 1:.4f}*0.5*(1-cos(2*PI*on/{total_frames}))'",
            f"x='(iw-iw/zoom)*{avg_cx:.4f}'",
            f"y='(ih-ih/zoom)*{avg_cy:.4f}'",
            f"d={total_frames}",
            f"s={self.output_width}x{self.output_height}",
            f"fps={fps}",
        ]
        return "zoompan=" + ":".join(expr_parts)

    @staticmethod
    def _nearest_frame(frames: list[CursorFrame], t: float) -> CursorFrame:
        """Return the cursor frame closest to time ``t``."""
        if not frames:
            return CursorFrame(0.0, 0.5, 0.5)
        best = frames[0]
        best_delta = abs(best.time - t)
        for frame in frames:
            delta = abs(frame.time - t)
            if delta < best_delta:
                best = frame
                best_delta = delta
        return best

    def _render_base(self, input_path: Path, output_path: Path) -> Path:
        """Apply the zoom-to-cursor filter and write an intermediate file."""
        info = probe_media(input_path)
        duration = info.duration or 0.0
        fps = info.fps or 30.0
        video_width = info.width
        video_height = info.height

        cursor_frames = self.tracker.track(input_path)
        cursor_frames = _smooth_track(cursor_frames)
        filter_expr = self._build_zoom_filter(
            video_width, video_height, cursor_frames, duration, fps
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
            "-c:a",
            self.render_options.audio_codec,
            "-b:a",
            self.render_options.audio_bitrate,
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def _burn_click_rings(
        self, input_path: Path, output_path: Path, clicks: list[ClickEvent]
    ) -> Path:
        """Draw a highlight ring at each click position using drawbox filters."""
        if not self.enable_clicks or not clicks:
            # passthrough
            cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(output_path)]
            run_ffmpeg(cmd)
            return output_path

        info = probe_media(input_path)
        width = info.width or self.output_width
        height = info.height or self.output_height
        ring = max(8, int(round(min(width, height) * 0.03)))

        filters: list[str] = []
        for click in clicks:
            t = max(0.0, click.time)
            enable = f"between(t,{t:.3f},{t + 0.6:.3f})"
            cx = max(0, min(width, int(click.x)))
            cy = max(0, min(height, int(click.y)))
            x = max(0, cx - ring // 2)
            y = max(0, cy - ring // 2)
            filters.append(
                f"drawbox=x={x}:y={y}:w={ring}:h={ring}:color=white@0.9:t=4:enable='{enable}'"
            )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            ",".join(filters),
            "-c:v",
            self.render_options.video_codec,
            "-preset",
            self.render_options.preset,
            "-c:a",
            "copy",
            str(output_path),
        ]
        run_ffmpeg(cmd)
        return output_path

    def run(
        self,
        video_path: Path | str,
        *,
        keystrokes: list[Keystroke] | None = None,
        output_name: str = "polished.mp4",
    ) -> dict[str, Any]:
        """Polish ``video_path`` and return the output + metadata JSON.

        Args:
            video_path: Screen recording file.
            keystrokes: Optional explicit keystroke overlays.
            output_name: Output filename under ``screen_tutorial/``.

        Returns:
            Skill output dict with ``output`` and ``metadata`` paths plus
            the click/keystroke summaries.
        """
        self.ctx.ensure_dirs()
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Screen tutorial input not found: {video_path}")

        info = probe_media(video_path)
        if info.duration is None:
            raise ScreenTutorialError(f"Could not probe video: {video_path}")

        # Extract audio for click detection (if present).
        audio_path: Path | None = None
        if info.has_audio:
            audio_path = self.output_dir / "audio.wav"
            from video_editor_ffmpeg.probe import extract_audio

            try:
                extract_audio(video_path, audio_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("screen_tutorial_audio_extract_failed", error=str(exc))
                audio_path = None

        cursor_frames = self.tracker.track(video_path)
        cursor_frames = _smooth_track(cursor_frames)
        clicks = self._detect_events(cursor_frames, audio_path)

        stage = self._render_base(video_path, self.output_dir / "zoomed.mp4")
        if self.enable_keystrokes and keystrokes:
            stage = render_keystrokes(
                stage,
                self.output_dir / "keystroked.mp4",
                keystrokes,
                render_options=self.render_options,
            )
        if self.enable_clicks and clicks:
            stage = self._burn_click_rings(stage, self.output_dir / "clicked.mp4", clicks)

        output_path = self.output_dir / output_name
        if stage != output_path:
            import shutil

            shutil.move(str(stage), str(output_path))

        metadata_path = self.output_dir / "metadata.json"
        metadata = {
            "job_id": self.ctx.job_id,
            "input": str(video_path),
            "output": str(output_path),
            "enable_zoom": self.enable_zoom,
            "enable_clicks": self.enable_clicks,
            "enable_keystrokes": self.enable_keystrokes,
            "cursor": {
                "count": len(cursor_frames),
                "sample": (
                    {
                        "time": cursor_frames[0].time,
                        "x": cursor_frames[0].x,
                        "y": cursor_frames[0].y,
                    }
                    if cursor_frames
                    else None
                ),
            },
            "clicks": [{"time": round(c.time, 3), "x": c.x, "y": c.y} for c in clicks],
            "keystrokes": [
                {"text": k.text, "start": k.start, "end": k.end} for k in (keystrokes or [])
            ],
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        self.ctx.manifest().stage_done(
            "screen_tutorial",
            {"video": str(output_path), "metadata": str(metadata_path)},
        )
        self.ctx.store.save()

        logger.info(
            "screen_tutorial_complete",
            job_id=self.ctx.job_id,
            output=str(output_path),
            clicks=len(clicks),
            keystrokes=len(keystrokes or []),
        )
        return {
            "output": str(output_path),
            "metadata": str(metadata_path),
            "clicks": metadata["clicks"],
            "keystrokes": metadata["keystrokes"],
            "count": len(clicks),
        }
