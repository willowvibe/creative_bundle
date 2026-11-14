#!/usr/bin/env python3
"""Compose a musical video directly with FFmpeg.

This bypasses Remotion to give deterministic control over:
- smooth crossfade transitions between segments
- Ken Burns motion on still images only (no motion on video clips)
- plain scaling/cropping for video clips
- reliable music audio muxing (only the supplied music file is used as audio)
- 50% slow-motion on video clips and 50% reduced Ken Burns amplitude

Example:
    python scripts/compose_musical_video.py \
        --music ./Shiva_Lingashtakam_-_Meditative_Chant.mp3 \
        --media-dir ./visuals \
        --output ./ligastakam_final.mp4 \
        --segment-seconds 8.0 \
        --transition-duration 1.5 \
        --width 1920 --height 1080
"""

from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
from pathlib import Path

from video_editor_core.musical import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    discover_media,
)
from video_editor_ffmpeg.probe import MediaInfoError, probe_duration

# Kept as module-level aliases for callers that imported the old names.
VIDEO_EXTS = VIDEO_EXTENSIONS
IMAGE_EXTS = IMAGE_EXTENSIONS


def _resolve(path: Path) -> Path:
    # Path.resolve(strict=False) (the default since Python 3.6) does not raise
    # for non-existent paths, so the previous try/except OSError was dead code
    # (#222). Resolve unconditionally.
    return path.resolve()


def _probe_duration(path: Path) -> float:
    """Probe the duration of a media file via the shared helper (#279)."""
    try:
        return probe_duration(path)
    except MediaInfoError:
        return 0.0
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned non-numeric duration for {path}") from exc


def _discover_media(
    media_dir: Path,
    exclude_paths: set[Path] | None = None,
) -> list[Path]:
    """Paths for the legacy filtergraph; discovery itself lives in core."""
    return [source.path for source in discover_media(media_dir, exclude=exclude_paths or ())]


def _build_segment_filter(
    input_label: str,
    path: Path,
    start: float,
    duration: float,
    width: int,
    height: int,
    fps: int,
    motion_index: int,
) -> str:
    """Return a filtergraph snippet that turns one input into a trimmed/scaled segment.

    Images get a slow Ken Burns zoom+pan via the zoompan filter. Videos (and
    animated GIFs) get plain scaling/cropping to the target dimensions and are
    trimmed to the segment duration.
    """
    ext = path.suffix.lower().lstrip(".")

    if ext in IMAGE_EXTS:
        # Slow alternating zoom directions to keep motion subtle.
        zoom_in = motion_index % 2 == 0
        # Ken Burns slowed by 50%: halve the zoom/pan amplitude so the same
        # wall-clock segment feels calmer while still covering the full range.
        # zoompan syntax: d=frames, s=output size, z='zoom expression'
        frames = int(round(duration * fps))
        scale_delta = 0.04
        pan_delta = 0.01
        start_scale = 1.0 if zoom_in else 1.0 + scale_delta
        end_scale = 1.0 + scale_delta if zoom_in else 1.0
        z_expr = f"{start_scale}+({end_scale}-{start_scale})*on/{frames}"
        # Pre-scale to cover the target aspect ratio and center-crop to exact
        # target dimensions so a non-matching aspect (e.g. 4:3 into 16:9) is
        # not stretched by zoompan (#205). After crop the frame is exactly
        # WxH, so zoompan's iw/ih equal the target.
        #
        # Bug #227: FFmpeg 6.1+ validates the static expression form of
        # ``x=``/``y=`` before runtime and rejects the runtime-only ``z``
        # variable as an undefined constant
        # ("Undefined constant or missing '(' in 'z)'"). Workaround: spell the
        # zoom out inline in terms of ``on`` (no ``z`` reference).
        #
        # The crop is centred on the scaled frame: ``(iw - iw/z) / 2``. The
        # previous expression dropped that term, which anchored the zoom at
        # the top-left even though the comment called it centred. The pan rides
        # on top of the centring offset and is zero at the start scale.
        if zoom_in:
            pan_start = -pan_delta * (1.0 - 1.0 / start_scale)
            pan_end = pan_delta * (1.0 - 1.0 / end_scale)
        else:
            pan_start = pan_delta * (1.0 - 1.0 / start_scale)
            pan_end = -pan_delta * (1.0 - 1.0 / end_scale)
        x_expr = f"(iw-iw/({z_expr}))/2+({pan_start}+({pan_end}-{pan_start})*on/{frames})*iw"
        y_expr = f"(ih-ih/({z_expr}))/2"
        # zoompan outputs a stream of the requested size; then we trim to the
        # segment duration.
        return (
            f"{input_label}"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps={fps},"
            f"trim=start=0:end={duration},setpts=PTS-STARTPTS[seg{motion_index}]"
        )

    # Video: scale to cover the target frame while preserving aspect ratio, then crop.
    # scale with force_original_aspect_ratio=increase scales until both dimensions
    # cover the target; crop then trims the overflow to exact target size.
    # Slow the clip to 50%: use half the raw source duration, then stretch
    # timestamps by 2x so the same segment slot plays at half speed.
    # Bug #194: if the source is shorter than ``duration * 0.5`` the slowed
    # segment comes up short and the xfade chain's ``trim=0:{source_dur}``
    # would have nothing to trim into. ``tpad=stop_mode=clone`` freezes the
    # last frame for up to ``duration`` extra seconds so the segment is
    # always at least ``duration`` long; the xfade trim then cuts it to
    # exactly ``source_dur``. Matches the documented "shorter videos freeze
    # on last frame" caveat.
    return (
        f"{input_label}"
        f"trim=start=0:end={duration * 0.5},setpts=2*(PTS-STARTPTS),"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"setsar=1,"
        f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
        f"tpad=stop_mode=clone:stop_duration={duration},"
        f"fps={fps}[seg{motion_index}]"
    )


