"""Shared FFmpeg region parser tests (#113 / A62)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.regions import parse_ffmpeg_regions


class TestParseFfmpegRegions:
    """Unit tests for the shared start/end timestamp parser."""

    def test_pairs_matching_start_end(self) -> None:
        stderr = (
            "silence_start: 1.200\nsilence_end: 2.100\nsilence_start: 3.000\nsilence_end: 3.500\n"
        )
        regions = parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:")
        assert regions == [(1.2, 2.1), (3.0, 3.5)]

    def test_trailing_start_without_end(self) -> None:
        """A trailing unpaired start is reported with a minimal duration."""
        stderr = "silence_start: 1.200\nsilence_end: 2.100\nsilence_start: 4.500\n"
        regions = parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:")
        assert regions == [(1.2, 2.1), (4.5, 4.6)]

    def test_leading_end_without_start_is_ignored(self) -> None:
        """An unmatched end before any start is consumed by positional zip and
        does not pair with the next start.
        """
        stderr = "silence_end: 1.000\nsilence_start: 2.000\nsilence_end: 3.000\n"
        regions = parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:")
        assert regions == []

    def test_blackdetect_same_line_start_and_end(self) -> None:
        """blackdetect can emit both keys on one stderr line."""
        stderr = "black_start: 0.500 black_end: 1.200\n"
        regions = parse_ffmpeg_regions(stderr, "black_start:", "black_end:")
        assert regions == [(0.5, 1.2)]

    def test_skips_zero_and_negative_duration_pairs(self) -> None:
        stderr = (
            "silence_start: 5.000\nsilence_end: 5.000\nsilence_start: 7.000\nsilence_end: 6.000\n"
        )
        regions = parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:")
        assert regions == []

    def test_custom_trailing_duration(self) -> None:
        stderr = "silence_start: 10.000\n"
        regions = parse_ffmpeg_regions(
            stderr, "silence_start:", "silence_end:", trailing_duration=0.5
        )
        assert regions == [(10.0, 10.5)]


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available on PATH")
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not available on PATH")


def _build_silent_video(path: Path, duration: float = 1.5) -> Path:
    """Synthesize a video whose audio track is digital silence."""
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
            "anullsrc=r=44100:cl=mono",
            "-t",
            str(duration),
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


def test_qa_re_export_matches_shared_parser() -> None:
    """qa._parse_ffmpeg_regions is a thin wrapper around the shared helper."""
    from qa.qa import _parse_ffmpeg_regions

    stderr = "silence_start: 0.100\nsilence_end: 0.200\n"
    assert _parse_ffmpeg_regions(stderr, "silence_start:", "silence_end:") == [(0.1, 0.2)]


def test_rough_cut_silence_regions_detects_trailing_silence(tmp_path: Path) -> None:
    """Bug #113: the legacy rough-cut parser must handle unpaired events the
    same way the QA parser does.
    """
    _require_ffmpeg()
    video = _build_silent_video(tmp_path / "silent.mp4", duration=1.5)

    from rough_cut.rough_cut import _silence_regions

    # Extract audio so _silence_regions has a single audio stream to probe.
    audio_path = tmp_path / "audio.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "1",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    regions = _silence_regions(audio_path)
    # A fully-silent 1.5s audio file should be reported as one silent region.
    assert regions
    start, end = regions[0]
    assert start < 0.5
    assert end - start >= 0.5
