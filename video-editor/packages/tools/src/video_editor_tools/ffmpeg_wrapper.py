"""Re-export of the idempotent FFmpeg wrapper for skills.

Additional helpers specific to the new architecture live here; core FFmpeg
operations continue to live in `video_editor_ffmpeg`.
"""

from __future__ import annotations

# Re-export the stable FFmpeg API so skills import from one place.
from video_editor_ffmpeg.captions import (
    drawtext_captions,
    generate_caption_segments,
    remap_transcript_to_cuts,
    write_srt,
)
from video_editor_ffmpeg.cuts import apply_cuts
from video_editor_ffmpeg.normalize import normalize_audio
from video_editor_ffmpeg.probe import extract_audio, probe_media
from video_editor_ffmpeg.proxy import make_preview, make_proxy

__all__ = [
    "apply_cuts",
    "drawtext_captions",
    "extract_audio",
    "generate_caption_segments",
    "make_preview",
    "make_proxy",
    "normalize_audio",
    "probe_media",
    "remap_transcript_to_cuts",
    "write_srt",
]
