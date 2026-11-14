"""Tests for the audio cleanup skill."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from audio_cleanup.audio_cleanup import AudioCleanupSkill
from audio_cleanup.models import AudioCleanupConfig
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_noisy_video(tmp_path: Path) -> Path:
    """Create a short MP4 with a sine-wave audio track."""
    _require_ffmpeg()
    video = tmp_path / "noisy.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2.0",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=2.0",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    return video


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(job_id="ac-test", work_root=str(tmp_path), output_root=str(tmp_path))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_audio_cleanup_audio_output(ctx: JobContext, tmp_path: Path) -> None:
    """Cleaning an audio-only input should produce a cleaned WAV."""
    _require_ffmpeg()
    audio = tmp_path / "input.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )

    skill = AudioCleanupSkill(ctx)
    result = skill.run(audio, output_name="cleaned.wav")

    assert result.exists()
    assert result.suffix == ".wav"
    assert (ctx.work_dir / "audio_cleanup" / "audio_cleanup.json").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_audio_cleanup_video_output(ctx: JobContext, tmp_path: Path) -> None:
    """Cleaning a video input with return_video=True should produce a cleaned MP4."""
    _require_ffmpeg()
    video = _make_noisy_video(tmp_path)

    skill = AudioCleanupSkill(ctx)
    result = skill.run(video, output_name="cleaned.wav", return_video=True)

    assert result.exists()
    assert result.suffix == ".mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")
def test_audio_cleanup_manifest_stages(ctx: JobContext, tmp_path: Path) -> None:
    """The manifest should record which stages were applied."""
    _require_ffmpeg()
    audio = tmp_path / "input.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.0",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )

    skill = AudioCleanupSkill(
        ctx,
        config=AudioCleanupConfig(isolate_voice=False, denoise=False, normalize=True),
    )
    result = skill.run(audio)

    manifest_path = ctx.work_dir / "audio_cleanup" / "audio_cleanup.json"
    import json

    data = json.loads(manifest_path.read_text())
    assert data["stages_applied"] == ["normalize"]
    assert data["cleaned_audio"] == str(result)


def test_audio_cleanup_missing_input(ctx: JobContext, tmp_path: Path) -> None:
    """A missing input file should raise FileNotFoundError."""
    skill = AudioCleanupSkill(ctx)
    with pytest.raises(FileNotFoundError):
        skill.run(tmp_path / "missing.wav")