def _build_xfade_chain(
    source_durations: list[float],
    segment_duration: float,
    transition_duration: float,
    fps: int,
) -> str:
    """Build the xfade chain that concatenates segment outputs with transitions.

    Each transition overlaps the tail of one segment with the head of the next.
    Internal segments are trimmed to ``segment_duration + transition_duration``
    so they provide enough material for the outgoing transition; the last
    segment is trimmed to whatever remains of the music. The resulting
    timeline is approximately ``num_segments * segment_duration`` (truncated
    to the music length via ``-shortest``).

    Bug #193: the offset for transition ``i`` is computed from the *actual*
    running output duration (``prev_duration - transition``), not a hard-coded
    ``segment_duration * (i + 1)``. When an intermediate segment's
    ``source_dur`` is clamped by the music length (which happens for music
    durations in ``(N*segment_duration, N*segment_duration + transition_duration)``),
    it contributes less than ``segment_duration + transition_duration``, so a
    hard-coded offset would overshoot the previous xfade output and FFmpeg would
    hard-cut, freeze, or error. The transition duration is also clamped to the
    material available on both sides so neither input runs out mid-crossfade.
    """
    segment_count = len(source_durations)
    if segment_count == 1:
        return f"[seg0]trim=0:{source_durations[0]},setpts=PTS-STARTPTS[seg0_t];[seg0_t]"

    parts: list[str] = []
    for i, dur in enumerate(source_durations):
        parts.append(f"[seg{i}]trim=0:{dur},setpts=PTS-STARTPTS[seg{i}_t]")

    styles = ["fade", "wipeleft", "wiperight", "slideleft", "radial", "pixelize"]
    # Running cumulative duration of the previous xfade output (or seg0_t for
    # the first transition). Updated after each xfade: out{i+1} duration is
    # ``offset + source_durations[i+1]``.
    prev_duration = source_durations[0]
    for i in range(segment_count - 1):
        a = f"seg{i}_t" if i == 0 else f"out{i}"
        b = f"seg{i + 1}_t"
        b_dur = source_durations[i + 1]
        # The transition overlaps the tail of ``a`` with the head of ``b``; it
        # cannot be longer than either side's available material.
        trans = min(transition_duration, prev_duration, b_dur)
        offset = prev_duration - trans
        style = styles[i % len(styles)]
        parts.append(
            f"[{a}][{b}]xfade=transition={style}:duration={trans}:offset={offset}[out{i + 1}]"
        )
        prev_duration = offset + b_dur

    return ";".join(parts) + f";[out{segment_count - 1}]"


