"""Tests for the SFX placement skill."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sfx_placement.mapper import (
    SfxMoment,
    extract_moments_from_segments,
    extract_moments_from_words,
)
from sfx_placement.sfx_placement import SfxPlacementSkill
from video_editor_state.job import JobContext


def _make_video(tmp_path: Path, duration: float = 3.0) -> Path:
    """Create a short test video with audio."""
    path = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s=320x240:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(job_id="sfx-test", work_root=str(tmp_path), output_root=str(tmp_path))


def test_extract_moments_from_words() -> None:
    """Keyword mapping should surface SFX moments from word-level data."""
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.3},
        {"word": "click", "start": 1.0, "end": 1.2},
        {"word": "here", "start": 1.2, "end": 1.5},
    ]
    moments = extract_moments_from_words(words)
    assert len(moments) == 1
    assert moments[0].keyword == "click"
    assert moments[0].category == "ui"
    assert moments[0].start == pytest.approx(1.0)


def test_extract_moments_from_segments() -> None:
    """Keyword mapping should surface SFX moments from segment text."""
    segments = [
        {"text": "Then the crowd cheers loudly", "start": 2.0, "end": 4.0},
    ]
    moments = extract_moments_from_segments(segments)
    assert len(moments) == 1
    assert moments[0].keyword == "crowd"
    assert moments[0].category == "atmosphere"


def test_moment_to_dict() -> None:
    """SfxMoment should serialize cleanly."""
    moment = SfxMoment(
        start=1.0, end=1.5, text="click", keyword="click", category="ui", prompt="ui click"
    )
    d = moment.to_dict()
    assert d["keyword"] == "click"
    assert d["start"] == 1.0
    assert "prompt" in d


def test_skill_name(ctx: JobContext) -> None:
    """The skill should report the correct protocol name."""
    skill = SfxPlacementSkill(ctx)
    assert skill.name == "sfx_placement"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_run_places_sfx(ctx: JobContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end SFX placement with mocked provider."""
    video = _make_video(tmp_path, duration=3.0)
    transcript = {
        "words": [
            {"word": "tap", "start": 0.5, "end": 0.7},
            {"word": "whoosh", "start": 1.5, "end": 1.8},
        ]
    }

    skill = SfxPlacementSkill(ctx, sfx_duration_seconds=1.0, gain_db=-10.0)

    # Patch the provider's generate to write a tiny silent MP3.
    async def fake_generate(request: Any, output_path: Path) -> object:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "1.0",
                "-acodec",
                "libmp3lame",
                "-q:a",
                "4",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        from video_editor_asset_ingestion.models import AssetResult, AssetType

        return AssetResult(
            asset_id=request.asset_id,
            asset_type=AssetType.SFX,
            scene_id="",
            local_path=str(output_path),
            duration_ms=1000,
        )

    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    with patch(
        "sfx_placement.sfx_placement.FreesoundSFXProvider.generate",
        new=AsyncMock(side_effect=fake_generate),
    ):
        result = asyncio.run(skill.run(video, transcript, output_name="with_sfx.mp4"))

    assert Path(result["output"]).exists()
    assert Path(result["plan"]).exists()
    assert result["count"] == 2
    plan = json.loads(Path(result["plan"]).read_text())
    assert plan["gain_db"] == -10.0
    assert len(plan["sfx"]) == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_run_no_moments_copies_video(ctx: JobContext, tmp_path: Path) -> None:
    """With no SFX moments the output should still exist as a copy."""
    video = _make_video(tmp_path, duration=2.0)
    transcript = {"words": [{"word": "hello", "start": 0.0, "end": 0.5}]}

    skill = SfxPlacementSkill(ctx)

    with patch("sfx_placement.sfx_placement.ElevenLabsSFXProvider.generate", new=AsyncMock()):
        result = asyncio.run(skill.run(video, transcript))

    assert Path(result["output"]).exists()
    assert result["count"] == 0


def test_run_missing_input(ctx: JobContext, tmp_path: Path) -> None:
    """A missing input file should raise FileNotFoundError."""
    skill = SfxPlacementSkill(ctx)
    with pytest.raises(FileNotFoundError):
        asyncio.run(skill.run(tmp_path / "missing.mp4", {"words": []}))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_a_manager_routes_generation_through_the_job_budget(
    ctx: JobContext, tmp_path: Path
) -> None:
    """With a manager the request carries the job budget and provider chain."""
    from unittest.mock import AsyncMock

    from video_editor_asset_ingestion.models import AssetResult, AssetType, BatchResult

    video = _make_video(tmp_path, duration=3.0)
    cached = tmp_path / "cached.mp3"
    cached.write_bytes(b"mp3")
    manager = AsyncMock()
    manager.resolve_partial = AsyncMock(
        return_value=BatchResult(
            results=[
                AssetResult(
                    asset_id="sfx_crowd_1000",
                    asset_type=AssetType.SFX,
                    local_path=str(cached),
                )
            ]
        )
    )
    skill = SfxPlacementSkill(ctx, manager=manager, sfx_duration_seconds=1.0)

    with patch("sfx_placement.sfx_placement.mix_sfx_into_video") as mix:
        result = asyncio.run(
            skill.run(
                video,
                {"segments": [{"text": "the crowd cheers", "start": 1.0, "end": 2.0}]},
            )
        )

    request = manager.resolve_partial.call_args.args[0][0]
    assert request.asset_type is AssetType.SFX
    assert request.provider == "freesound_sfx"
    assert request.output_ext == "mp3"
    assert request.params["duration_seconds"] == 1.0
    assert (skill.output_dir / "crowd_1000.mp3").read_bytes() == b"mp3"
    mix.assert_called_once()
    assert result["count"] == 1
