"""Render-time encoding options used by FFmpeg helpers and skills.

This module is intentionally kept dependency-light: it does not import
``video_editor_config`` so ``video_editor_ffmpeg`` can be used by skills and
v1 engine code without pulling in the config package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RenderOptions:
    """Container for codec/bitrate/fps choices when re-encoding video/audio.

    Defaults match the historical hard-coded values used throughout the
    engine so that passing ``None`` (or omitting the argument) produces
    identical FFmpeg commands.
    """

    video_codec: str = "libx264"
    audio_codec: str = "aac"
    video_bitrate: str = ""
    audio_bitrate: str = "192k"
    fps: int | None = None
    crf: int | None = None
    width: int | None = None
    height: int | None = None
    preset: str = "fast"
    hwaccel: str = ""
    skip_hyperframes_lint: bool = False
    transition: str = "none"
    transition_duration: float = 0.3

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> RenderOptions:
        """Build from a plain dict such as ``ProjectConfig.render.model_dump()``.

        Unknown keys are ignored so callers can forward a full render
        section without worrying about extras like ``output_name_template``.
        """
        if not mapping:
            return cls()
        return cls(
            video_codec=mapping.get("video_codec", cls.video_codec),
            audio_codec=mapping.get("audio_codec", cls.audio_codec),
            video_bitrate=mapping.get("video_bitrate", cls.video_bitrate),
            audio_bitrate=mapping.get("audio_bitrate", cls.audio_bitrate),
            fps=mapping.get("fps", cls.fps),
            crf=mapping.get("crf", cls.crf),
            width=mapping.get("width", cls.width),
            height=mapping.get("height", cls.height),
            preset=mapping.get("preset", cls.preset),
            hwaccel=mapping.get("hwaccel", cls.hwaccel),
            transition=mapping.get("transition", cls.transition),
            transition_duration=mapping.get("transition_duration", cls.transition_duration),
        )


def _add_video_encode_args(args: list[str], options: RenderOptions) -> list[str]:
    """Append video encode flags to an FFmpeg argument list.

    The caller decides whether video is copied or re-encoded; when this
    helper is used the stream is re-encoded.
    """
    args.extend(["-c:v", options.video_codec])
    if options.preset:
        args.extend(["-preset", options.preset])
    if options.crf is not None and options.video_codec in {
        "libx264",
        "libx265",
        "h264_nvenc",
        "hevc_nvenc",
    }:
        args.extend(["-crf", str(options.crf)])
    if options.video_bitrate:
        args.extend(["-b:v", options.video_bitrate])
    if options.fps is not None:
        args.extend(["-r", str(options.fps)])
    return args


def _add_audio_encode_args(args: list[str], options: RenderOptions) -> list[str]:
    """Append audio encode flags to an FFmpeg argument list.

    The caller decides whether audio is copied or re-encoded.
    """
    args.extend(["-c:a", options.audio_codec])
    if options.audio_bitrate:
        args.extend(["-b:a", options.audio_bitrate])
    return args