def _build_filtergraph(
    media_paths: list[Path],
    segment_duration: float,
    transition_duration: float,
    music_duration: float,
    width: int,
    height: int,
    fps: int,
) -> tuple[str, float, int]:
    """Return (complex_filter, total_duration, number_of_segments)."""
    if segment_duration <= 0:
        raise ValueError("segment_duration must be positive")
    # Bug #225: transition_duration=0.0 used to pass validation but
    # ``xfade=duration=0`` is rejected by FFmpeg. Reject non-positive values
    # up front with a clear error instead of failing inside FFmpeg.
    if transition_duration <= 0 or transition_duration >= segment_duration:
        raise ValueError("transition_duration must be positive and less than segment_duration")

    num_segments = max(1, math.ceil(music_duration / segment_duration))
    total_duration = num_segments * segment_duration

    segment_defs: list[str] = []
    source_durations: list[float] = []
    for i in range(num_segments):
        media = media_paths[i % len(media_paths)]
        start = i * segment_duration
        end = min(start + segment_duration, music_duration)
        if end <= start:
            break
        visible_dur = end - start
        # Internal segments need extra tail material for the outgoing transition.
        extra = transition_duration if i < num_segments - 1 else 0.0
        source_dur = min(visible_dur + extra, music_duration - start)
        source_durations.append(source_dur)
        segment_defs.append(
            _build_segment_filter(
                input_label=f"[{i}:v]",
                path=media,
                start=start,
                duration=source_dur,
                width=width,
                height=height,
                fps=fps,
                motion_index=i,
            )
        )

    real_num_segments = len(segment_defs)
    xfade_part = _build_xfade_chain(
        source_durations=source_durations,
        segment_duration=segment_duration,
        transition_duration=transition_duration,
        fps=fps,
    )
    video_chain = ";".join(segment_defs)
    if video_chain:
        full_filter = f"{video_chain};{xfade_part}format=yuv420p[final_v]"
    else:
        # Fallback: black screen.
        full_filter = f"color=c=black:s={width}x{height}:d={music_duration}:r={fps}[final_v]"

    return full_filter, total_duration, real_num_segments


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg command, surfacing stderr in the exception on failure (#208).

    ``subprocess.run(..., check=True)`` without ``capture_output`` raises a bare
    ``CalledProcessError`` showing only the return code; if stderr is
    redirected by a wrapper the user has no idea why FFmpeg failed. Capture
    stderr and re-raise with it included. The diagnostic print uses
    ``shlex.join`` so paths with spaces are not mangled.
    """
    print("Running:\n", shlex.join(cmd), flush=True)
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {completed.returncode}\nstderr:\n{completed.stderr}"
        )


def compose(
    music_path: Path,
    media_dir: Path,
    output_path: Path,
    segment_seconds: float = 8.0,
    transition_seconds: float = 1.5,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    crf: int = 23,
    audio_bitrate: str = "192k",
) -> Path:
    # Bug #221: give clear errors for missing inputs instead of an opaque
    # ffprobe CalledProcessError or a bare FileNotFoundError from iterdir().
    if not music_path.is_file():
        raise FileNotFoundError(f"Music file not found: {music_path}")
    if not media_dir.is_dir():
        raise NotADirectoryError(f"Media directory not found: {media_dir}")

    # Bug #204: every engine FFmpeg helper creates the output parent before
    # invoking ffmpeg; this script did not, so ``--output ./subdir/final.mp4``
    # with a missing ``./subdir`` failed with a confusing ffmpeg error.
    output_path.parent.mkdir(parents=True, exist_ok=True)

    music_duration = _probe_duration(music_path)
    media_paths = _discover_media(
        media_dir,
        exclude_paths={music_path, output_path},
    )

    complex_filter, _, _ = _build_filtergraph(
        media_paths=media_paths,
        segment_duration=segment_seconds,
        transition_duration=transition_seconds,
        music_duration=music_duration,
        width=width,
        height=height,
        fps=fps,
    )

    # Build ffmpeg command. One -i per segment source. The same file may be reused
    # across segments, but ffmpeg needs separate inputs for the filtergraph labels.
    num_segments = max(1, math.ceil(music_duration / segment_seconds))
    cmd = ["ffmpeg", "-y"]
    for i in range(num_segments):
        media = media_paths[i % len(media_paths)]
        cmd.extend(["-i", str(media)])
    # Add music as the last input.
    cmd.extend(["-i", str(music_path)])

    music_input_index = num_segments

    cmd.extend(
        [
            "-filter_complex",
            complex_filter,
            "-map",
            "[final_v]",
            "-map",
            f"{music_input_index}:a",
            "-c:v",
            video_codec,
            "-crf",
            str(crf),
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            audio_codec,
            "-b:a",
            audio_bitrate,
            "-ar",
            "48000",
            "-shortest",
            str(output_path),
        ]
    )

    _run_ffmpeg(cmd)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose a musical video with FFmpeg")
    parser.add_argument("--music", required=True, type=Path, help="Path to music/audio file")
    parser.add_argument("--media-dir", required=True, type=Path, help="Directory with images/clips")
    parser.add_argument("--output", required=True, type=Path, help="Output MP4 path")
    parser.add_argument("--segment-seconds", type=float, default=8.0)
    parser.add_argument("--transition-duration", type=float, default=1.5)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args(argv)

    # Bug #220: validate dimensions/fps at parse time so failures are not
    # delayed to ffmpeg (``--fps 0`` → ``zoompan=d=0``; ``--width 0`` →
    # ``scale=0:0`` which ffmpeg rejects).
    if args.width <= 0 or args.height <= 0:
        parser.error("--width and --height must be positive integers")
    if args.fps <= 0:
        parser.error("--fps must be a positive integer")

    compose(
        music_path=args.music,
        media_dir=args.media_dir,
        output_path=args.output,
        segment_seconds=args.segment_seconds,
        transition_seconds=args.transition_duration,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )
    print(f"Done: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
