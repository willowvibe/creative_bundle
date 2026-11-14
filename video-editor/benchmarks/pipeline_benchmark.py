#!/usr/bin/env python
"""Time the skills pipeline end-to-end (time-to-export).

Generates a synthetic talking-head-style clip (testsrc video + sine audio),
runs ``SkillsPipeline`` against it, and prints the wall-clock time per stage
group. The render graph is the canonical path; this measures it end-to-end.

Usage:
    uv run python benchmarks/pipeline_benchmark.py --seconds 60
    uv run python benchmarks/pipeline_benchmark.py --seconds 600 --preset short_explainer
    uv run python benchmarks/pipeline_benchmark.py --source projects/day-master-deck/voiced.mp4 \
        --transcript projects/day-master-deck/narration_words.json --skip-music

The synthetic clip is a proxy for a real recording; ``--source`` runs the
benchmark on a real file with its transcript and measures the same stages.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from pathlib import Path

from video_editor_ffmpeg.run import run_ffmpeg

REPO_ROOT = Path(__file__).resolve().parents[1]


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise SystemExit(f"{binary} is required but not on PATH")


def _make_clip(path: Path, seconds: int, width: int, height: int, fps: int) -> None:
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size={width}x{height}:rate={fps}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=600,
    )


def _transcript(seconds: int) -> dict[str, object]:
    words = []
    t = 0.0
    i = 0
    while t < seconds:
        words.append({"word": f"w{i}", "start": t, "end": t + 0.4, "probability": 0.95})
        t += 0.5
        i += 1
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": words,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60, help="clip duration")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--preset", default="short_explainer")
    parser.add_argument("--source", type=Path, help="real source video instead of a synthetic clip")
    parser.add_argument("--transcript", type=Path, help="transcript JSON for --source")
    parser.add_argument(
        "--skip-music",
        action="store_true",
        help="do not auto-discover or generate music (matches the parity benchmark)",
    )
    args = parser.parse_args()

    _require("ffmpeg")
    if args.source is not None:
        if not args.source.is_file():
            raise SystemExit(f"source not found: {args.source}")
        if args.transcript is None or not args.transcript.is_file():
            raise SystemExit("--source requires an existing --transcript JSON")

    # Import lazily so --help works without the workspace installed.
    import asyncio
    import json

    from video_editor_ffmpeg.probe import probe_media
    from video_editor_pipelines.pipeline import SkillsPipeline
    from video_editor_state.job import JobContext

    with tempfile.TemporaryDirectory(prefix="ve-bench-") as tmp:
        tmp_path = Path(tmp)
        if args.source is None:
            source = tmp_path / "source.mp4"
            print(f"Generating {args.seconds}s {args.width}x{args.height}@{args.fps} clip…")
            _make_clip(source, args.seconds, args.width, args.height, args.fps)
            transcript_path = tmp_path / "transcript.json"
            transcript_path.write_text(json.dumps(_transcript(args.seconds)), encoding="utf-8")
            seconds = float(args.seconds)
        else:
            source = args.source.resolve()
            transcript_path = args.transcript.resolve()
            info = probe_media(source)
            seconds = float(info.duration or 0.0)
            stream = info.video_stream or {}
            dimensions = f"{stream.get('width')}x{stream.get('height')}" if stream else "no video"
            print(f"Using real source {source} ({seconds:.1f}s, {dimensions})")

        ctx = JobContext(
            job_id="benchmark",
            work_root=tmp_path / "work",
            output_root=tmp_path / "output",
        )
        pipeline = SkillsPipeline(
            ctx,
            preset=args.preset,
            skip_music=args.skip_music,
        )

        print(f"Running pipeline (render-graph, preset={args.preset})…")
        start = time.perf_counter()
        result = asyncio.run(pipeline.run_async(source, transcript_path=transcript_path))
        elapsed = time.perf_counter() - start

        final = Path(result["final"])
        size_mb = final.stat().st_size / (1024 * 1024) if final.exists() else 0.0
        print(f"\nmode=render-graph preset={args.preset} source={seconds:.1f}s")
        print(f"time-to-export: {elapsed:.1f}s  ({seconds / elapsed:.2f}x realtime)")
        print(f"final: {final} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
