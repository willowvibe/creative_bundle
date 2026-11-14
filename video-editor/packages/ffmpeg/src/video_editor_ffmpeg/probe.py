"""FFprobe helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import structlog

from video_editor_ffmpeg.run import run_ffmpeg

logger = structlog.get_logger(__name__)


class MediaInfoError(Exception):
    """Raised when required probe metadata is missing or invalid."""


class MediaInfo:
    """Parsed media metadata."""

    def __init__(self, path: Path | str, probe: dict[str, Any]):
        self.path = Path(path)
        self.format = probe.get("format", {})
        self.streams = probe.get("streams", [])
        self.video_stream = self._first_stream("video")
        self.audio_stream = self._first_stream("audio")

    def _first_stream(self, codec_type: str) -> dict[str, Any] | None:
        for stream in self.streams:
            if stream.get("codec_type") == codec_type:
                return cast(dict[str, Any], stream)
        return None

    @property
    def duration(self) -> float | None:
        duration = self.format.get("duration")
        if duration is not None:
            return float(duration)
        if self.video_stream and "duration" in self.video_stream:
            return float(self.video_stream["duration"])
        return None

    @property
    def width(self) -> int:
        return int(self.video_stream.get("width", 0)) if self.video_stream else 0

    @property
    def height(self) -> int:
        return int(self.video_stream.get("height", 0)) if self.video_stream else 0

    @property
    def fps(self) -> float:
        if not self.video_stream:
            return 0.0
        rate = self.video_stream.get("r_frame_rate", "0/1")
        num, den = map(int, rate.split("/"))
        return num / den if den else 0.0

    @property
    def has_audio(self) -> bool:
        return self.audio_stream is not None

    def duration_ms(self) -> int:
        """Return duration in milliseconds, raising if it is missing."""
        if self.duration is None:
            raise MediaInfoError(f"duration missing for {self.path}")
        return int(self.duration * 1000)


def probe_duration(path: Path | str) -> float:
    """Return a media file's duration in seconds, or raise ``MediaInfoError``.

    One implementation for every caller that used to carry its own ``ffprobe``
    argv and fallback (#279).
    """
    info = probe_media(path)
    if info.duration is None:
        raise MediaInfoError(f"duration missing for {path}")
    return float(info.duration)


def probe_media(path: Path | str) -> MediaInfo:
    """Run ffprobe and return a MediaInfo object."""
    path = Path(path)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    logger.info("probing_media", path=str(path))
    result = run_ffmpeg(cmd)
    data = json.loads(result.stdout)
    return MediaInfo(path, data)


def extract_audio(
    input_path: Path | str, output_path: Path | str, sample_rate: int = 16000
) -> Path:
    """Extract a mono WAV for transcription."""
    output = Path(output_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        str(output),
    ]
    logger.info("extracting_audio", input=str(input_path), output=str(output))
    run_ffmpeg(cmd)
    return output
