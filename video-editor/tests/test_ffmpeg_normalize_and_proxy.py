"""Tests for FFmpeg normalize and proxy helpers (#105 / A54)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.normalize import normalize_audio
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.proxy import make_preview, make_proxy

from tests.conftest import _require_ffmpeg


def _make_video(path: Path, duration: float = 1.0) -> Path:
    """Synthesize a small H.264/AAC video."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(autouse=True)
def _require_ffmpeg_fixture() -> None:
    _require_ffmpeg()


def test_normalize_audio_re_encodes_to_aac(tmp_path: Path) -> None:
    """normalize_audio applies loudnorm and writes a valid AAC audio track."""
    video = _make_video(tmp_path / "source.mp4")
    output = tmp_path / "normalized.mp4"

    result = normalize_audio(video, output)

    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0
    info = probe_media(output)
    assert info.has_audio


def test_make_proxy_scales_video(tmp_path: Path) -> None:
    """make_proxy produces a smaller, valid H.264 video."""
    video = _make_video(tmp_path / "source.mp4")
    output = tmp_path / "proxy.mp4"

    result = make_proxy(video, output, width=160)

    assert result == output
    assert output.exists()
    info = probe_media(output)
    assert info.width == 160


def test_make_preview_uses_default_width(tmp_path: Path) -> None:
    """make_preview defaults to a 960px-wide proxy."""
    video = _make_video(tmp_path / "source.mp4")
    output = tmp_path / "preview.mp4"

    result = make_preview(video, output)

    assert result == output
    info = probe_media(output)
    assert info.width == 960
