"""Tests for the FFmpeg wrapper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.captions import (
    _escape_ffmpeg_text,
    burn_subtitles,
    drawtext_captions,
    generate_caption_segments,
    remap_transcript_to_cuts,
    write_srt,
)
from video_editor_ffmpeg.cuts import apply_cuts, concat_segments, cut_segment
from video_editor_ffmpeg.probe import probe_media
from video_editor_ffmpeg.run import CommandError, FFmpegError, run_ffmpeg, run_subprocess


def test_probe_media(tmp_media: Path) -> None:
    info = probe_media(tmp_media)
    assert info.duration is not None
    assert info.duration >= 2.9
    assert info.width == 320
    assert info.height == 240
    assert info.fps == 30.0
    assert info.has_audio


def test_apply_cuts(tmp_media: Path, tmp_path: Path) -> None:
    output = tmp_path / "cut.mp4"
    apply_cuts(tmp_media, [(0.0, 1.5), (2.0, 2.9)], output)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration > 0


def test_generate_caption_segments() -> None:
    transcript = {
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4},
            {"word": "world", "start": 0.5, "end": 0.9},
            {"word": "test", "start": 2.0, "end": 2.4},
        ]
    }
    segments = generate_caption_segments(transcript)
    assert len(segments) == 2
    assert segments[0]["text"] == "Hello world"
    assert segments[1]["text"] == "test"


def test_write_srt(tmp_path: Path) -> None:
    segments = [
        {"text": "Hello", "start": 0.0, "end": 1.0},
        {"text": "World", "start": 2.0, "end": 3.0},
    ]
    path = write_srt(segments, tmp_path / "test.srt")
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Hello" in text
    assert "00:00:01,000" in text


def test_remap_transcript_to_cuts() -> None:
    transcript = {
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4},
            {"word": "um", "start": 0.5, "end": 0.7},
            {"word": "world", "start": 0.8, "end": 1.2},
            {"word": "pause", "start": 2.0, "end": 2.5},
            {"word": "Again", "start": 3.0, "end": 3.4},
        ]
    }
    kept = [(0.0, 0.4), (0.8, 1.2), (3.0, 3.4)]
    remapped = remap_transcript_to_cuts(transcript, kept)
    words = remapped["words"]
    assert len(words) == 3
    assert words[0]["word"] == "Hello"
    assert words[0]["start"] == 0.0
    assert words[1]["word"] == "world"
    # Second kept segment starts after 0.4s of removed filler.
    assert words[1]["start"] == pytest.approx(0.4, abs=0.01)
    assert words[2]["word"] == "Again"
    # Third kept segment starts after 0.4 + 0.4s of kept segments before it.
    assert words[2]["start"] == pytest.approx(0.8, abs=0.01)


class TestRunFFmpeg:
    """Bug #63 / #64 regression: timeout and stderr capture."""

    def test_run_ffmpeg_includes_stderr_on_failure(self) -> None:
        with pytest.raises(FFmpegError, match="Unknown encoder"):
            run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=2x2:d=0.1",
                    "-c:v",
                    "not_a_codec",
                    "-f",
                    "null",
                    "-",
                ]
            )

    def test_run_ffmpeg_raises_ffmpeg_error_with_command(self) -> None:
        with pytest.raises(FFmpegError) as exc_info:
            run_ffmpeg(["ffmpeg", "-invalid_flag"])
        assert "ffmpeg" in str(exc_info.value)

    def test_run_ffmpeg_times_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Bug #63 regression: a long-running ffmpeg call must not wedge forever."""

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=0.1)

        monkeypatch.setattr("video_editor_ffmpeg.run.subprocess.run", fake_run)
        with pytest.raises(FFmpegError, match="timed out"):
            run_ffmpeg(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], timeout=0.1)

    def test_run_ffmpeg_success_returns_completed_process(self) -> None:
        result = run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=2x2:d=0.1",
                "-f",
                "null",
                "-",
            ]
        )
        assert isinstance(result, subprocess.CompletedProcess)


def test_remap_transcript_keeps_words_bleeding_past_cut() -> None:
    """Regression: words that start inside a kept segment and end past the
    next cut boundary should NOT be dropped — they should be clipped to the
    segment edge so the audible speech is still labeled."""
    transcript = {
        "words": [
            # "Hello" is fully inside the first kept segment.
            {"word": "Hello", "start": 0.0, "end": 0.4},
            # "world" starts inside the first kept segment and bleeds 0.2s
            # past the cut. Previously dropped entirely.
            {"word": "world", "start": 0.4, "end": 0.8},
            # A word fully inside the second kept segment.
            {"word": "again", "start": 1.5, "end": 1.9},
        ]
    }
    kept = [(0.0, 0.6), (1.0, 2.0)]
    remapped = remap_transcript_to_cuts(transcript, kept)
    words = remapped["words"]
    texts = [w["word"] for w in words]
    # "world" should be present (clipped to 0.6, the segment boundary),
    # not silently dropped.
    assert "world" in texts
    clipped = next(w for w in words if w["word"] == "world")
    # The word was clipped to the first segment's end (0.6s).
    assert clipped["end"] == pytest.approx(0.6, abs=1e-3)
    # And it lives in the cut timeline at the same offset as Hello (no shift).
    assert clipped["start"] == pytest.approx(0.4, abs=1e-3)


