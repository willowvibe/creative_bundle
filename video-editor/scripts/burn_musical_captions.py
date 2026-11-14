#!/usr/bin/env python3
"""Burn Devanagari lyrics onto a base video as lower-bottom pop-style captions.

Each non-empty line of the lyrics file becomes one caption. Time windows are
divided equally across the base video's duration, so a 180s video with 24
caption lines gives each line a 7.5s window. Section headers (e.g. "फलशृति")
are folded into the next non-empty line as a prefix; verse markers (e.g. the
trailing "॥ 1 ॥") are preserved as written.

Pop-style: each caption scales up (initial_fontsize -> target_fontsize) and
fades in (alpha 0 -> 1) over ``--pop-duration`` seconds, then holds static
for the remainder of its window. Lower-bottom position: x is centered, y is
``h - 2*text_h - 50`` (≈ 50px above the bottom edge). White text on a
translucent black rounded-rect backdrop for readability over varied imagery.

Typical usage::

    python scripts/burn_musical_captions.py \
        --base-video videos/2026-07-12/suryastakam/suryastakam_no_captions.mp4 \
        --lyrics     /home/harish/Videos/suryastakam/lyrics \
        --output     videos/2026-07-12/suryastakam/suryastakam_final.mp4

The default font is Noto Sans Devanagari Bold (Debian/Ubuntu path); override
via ``--font`` for other Devanagari-capable faces.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from video_editor_core.lyrics import parse_lyrics
from video_editor_ffmpeg.probe import probe_duration


def write_caption_files(captions: list[str], work_dir: Path) -> list[Path]:
    """Write each caption to its own UTF-8 text file (drawtext textfile=)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i, text in enumerate(captions):
        p = work_dir / f"cap_{i:02d}.txt"
        p.write_text(text, encoding="utf-8")
        out.append(p)
    return out


def build_filter_complex(
    caption_files: list[Path],
    total_duration: float,
    font: str,
    target_fontsize: int,
    initial_fontsize: int,
    pop_duration: float,
) -> str:
    """Construct the FFmpeg filter_complex string for the caption chain.

    Each caption is a drawtext layer with:
      - fontsize ramp (initial -> target) over pop_duration
      - alpha ramp (0 -> 1) over pop_duration
      - white text on translucent black box, centered, lower-bottom
      - enable= restricted to the caption's time window
    """
    n = len(caption_files)
    if n == 0:
        raise ValueError("at least one caption required")
    win = total_duration / n
    layers: list[str] = []
    for i, path in enumerate(caption_files):
        start = i * win
        end = (i + 1) * win if i < n - 1 else total_duration
        # FFmpeg's drawtext=textfile= needs a single-quoted path; escape any
        # literal single quotes so the parser doesn't terminate the string.
        safe_path = str(path).replace("'", "'\\''")
        fs_expr = (
            f"if(lt(t-{start},{pop_duration}),"
            f"{initial_fontsize}+({target_fontsize}-{initial_fontsize})*(t-{start})/{pop_duration},"
            f"{target_fontsize})"
        )
        alpha_expr = f"if(lt(t-{start},{pop_duration}),(t-{start})/{pop_duration},1)"
        layers.append(
            f"drawtext="
            f"fontfile='{font}'"
            f":textfile='{safe_path}'"
            f":fontsize='{fs_expr}'"
            f":fontcolor=white"
            f":alpha='{alpha_expr}'"
            f":box=1:boxcolor=black@0.55:boxborderw=20"
            f":x=(w-text_w)/2"
            f":y=h-2*text_h-50"
            f":enable='between(t,{start},{end})'"
        )

    # Chain all drawtext layers sequentially: base -> v1 -> v2 -> ... -> vout
    chain: list[str] = ["[0:v]format=yuv420p[base]"]
    last = "base"
    for i, layer in enumerate(layers):
        out_label = f"v{i + 1}"
        chain.append(f"[{last}]{layer}[{out_label}]")
        last = out_label
    chain.append(f"[{last}]format=yuv420p[vout]")
    return ";\n".join(chain)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Burn lower-bottom pop-style Devanagari captions onto a video.",
    )
    p.add_argument(
        "--base-video",
        required=True,
        type=Path,
        help="Input video to overlay captions on (audio is copied through).",
    )
    p.add_argument(
        "--lyrics",
        required=True,
        type=Path,
        help="UTF-8 text file; one non-empty line per caption.",
    )
    p.add_argument("--output", required=True, type=Path, help="Output MP4 path.")
    p.add_argument(
        "--font",
        default="/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        help="Devanagari-capable TTF/OTF path.",
    )
    p.add_argument(
        "--target-fontsize", type=int, default=40, help="Steady-state caption size in pixels."
    )
    p.add_argument(
        "--initial-fontsize", type=int, default=22, help="Pop-in starting size in pixels."
    )
    p.add_argument(
        "--pop-duration",
        type=float,
        default=0.35,
        help="Seconds for the pop-in (scale + alpha) animation.",
    )
    p.add_argument(
        "--work-dir",
        type=Path,
        default=Path("/tmp/musical_caption_work"),
        help="Scratch dir for per-caption text files (auto-cleaned each run).",
    )
    args = p.parse_args()

    for label, path in [
        ("base video", args.base_video),
        ("lyrics", args.lyrics),
        ("font", Path(args.font)),
    ]:
        if not path.is_file():
            print(f"{label} not found: {path}", file=sys.stderr)
            return 1

    captions = parse_lyrics(args.lyrics.read_text(encoding="utf-8"))
    if not captions:
        print("no captions parsed from lyrics", file=sys.stderr)
        return 1
    duration = probe_duration(args.base_video)
    win = duration / len(captions)
    print(f"parsed {len(captions)} captions over {duration:.2f}s ({win:.2f}s per caption)")
    for i, c in enumerate(captions):
        start = i * win
        end = (i + 1) * win if i < len(captions) - 1 else duration
        print(f"  {start:6.2f}-{end:6.2f}  {c[:70]}")

    # Clean the work dir so re-runs don't see stale cap_NN.txt files.
    if args.work_dir.exists():
        for f in args.work_dir.glob("cap_*.txt"):
            f.unlink()
    caption_files = write_caption_files(captions, args.work_dir)

    fc = build_filter_complex(
        caption_files,
        duration,
        args.font,
        args.target_fontsize,
        args.initial_fontsize,
        args.pop_duration,
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(args.base_video),
        "-filter_complex",
        fc,
        "-map",
        "[vout]",
        "-map",
        "0:a",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(args.output),
    ]
    print(f"\nrunning ffmpeg with {len(captions)} drawtext layers...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        print("STDERR (last 3000 chars):", file=sys.stderr)
        print(result.stderr[-3000:], file=sys.stderr)
        return result.returncode
    print(f"\nOK: {args.output}")
    print(f"size: {args.output.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
