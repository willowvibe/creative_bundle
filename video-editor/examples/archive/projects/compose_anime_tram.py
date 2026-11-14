#!/usr/bin/env python3
"""Composer for a dreamy anime-style tram station scene.

Generates a short cinematic video via the primary PoYo.ai provider
(default: ``seedance-2-fast``), then mixes in a soft synthesized ambient
pad so the final MP4 has audio. Set ``USE_FAL=1`` to use the legacy FAL
Seedance path instead.

Output: videos/<today>/anime-tram-station/renders/anime-tram-station.mp4
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from video_editor_asset_ingestion.config import ProviderConfig
from video_editor_asset_ingestion.models import AssetRequest, AssetResult, AssetType
from video_editor_asset_ingestion.providers.visual.fal_ai import FalAIProvider
from video_editor_asset_ingestion.providers.visual.poyo_ai import PoyoAIProvider
from video_editor_ffmpeg.probe import probe_duration, probe_media

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env", override=False)
TODAY = date.today().isoformat()
PROJECT_DIR = REPO_ROOT / "videos" / TODAY / "anime-tram-station"
WORK_DIR = PROJECT_DIR / "work"
RENDERS_DIR = PROJECT_DIR / "renders"
FINAL_PATH = RENDERS_DIR / "anime-tram-station.mp4"

PROMPT = (
    "A realistic yet dreamy anime-style scene of a classic tram arriving at a "
    "picturesque station surrounded by lush greenery and vibrant trees. Intricately "
    "detailed anime characters step off the tram while others gracefully hop on. "
    "Soft, ethereal sunlight filters through the foliage, creating magical god rays "
    "and a cinematic atmosphere. 16:9, smooth motion, high detail, tranquil mood."
)

FAL_VIDEO_MODEL = "bytedance/seedance-2.0/mini/text-to-video"
POYO_VIDEO_MODEL = "seedance-2-fast"


def run(cmd: list[str], **kwargs: Any) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def ensure_dirs() -> None:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)


async def generate_video_poyo(raw_path: Path) -> AssetResult:
    """Generate the scene via PoYo.ai (primary provider)."""
    config = ProviderConfig(
        api_key_env="POYO_API_KEY",
        default_model="gpt-image-2",
        provider_options={
            "video_model": POYO_VIDEO_MODEL,
            "poll_interval_seconds": 2.0,
            "poll_timeout_seconds": 600.0,
        },
    )
    request = AssetRequest(
        asset_id="tram_station_scene",
        job_id="anime-tram-station",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt=PROMPT,
        provider="poyo_ai",
        output_ext="mp4",
        params={
            "model_kwargs": {
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "duration": 10,
                "generate_audio": False,
            }
        },
    )
    async with PoyoAIProvider(config) as provider:
        return await provider.generate(request, raw_path)


async def generate_video_fal(raw_path: Path) -> AssetResult:
    """Generate the scene via FAL.ai (legacy fallback)."""
    config = ProviderConfig(
        api_key_env="FAL_KEY",
        provider_options={
            "video_model": FAL_VIDEO_MODEL,
            "poll_interval_seconds": 2.0,
            "poll_timeout_seconds": 600.0,
        },
    )
    request = AssetRequest(
        asset_id="tram_station_scene",
        job_id="anime-tram-station",
        scene_id="scene-1",
        asset_type=AssetType.VIDEO,
        prompt=PROMPT,
        provider="fal_ai",
        output_ext="mp4",
        params={
            "model_kwargs": {
                "generate_audio": False,
                "aspect_ratio": "16:9",
            }
        },
    )
    async with FalAIProvider(config) as provider:
        return await provider.generate(request, raw_path)


async def generate_video(raw_path: Path) -> AssetResult:
    """Route to PoYo by default; FAL only when ``USE_FAL`` is set."""
    if os.getenv("USE_FAL"):
        print("Using legacy FAL.ai provider (USE_FAL is set).")
        return await generate_video_fal(raw_path)
    print(f"Using primary PoYo.ai provider (model={POYO_VIDEO_MODEL}).")
    return await generate_video_poyo(raw_path)


def generate_pad(path: Path, duration: float) -> None:
    # Soft ambient pad: two low sines + very quiet filtered noise for texture.
    expr = (
        "0.15*sin(2*PI*110*t) + 0.10*sin(2*PI*164*t) + "
        "0.03*sin(2*PI*220*t)*sin(2*PI*0.25*t)"
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"aevalsrc='{expr}':s=48000:c=stereo",
            "-f", "lavfi",
            "-i", "anoisesrc=color=brown:amplitude=0.015:seed=7",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:normalize=0[pad];"
            "[pad]afade=t=in:ss=0:d=1.2,afade=t=out:st=" + str(max(0, duration - 1.5)) + ":d=1.5",
            "-t", str(duration),
            "-ar", "48000",
            "-ac", "2",
            str(path),
        ]
    )
    print(f"pad: {path} ({duration:.2f}s)")


def mix_final(video_path: Path, pad_path: Path, duration: float) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-i", str(pad_path),
            "-filter_complex",
            "[0:v]copy[vout];[1:a]loudnorm=I=-20:TP=-2:LRA=11[afinal]",
            "-map", "[vout]",
            "-map", "[afinal]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", str(duration),
            str(FINAL_PATH),
        ]
    )
    print(f"final: {FINAL_PATH} ({probe_duration(FINAL_PATH):.2f}s)")


async def main() -> int:
    ensure_dirs()

    raw_video = WORK_DIR / "raw_video.mp4"
    if os.getenv("REUSE_VIDEO") and raw_video.exists():
        print(f"video (reused): {raw_video}")
    else:
        result = await generate_video(raw_video)
        print(f"video: {result.local_path} ({result.duration_ms / 1000.0:.2f}s)")

    duration = probe_duration(raw_video)
    if duration <= 0:
        raise RuntimeError(f"Could not probe duration of {raw_video}")

    pad_path = WORK_DIR / "pad.wav"
    if os.getenv("REUSE_AUDIO") and pad_path.exists():
        print(f"pad (reused): {pad_path}")
    else:
        generate_pad(pad_path, duration)

    mix_final(raw_video, pad_path, duration)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