def test_escape_ffmpeg_text_escapes_drawtext_special_chars() -> None:
    """Bug #91 regression: drawtext special chars must be escaped."""
    assert _escape_ffmpeg_text("Time: 12:30") == "Time\\: 12\\:30"
    assert _escape_ffmpeg_text("100% done") == "100\\% done"
    assert _escape_ffmpeg_text("a, b, c") == "a\\, b\\, c"
    assert _escape_ffmpeg_text("{x}") == "\\{x\\}"
    assert _escape_ffmpeg_text("it's") == "it\\'s"
    assert _escape_ffmpeg_text("C:\\path") == r"C\:\\path"


def test_drawtext_captions_with_special_chars(tmp_media: Path, tmp_path: Path) -> None:
    """Bug #91 regression: captions with : % , {} ' do not crash ffmpeg."""
    output = tmp_path / "captioned.mp4"
    segments = [
        {"text": "Time: 12:30, 100% done {ok}", "start": 0.0, "end": 1.0},
    ]
    result = drawtext_captions(tmp_media, output, segments)
    assert result.exists()
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration > 0


def test_cut_segment_non_keyframe_start_re_encodes(tmp_media: Path, tmp_path: Path) -> None:
    """Bug #92 regression: cutting at a non-keyframe boundary with -c copy
    produces frozen/gray frames. The cut should re-encode so the output is
    valid from the requested start time."""
    output = tmp_path / "cut.mp4"
    cut_segment(tmp_media, output, start=0.15, end=1.5)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration == pytest.approx(1.35, abs=0.2)


def test_cut_segment_copy_flag_uses_stream_copy(tmp_media: Path, tmp_path: Path) -> None:
    """The fast stream-copy path is still available for keyframe-aware callers."""
    output = tmp_path / "cut_copy.mp4"
    cut_segment(tmp_media, output, start=0.0, end=1.5, copy=True)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration == pytest.approx(1.5, abs=0.2)


def test_concat_segments_escapes_paths_with_apostrophe(tmp_media: Path, tmp_path: Path) -> None:
    """Bug #93 regression: paths containing a single quote must not break the
    concat demuxer file list."""
    segment_dir = tmp_path / "sean's_clips"
    segment_dir.mkdir()
    seg1 = segment_dir / "clip1.mp4"
    seg2 = segment_dir / "clip2.mp4"
    cut_segment(tmp_media, seg1, start=0.0, end=1.0)
    cut_segment(tmp_media, seg2, start=1.0, end=2.0)

    output = tmp_path / "joined.mp4"
    concat_segments([seg1, seg2], output)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration == pytest.approx(2.0, abs=0.2)


def test_burn_subtitles_srt_path_with_special_chars(tmp_media: Path, tmp_path: Path) -> None:
    """Bug #93 regression: an SRT path containing ':' or \"'\" should not
    break the subtitles filter."""
    srt_dir = tmp_path / "sub:titles"
    srt_dir.mkdir()
    srt_path = srt_dir / "sean's.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    output = tmp_path / "subbed.mp4"
    burn_subtitles(tmp_media, srt_path, output)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration > 0


def test_media_info_duration_none_when_missing(tmp_path: Path) -> None:
    """Bug #94 regression: missing duration must be reported as None, not 0.0."""
    from video_editor_ffmpeg.probe import MediaInfo

    info = MediaInfo(tmp_path / "nope.mp4", {"format": {}, "streams": []})
    assert info.duration is None


def test_run_subprocess_raises_command_error_with_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C11 regression: generic subprocess failures must raise an actionable
    ``CommandError`` with stderr, not a bare ``CalledProcessError``."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["pnpm", "run", "build"],
            stderr="missing script: build",
        )

    monkeypatch.setattr("video_editor_ffmpeg.run.subprocess.run", fake_run)
    with pytest.raises(CommandError, match="missing script: build") as exc_info:
        run_subprocess(["pnpm", "run", "build"])
    assert exc_info.value.cmd == ["pnpm", "run", "build"]


def test_run_subprocess_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """C11 regression: generic subprocess calls must respect a timeout."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["pnpm"], timeout=0.1)

    monkeypatch.setattr("video_editor_ffmpeg.run.subprocess.run", fake_run)
    with pytest.raises(CommandError, match="timed out"):
        run_subprocess(["pnpm", "run", "build"], timeout=0.1)


def test_burn_subtitles_temp_srt_not_in_output_dir(tmp_media: Path, tmp_path: Path) -> None:
    """C35 regression: ``burn_subtitles`` must not leave its temporary SRT
    copy in the output directory; it should live in the system temp dir."""
    srt_path = tmp_path / "captions.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    output = output_dir / "subbed.mp4"

    burn_subtitles(tmp_media, srt_path, output)

    assert output.exists()
    # The temp SRT should not be in the output directory.
    assert not any(p.name.endswith(".srt") for p in output_dir.iterdir())


def test_concat_segments_re_encodes_by_default(tmp_media: Path, tmp_path: Path) -> None:
    """Regression: ``concat_segments`` now routes through ``run_ffmpeg`` so
    skill-level concats get timeout/stderr handling."""
    seg1 = tmp_path / "seg1.mp4"
    seg2 = tmp_path / "seg2.mp4"
    cut_segment(tmp_media, seg1, start=0.0, end=1.0, copy=True)
    cut_segment(tmp_media, seg2, start=1.0, end=2.0, copy=True)

    output = tmp_path / "joined.mp4"
    concat_segments([seg1, seg2], output)
    info = probe_media(output)
    assert info.duration is not None
    assert info.duration == pytest.approx(2.0, abs=0.2)
