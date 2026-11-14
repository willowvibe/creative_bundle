"""Tests for the ``mix_sfx_into_video`` FFmpeg helper."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from video_editor_ffmpeg.broll import mix_sfx_into_video
from video_editor_ffmpeg.probe import probe_media


def _make_silent_video(path: Path, duration: float = 2.0) -> Path:
    """Create a 2s 320x240 silent test video via lavfi."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_sfx_clip(path: Path, duration: float = 0.5) -> Path:
    """Create a short SFX-like MP3 (sine wave with fade)."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-af",
            f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(0, duration - 0.1)}:d=0.1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_mix_sfx_empty_timeline_copies_through(tmp_path: Path) -> None:
    """An empty timeline copies the base video's audio through unchanged."""
    base = _make_silent_video(tmp_path / "base.mp4", duration=1.5)
    output = tmp_path / "out.mp4"
    mix_sfx_into_video(
        base_video=base,
        sfx_timeline=[],
        output_path=output,
    )
    assert output.exists()
    info = probe_media(output)
    assert info.duration is not None
    # Base video was 1.5s; output should be the same.
    assert abs(info.duration - 1.5) < 0.2


def test_mix_sfx_single_clip_at_t_zero(tmp_path: Path) -> None:
    """A single SFX at t=0 mixes into the base video and the output is longer."""
    base = _make_silent_video(tmp_path / "base.mp4", duration=1.5)
    sfx = _make_sfx_clip(tmp_path / "sfx.mp3", duration=0.4)
    output = tmp_path / "out.mp4"
    mix_sfx_into_video(
        base_video=base,
        sfx_timeline=[(sfx, 0.0)],
        output_path=output,
        sfx_gain_db=-6.0,
    )
    assert output.exists()
    info = probe_media(output)
    assert info.duration is not None
    # Base is 1.5s; the SFX is 0.4s and starts at t=0. Output
    # duration should still be ~1.5s (mix doesn't extend past base).
    assert abs(info.duration - 1.5) < 0.2


def _mean_volume_db(path: Path) -> float:
    """Return FFmpeg ``volumedetect`` mean_volume in dB."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume: ([-\d.]+) dB", result.stderr)
    if not match:
        raise RuntimeError("volumedetect did not report mean_volume")
    return float(match.group(1))


def _make_tone_video(path: Path, duration: float = 2.0) -> Path:
    """Create a test video with a 440 Hz sine audio track."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_mix_sfx_normalize_zero_preserves_voiceover_level(tmp_path: Path) -> None:
    """C32 regression: ``amix`` must use ``normalize=0`` so the base
    voiceover is not attenuated by 1/(N+1) whenever an SFX clip is active.

    The SFX is mixed at -60 dB so it contributes almost no energy. With the
    buggy default ``normalize=1`` the base tone would drop ~6 dB; with the
    fix the output's mean volume matches the base within 1 dB.
    """
    base = _make_tone_video(tmp_path / "base.mp4", duration=2.0)
    sfx = _make_sfx_clip(tmp_path / "sfx.mp3", duration=0.5)
    output = tmp_path / "out.mp4"

    base_db = _mean_volume_db(base)
    mix_sfx_into_video(
        base_video=base,
        sfx_timeline=[(sfx, 0.5)],
        output_path=output,
        sfx_gain_db=-60.0,
    )
    assert output.exists()
    out_db = _mean_volume_db(output)

    assert abs(out_db - base_db) < 1.0, (
        f"voiceover level changed by {out_db - base_db:.1f} dB with normalize=0"
    )
