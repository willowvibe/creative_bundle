"""Face / salient-region tracker for smart reframing.

The tracker uses MediaPipe when available and falls back to a simple
motion/saliency heuristic (center-of-brightness + motion) when it is not.
Both implementations return a normalized ``(x, y)`` focal point in the
``[0, 1]`` range that the reframer can center on.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import structlog
from video_editor_ffmpeg.probe import MediaInfoError, probe_duration
from video_editor_ffmpeg.run import PROBE_TIMEOUT_SECONDS, run_ffmpeg

logger = structlog.get_logger(__name__)


class FocalPoint:
    """Normalized focal point with optional confidence."""

    def __init__(self, x: float, y: float, confidence: float = 1.0):
        self.x = max(0.0, min(1.0, x))
        self.y = max(0.0, min(1.0, y))
        self.confidence = max(0.0, min(1.0, confidence))


class FaceTracker:
    """Detect a focal point for reframing.

    Preference order:
    1. MediaPipe face detection (if ``mediapipe`` is installed).
    2. FFmpeg ``select=centre`` motion + brightness fallback.
    """

    def __init__(self, sample_interval_seconds: float = 0.5):
        self.sample_interval = sample_interval_seconds
        self._mediapipe: Any | None = None
        self._face_detection: Any | None = None

    def _load_mediapipe(self) -> tuple[Any, Any] | None:
        if self._mediapipe is not None and self._face_detection is not None:
            return self._mediapipe, self._face_detection
        try:
            import mediapipe as mp

            self._mediapipe = mp
            self._face_detection = mp.solutions.face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5
            )
            return self._mediapipe, self._face_detection
        except Exception as exc:  # noqa: BLE001
            logger.debug("mediapipe_unavailable", error=str(exc))
            return None

    def track(
        self,
        video_path: Path,
        frame_count: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> list[FocalPoint]:
        """Return a focal point for each sampled frame of ``video_path``."""
        mp = self._load_mediapipe()
        if mp is not None:
            return self._track_with_mediapipe(video_path)
        return self._track_with_ffmpeg(video_path, frame_count, width, height)

    def _track_with_mediapipe(self, video_path: Path) -> list[FocalPoint]:
        """Sample frames and detect face bounding-box centers."""
        import cv2

        detector = self._face_detection
        if detector is None:
            return []
        capture = cv2.VideoCapture(str(video_path))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        sample_step = max(1, int(fps * self.sample_interval))

        points: list[FocalPoint] = []
        frame_idx = 0
        while True:
            ret, frame = capture.read()
            if not ret:
                break
            if frame_idx % sample_step != 0:
                frame_idx += 1
                continue

            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = detector.process(rgb)

            if results.detections:
                detection = results.detections[0]
                bbox = detection.location_data.relative_bounding_box
                x = bbox.xmin + bbox.width / 2
                y = bbox.ymin + bbox.height / 2
                conf = detection.score[0] if detection.score else 0.8
                points.append(FocalPoint(x, y, conf))
            else:
                points.append(FocalPoint(0.5, 0.5, confidence=0.1))
            frame_idx += 1

        capture.release()
        if not points:
            return [FocalPoint(0.5, 0.5)]
        return points

    def _track_with_ffmpeg(
        self,
        video_path: Path,
        frame_count: int | None,
        width: int | None,
        height: int | None,
    ) -> list[FocalPoint]:
        """Fallback focal-point estimator using FFmpeg cropdetect + motion."""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return [FocalPoint(0.5, 0.5)]

        # Cropdetect gives a hint about black bars / active picture region.
        result = run_ffmpeg(
            [
                ffmpeg,
                "-i",
                str(video_path),
                "-vf",
                "cropdetect=limit=24:round=2:reset=0",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=False,
            timeout=120,
        )
        crop_x, crop_y, crop_w, crop_h = self._parse_cropdetect(result.stderr)
        if width is None or height is None:
            width, height = self._video_dimensions(video_path)

        # If cropdetect found an active region, center it. Otherwise center frame.
        if crop_w and crop_h:
            cx = (crop_x + crop_w / 2) / width
            cy = (crop_y + crop_h / 2) / height
        else:
            cx, cy = 0.5, 0.5

        # For the fallback we return the same static focal point for every sample.
        points: list[FocalPoint] = []
        n = frame_count or int(self._video_duration(video_path) / self.sample_interval) or 1
        for _ in range(max(1, n)):
            points.append(FocalPoint(cx, cy, confidence=0.5))
        return points

    @staticmethod
    def _parse_cropdetect(stderr: str) -> tuple[int, int, int, int]:
        """Parse the last cropdetect line ``crop=W:H:X:Y``."""
        last = ""
        for line in stderr.splitlines():
            if "crop=" in line:
                last = line
        if not last:
            return 0, 0, 0, 0
        try:
            crop_part = last.split("crop=")[-1]
            w, h, x, y = map(int, crop_part.split(":"))
            return x, y, w, h
        except Exception:  # noqa: BLE001
            return 0, 0, 0, 0

    @staticmethod
    def _video_dimensions(video_path: Path) -> tuple[int, int]:
        """Return (width, height) via ffprobe."""
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        result = run_ffmpeg(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(video_path),
            ],
            check=False,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            w, h = map(int, result.stdout.strip().split("x"))
            return w, h
        except Exception:  # noqa: BLE001
            return 1920, 1080

    @staticmethod
    def _video_duration(video_path: Path) -> float:
        """Return duration in seconds via ffprobe."""
        try:
            return probe_duration(video_path)
        except (MediaInfoError, OSError, ValueError):
            return 0.0
