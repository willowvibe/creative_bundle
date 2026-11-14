#!/usr/bin/env python3
"""Render a musical video from an engine ScenePlan using direct FFmpeg.

This is the post-FableCut render path: after ``ve import-fablecut`` has
written an edited ``scene_plan_<name>.json``, this script reads it, composes
the visual timeline with crossfade transitions and Ken Burns motion, muxes the
master music track, and optionally burns lyric captions.

Example::

    python scripts/render_musical_plan.py \
        --plan projects/suryastakam/plan/scene_plan_fablecut.json \
        --music /home/harish/Videos/suryastakam/Sri_Suryashtakam_2026-07-05T055930.mp3 \
        --lyrics videos/2026-07-12/suryastakam/comfystudio/lyrics.txt \
        --output videos/2026-07-12/suryastakam/suryastakam_fablecut_final.mp4 \
        --transition 1.5
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {"mp4", "mov", "m4v", "mkv", "webm", "avi", "gif"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}


def _probe_duration(path: Path) -> float:
    from video_editor_ffmpeg.probe import MediaInfoError, probe_duration

    try:
        return probe_duration(path)
    except MediaInfoError:
        return 0.0


def _media_kind(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    raise ValueError(f"Unsupported media extension for {path}: {ext}")


def _build_segment_filter(
    input_label: str,
    path: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    motion_index: int,
) -> str:
    """Return a filtergraph snippet that turns one input into a trimmed/scaled segment."""
    kind = _media_kind(path)
    frames = max(1, int(round(duration * fps)))

    if kind == "image":
        zoom_in = motion_index % 2 == 0
        scale_delta = 0.04
        pan_delta = 0.01
        start_scale = 1.0 if zoom_in else 1.0 + scale_delta
        end_scale = 1.0 + scale_delta if zoom_in else 1.0
        z_expr = f"{start_scale}+({end_scale}-{start_scale})*on/{frames}"
        if zoom_in:
            pan_start = -pan_delta * (1.0 - 1.0 / start_scale)
            pan_end = pan_delta * (1.0 - 1.0 / end_scale)
        else:
            pan_start = pan_delta * (1.0 - 1.0 / start_scale)
            pan_end = -pan_delta * (1.0 - 1.0 / end_scale)
        x_expr = f"({pan_start}+({pan_end}-{pan_start})*on/{frames})*iw"
        y_expr = "(ih-oh)/2"
        return (
            f"{input_label}"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps={fps},"
            f"trim=start=0:end={duration},setpts=PTS-STARTPTS[seg{motion_index}]"
        )

    # Video / GIF: slow to 50%, trim to the requested scene duration, scale/crop.
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
    transition_duration: float,
    fps: int,
) -> str:
    """Build xfade chain concatenating segments with transitions."""
    segment_count = len(source_durations)
    if segment_count == 1:
        return f"[seg0]trim=0:{source_durations[0]},setpts=PTS-STARTPTS[seg0_t];[seg0_t]"

    parts: list[str] = []
    for i, dur in enumerate(source_durations):
        parts.append(f"[seg{i}]trim=0:{dur},setpts=PTS-STARTPTS[seg{i}_t]")

    styles = ["fade", "wipeleft", "wiperight", "slideleft", "radial", "pixelize"]
    prev_duration = source_durations[0]
    for i in range(segment_count - 1):
        a = f"seg{i}_t" if i == 0 else f"out{i}"
        b = f"seg{i + 1}_t"
        b_dur = source_durations[i + 1]
        trans = min(transition_duration, prev_duration, b_dur)
        offset = prev_duration - trans
        style = styles[i % len(styles)]
        parts.append(
            f"[{a}][{b}]xfade=transition={style}:duration={trans}:offset={offset}[out{i + 1}]"
        )
        prev_duration = offset + b_dur

    return ";".join(parts) + f";[out{segment_count - 1}]"


def _load_plan(plan_path: Path) -> tuple[list[Path], list[float]]:
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    paths: list[Path] = []
    durations: list[float] = []
    for scene in data.get("scenes", []):
        assets = scene.get("assets") or []
        visual = None
        for asset in assets:
            atype = asset.get("asset_type", "")
            if atype in ("image", "video"):
                visual = asset
                break
        if visual is None:
            raise ValueError(f"Scene {scene.get('id')} has no visual asset")
        local_path = visual.get("local_path") or visual.get("url_or_path")
        if not local_path:
            raise ValueError(f"Scene {scene.get('id')} visual asset has no path")
        p = Path(local_path)
        if not p.exists():
            raise FileNotFoundError(f"Scene {scene.get('id')} media not found: {p}")
        paths.append(p)
        durations.append(float(scene.get("duration_seconds", 0)))
    return paths, durations


def compose_from_plan(
    plan_path: Path,
    music_path: Path,
    output_path: Path,
    transition_duration: float = 1.5,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    video_bitrate: str = "6M",
    audio_bitrate: str = "192k",
    crf: int | None = None,
) -> Path:
    media_paths, scene_durations = _load_plan(plan_path)
    music_duration = _probe_duration(music_path)
    visual_duration = sum(scene_durations)
    total_duration = min(visual_duration, music_duration)

    if total_duration <= 0:
        raise ValueError("computed total duration must be positive")
    positive_durations = [d for d in scene_durations if d > 0]
    if not positive_durations:
        raise ValueError("scene plan has no positive-duration scenes")
    if transition_duration <= 0:
        raise ValueError("transition_duration must be positive")
    # The last scene may be a short tail, so it need not satisfy the transition
    # duration. Every scene that has an outgoing transition must be longer than
    # the transition, otherwise FFmpeg xfade runs out of material.
    for i, d in enumerate(positive_durations[:-1]):
        if transition_duration >= d:
            raise ValueError(
                f"transition_duration ({transition_duration}) must be less than "
                f"scene {i + 1} duration ({d}) because that scene has an outgoing transition"
            )

    # Each scene's source duration includes extra tail for the outgoing transition.
    source_durations: list[float] = []
    accumulated = 0.0
    for i, dur in enumerate(scene_durations):
        remaining = total_duration - accumulated
        visible_dur = min(dur, remaining)
        extra = (
            transition_duration
            if (i < len(scene_durations) - 1 and accumulated + dur < total_duration)
            else 0.0
        )
        source_dur = min(visible_dur + extra, remaining)
        source_durations.append(source_dur)
        accumulated += visible_dur
        if accumulated >= total_duration:
            break

    # Trim to the segments that actually fit.
    media_paths = media_paths[: len(source_durations)]
    segment_count = len(source_durations)

    segment_defs = [
        _build_segment_filter(
            input_label=f"[{i}:v]",
            path=media_paths[i],
            duration=source_durations[i],
            width=width,
            height=height,
            fps=fps,
            motion_index=i,
        )
        for i in range(segment_count)
    ]

    video_chain = ";".join(segment_defs)
    xfade_part = _build_xfade_chain(
        source_durations=source_durations,
        transition_duration=transition_duration,
        fps=fps,
    )
    full_filter = f"{video_chain};{xfade_part}format=yuv420p[final_v]"

    inputs: list[str] = []
    for p in media_paths:
        inputs.extend(["-i", str(p)])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    video_opts = ["-c:v", video_codec, "-b:v", video_bitrate]
    if crf is not None:
        video_opts.extend(["-crf", str(crf)])

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-i",
        str(music_path),
        "-filter_complex",
        full_filter,
        "-map",
        "[final_v]",
        "-map",
        f"{segment_count}:a",
        "-shortest",
        "-c:v",
        video_codec,
        "-b:v",
        video_bitrate,
        *([] if crf is None else ["-crf", str(crf)]),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        audio_codec,
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        "-t",
        str(total_duration),
        str(output_path),
    ]

    print("Running:\n", " ".join(shlex.quote(str(x)) for x in cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg exited with code {result.returncode}\nstderr:\n{result.stderr}")
    return output_path


def _burn_captions(
    base_video: Path,
    lyrics_path: Path,
    output: Path,
) -> Path:
    """Call scripts/burn_musical_captions.py to overlay lyrics."""
    script = Path(__file__).resolve().parent / "burn_musical_captions.py"
    cmd = [
        sys.executable,
        str(script),
        "--base-video",
        str(base_video),
        "--lyrics",
        str(lyrics_path),
        "--output",
        str(output),
    ]
    print("Running captions:\n", " ".join(shlex.quote(str(x)) for x in cmd), flush=True)
    subprocess.run(cmd, check=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a musical video from a ScenePlan.")
    parser.add_argument("--plan", required=True, type=Path, help="Path to scene_plan JSON.")
    parser.add_argument(
        "--music", required=True, type=Path, help="Path to master music/audio file."
    )
    parser.add_argument("--output", required=True, type=Path, help="Final output video path.")
    parser.add_argument(
        "--lyrics", type=Path, default=None, help="Optional lyrics file to burn as captions."
    )
    parser.add_argument(
        "--transition", type=float, default=1.5, help="Crossfade transition duration in seconds."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--video-bitrate", default="6M")
    parser.add_argument("--audio-bitrate", default="192k")
    args = parser.parse_args()

    temp_output = args.output.with_stem(args.output.stem + "_no_captions")
    compose_from_plan(
        plan_path=args.plan,
        music_path=args.music,
        output_path=temp_output,
        transition_duration=args.transition,
        width=args.width,
        height=args.height,
        fps=args.fps,
        video_bitrate=args.video_bitrate,
        audio_bitrate=args.audio_bitrate,
    )

    if args.lyrics and args.lyrics.is_file():
        _burn_captions(temp_output, args.lyrics, args.output)
        temp_output.unlink(missing_ok=True)
    else:
        shutil.move(str(temp_output), str(args.output))

    print(f"Final video: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
