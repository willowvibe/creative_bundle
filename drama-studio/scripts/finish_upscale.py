"""Finishing upscale for an episode's 720p clips: SeedVR2 on Modal, 1.5x to a native 1920x1080 (video-editor #324).

usage: finish_upscale.py <clips_dir> <out_dir> [--scale 1.5] [--only v01,v05] [--max-usd 20] [--yes]

Each clip goes through `rpc run seedvr2_upscale --modal` in the sibling runpod-comfyui repo (set RPC_REPO to move it).
Without --yes it only prints the plan and the price. Clips already in <out_dir> are skipped, so a stopped run resumes;
every finished clip is appended to <out_dir>/_upscale_runs.json. Assembly then reads <out_dir> instead of lanczos-
scaling 720p (v2_assemble's 1920x1080 FIT becomes a no-op).

Measured 2026-11-11 (Ep1 clips/lipsync/v05.mp4, L40S): 2x costs about $0.055 per output second with a cold start;
the price scales with output pixels, so 1.5x is about $0.031 per second. Flicker is under one grey level.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: Dollars per output second at 2x on Modal's L40S, cold start included (2026-11-11).
USD_PER_SECOND_AT_2X = 0.055
REPO = Path(__file__).resolve().parents[1]


def rpc_repo() -> Path:
    return Path(os.environ.get("RPC_REPO") or REPO.parent / "runpod-comfyui")


def seconds(clip: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out or 0)


def usd_for(duration: float, scale: float) -> float:
    """Price of one clip: output pixels relative to 2x times the measured 2x rate."""
    return duration * USD_PER_SECOND_AT_2X * (scale / 2.0) ** 2


def plan(clips_dir: Path, out_dir: Path, only: set[str] | None) -> list[Path]:
    """Clips still to upscale, in name order."""
    todo = []
    for clip in sorted(clips_dir.glob("*.mp4")):
        if only and clip.stem not in only:
            continue
        if (out_dir / clip.name).exists():
            continue
        todo.append(clip)
    return todo


def upscale(clip: Path, out_dir: Path, scale: float) -> Path:
    """Run one clip on Modal and move the result to out_dir/<clip name>."""
    with tempfile.TemporaryDirectory(prefix="seedvr2_") as tmp:
        subprocess.run(
            [
                "uv",
                "run",
                "rpc",
                "run",
                "seedvr2_upscale",
                "--modal",
                "--set",
                f"video={clip.resolve()}",
                "--set",
                f"scale={scale}",
                "-o",
                tmp,
            ],
            cwd=rpc_repo(),
            check=True,
        )
        produced = sorted(Path(tmp).glob("*.mp4"))
        if not produced:
            raise RuntimeError(f"seedvr2_upscale returned no video for {clip.name}")
        target = out_dir / clip.name
        shutil.move(str(produced[0]), target)
        return target


def record(out_dir: Path, entry: dict) -> None:
    ledger = out_dir / "_upscale_runs.json"
    rows = json.loads(ledger.read_text()) if ledger.exists() else []
    rows.append(entry)
    ledger.write_text(json.dumps(rows, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clips_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument(
        "--scale", type=float, default=1.5, help="1.5 turns 720p into 1080p (default)"
    )
    parser.add_argument("--only", default="", help="comma-separated clip names (stems)")
    parser.add_argument(
        "--max-usd", type=float, default=20.0, help="refuse a batch priced above this"
    )
    parser.add_argument(
        "--yes", action="store_true", help="spend: without it only the plan is printed"
    )
    args = parser.parse_args(argv)

    only = {name.strip() for name in args.only.split(",") if name.strip()} or None
    args.out_dir.mkdir(parents=True, exist_ok=True)
    todo = plan(args.clips_dir, args.out_dir, only)
    durations = {clip: seconds(clip) for clip in todo}
    total = sum(usd_for(d, args.scale) for d in durations.values())
    for clip, duration in durations.items():
        print(f"  {clip.name:24s} {duration:6.2f} s  ≈ ${usd_for(duration, args.scale):.2f}")
    print(
        f"{len(todo)} clip(s), {sum(durations.values()):.1f} s, ≈ ${total:.2f} at {args.scale}x on Modal"
    )
    if not todo:
        return 0
    if total > args.max_usd:
        print(f"refused: ≈ ${total:.2f} is above --max-usd {args.max_usd:.2f}", file=sys.stderr)
        return 2
    if not args.yes:
        print("dry run: add --yes to upscale")
        return 0
    for clip, duration in durations.items():
        started = time.monotonic()
        target = upscale(clip, args.out_dir, args.scale)
        record(
            args.out_dir,
            {
                "clip": clip.name,
                "seconds": round(duration, 2),
                "scale": args.scale,
                "wall_seconds": round(time.monotonic() - started, 1),
                "estimate_usd": round(usd_for(duration, args.scale), 3),
                "output": str(target),
            },
        )
        print(f"done {clip.name} -> {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
