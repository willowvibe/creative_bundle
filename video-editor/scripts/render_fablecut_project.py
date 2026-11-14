#!/usr/bin/env python3
"""Render a final video directly from a FableCut project.json.

This script is now a thin wrapper around ``video_editor_fablecut_bridge.renderer``.
The canonical engine command is ``ve render-fablecut``; this wrapper remains for
backwards compatibility and quick one-off renders.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from video_editor_fablecut_bridge.renderer import render_fablecut_project


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a final video from a FableCut project.json."
    )
    parser.add_argument(
        "--project",
        required=True,
        type=Path,
        help="Path to the edited FableCut project.json",
    )
    parser.add_argument(
        "--media",
        required=True,
        type=Path,
        help="Path to the FableCut media folder",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Final output video path",
    )
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument(
        "--font",
        type=str,
        default="/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    )
    parser.add_argument("--video-bitrate", default="6M")
    parser.add_argument("--audio-bitrate", default="192k")
    args = parser.parse_args()

    from video_editor_ffmpeg.render_options import RenderOptions

    render_options = RenderOptions(
        width=args.width,
        height=args.height,
        fps=args.fps,
        video_bitrate=args.video_bitrate,
        audio_bitrate=args.audio_bitrate,
    )
    render_fablecut_project(
        project_path=args.project,
        output_path=args.output,
        media_dir=args.media,
        render_options=render_options,
        font_path=args.font,
    )
    print(f"Final video: {args.output}")


if __name__ == "__main__":
    main()
