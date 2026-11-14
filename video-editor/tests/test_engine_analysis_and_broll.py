"""Tests for the v1 analysis modules and FFmpeg b-roll helpers.

These modules are part of the legacy v1 raw-footage pipeline (still
used by `ve ingest`/`ve analyze`) but were previously uncovered. The
tests here guard the *unit* surface — input/output shape and error
behavior — without exercising the full v1 chain.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from video_editor_engine.analysis.filler_words import detect_filler_words
from video_editor_engine.analysis.silence import (
    SilenceDetectionError,
    _merge_regions,
    detect_silence,
    detect_transcript_pauses,
)
from video_editor_ffmpeg.broll import generate_placeholder, insert_broll

from tests.conftest import _require_ffmpeg

# ---------------------------------------------------------------------------
# Analysis: filler words
# ---------------------------------------------------------------------------


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


def test_detect_filler_words_finds_default_fillers(
    tmp_path: Path,
) -> None:
    """Default filler set catches 'um' and 'uh' but not 'so' (it's a word)."""
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("um", 0.5, 0.7),
            ("world", 0.8, 1.2),
            ("uh", 1.3, 1.5),
        ]
    )
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    output_path = tmp_path / "fillers.json"
    data = detect_filler_words(transcript_path, output_path)

    assert output_path.exists()
    assert data["count"] == 2
    match_words = {m["word"] for m in data["matches"]}
    assert "um" in match_words
    assert "uh" in match_words


def test_detect_filler_words_with_custom_set(
    tmp_path: Path,
) -> None:
    """Custom filler set overrides the default."""
    transcript = _build_transcript([("actually", 0.0, 0.5)])
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    data = detect_filler_words(
        transcript_path,
        tmp_path / "out.json",
        fillers=["actually"],
    )
    assert data["count"] == 1
    assert data["matches"][0]["word"] == "actually"


def test_detect_filler_words_handles_empty_transcript(
    tmp_path: Path,
) -> None:
    """Empty transcripts produce empty matches, not errors."""
    transcript = _build_transcript([])
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    data = detect_filler_words(transcript_path, tmp_path / "out.json")
    assert data["count"] == 0
    assert data["matches"] == []


def test_detect_filler_words_finds_multi_word_phrases(
    tmp_path: Path,
) -> None:
    """C36 regression: multi-word fillers like ``you know`` must be detected,
    not silently missed because the code only compared single tokens."""
    transcript = _build_transcript(
        [
            ("you", 0.0, 0.2),
            ("know", 0.2, 0.4),
            ("um", 0.5, 0.7),
            ("it", 0.8, 1.0),
        ]
    )
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    data = detect_filler_words(transcript_path, tmp_path / "out.json")
    match_words = [m["word"] for m in data["matches"]]
    assert match_words == ["you", "know", "um"]
    assert data["count"] == 3


# ---------------------------------------------------------------------------
# Analysis: silence / pauses
# ---------------------------------------------------------------------------


def test_merge_regions_merges_overlapping() -> None:
    """Overlapping regions collapse to a single span."""
    merged = _merge_regions([(0.0, 1.0), (0.5, 2.0), (3.0, 4.0)])
    assert merged == [(0.0, 2.0), (3.0, 4.0)]


def test_merge_regions_handles_empty() -> None:
    """Empty input returns empty list."""
    assert _merge_regions([]) == []


def test_detect_silence_finds_real_silent_regions(
    tmp_path: Path,
) -> None:
    """A 2s silent gap in a tone burst is detected by FFmpeg silencedetect."""
    _require_ffmpeg()
    # Build a 4s audio file: 1s tone, 2s silence, 1s tone.
    audio_path = tmp_path / "audio.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1[a0];"
            "aevalsrc='0.5*sin(2*PI*440*t)':d=1:c=mono[s1];"
            "[a0][s1]concat=n=2:v=0:a=1[out]",
            "-map",
            "[out]",
            "-t",
            "4",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    output_path = tmp_path / "silence.json"
    data = detect_silence(audio_path, output_path, silence_threshold=-30, min_duration=0.5)

    assert output_path.exists()
    assert isinstance(data["silences"], list)
    # We expect at least one large silence region from the 2s gap.
    durations = [s["duration"] for s in data["silences"]]
    assert any(d >= 1.0 for d in durations), f"expected a long silence, got {durations}"


def test_detect_transcript_pauses_finds_long_gaps(
    tmp_path: Path,
) -> None:
    """A 1.0s gap between two words is flagged as a pause."""
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("world", 1.4, 1.8),  # 1.0s gap after "Hello"
        ]
    )
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    data = detect_transcript_pauses(transcript_path, tmp_path / "pauses.json", pause_threshold=0.5)
    assert len(data["pauses"]) == 1
    pause = data["pauses"][0]
    assert pause["duration"] == pytest.approx(1.0)  # 1.4 - 0.4 with float error
    assert pause["after_word"] == "Hello"
    assert pause["before_word"] == "world"


def test_detect_transcript_pauses_ignores_short_gaps(
    tmp_path: Path,
) -> None:
    """Gaps below the threshold don't appear in the result."""
    transcript = _build_transcript(
        [
            ("Hello", 0.0, 0.4),
            ("world", 0.5, 0.9),  # 0.1s gap — under default 0.5s threshold
        ]
    )
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")

    data = detect_transcript_pauses(transcript_path, tmp_path / "out.json")
    assert data["pauses"] == []


def test_detect_silence_raises_on_ffmpeg_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug #58 regression: a non-zero ffmpeg return code must raise
    SilenceDetectionError rather than silently returning empty silences."""

    class FakeResult:
        returncode = 1
        stderr = "ffmpeg: No such file or directory"

    monkeypatch.setattr(
        "video_editor_engine.analysis.silence.run_ffmpeg",
        lambda *args, **kwargs: FakeResult(),
    )

    with pytest.raises(SilenceDetectionError, match="ffmpeg silencedetect failed"):
        detect_silence(tmp_path / "missing.wav", tmp_path / "out.json")


# ---------------------------------------------------------------------------
# FFmpeg b-roll helpers
# ---------------------------------------------------------------------------


def test_generate_placeholder_writes_playable_video(
    tmp_path: Path,
) -> None:
    """generate_placeholder produces a real, decodable video file."""
    _require_ffmpeg()
    out = tmp_path / "placeholder.mp4"
    result = generate_placeholder(out, duration=0.5, width=320, height=240, label="Test")

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0
    # ffprobe confirms it's a valid H.264 video.
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "codec_name=h264" in probe.stdout


def test_insert_broll_overlays_clip_on_base(
    tmp_path: Path,
) -> None:
    """insert_broll composites a clip on top of a base video and the result
    is playable. We can't easily verify the overlay pixels in a unit
    test, but we can confirm the FFmpeg pipeline runs cleanly and the
    output duration matches the base.

    The base needs an audio track — ``insert_broll`` always does
    ``-map 0:a`` because it preserves the speech from the source.
    """
    _require_ffmpeg()
    base = tmp_path / "base.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(base),
        ],
        check=True,
        capture_output=True,
    )

    overlay = tmp_path / "overlay.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=0.5",
            "-pix_fmt",
            "yuv420p",
            str(overlay),
        ],
        check=True,
        capture_output=True,
    )

    out = tmp_path / "with_broll.mp4"
    insert_broll(base, [(overlay, 0.5, 0.5)], out)

    assert out.exists()
    assert out.stat().st_size > 0
    # Output duration should match the base (2s).
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # format line is "duration=2.000000"
    assert "duration=2" in probe.stdout


def _mean_frame(path: Path, t: float) -> float:
    """Return the mean luma (0-255) of a single frame at time ``t``."""
    import subprocess

    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    data = result.stdout
    if not data:
        return 0.0
    return sum(data) / len(data)


def test_insert_broll_overlay_plays_during_enable_window(
    tmp_path: Path,
) -> None:
    """C31 regression: the overlay clip must play during its enable window,
    not show a frozen last frame. We compare two frames inside the window:
    with ``setpts`` they differ; without it they would be identical.
    """
    _require_ffmpeg()
    base = tmp_path / "base.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(base),
        ],
        check=True,
        capture_output=True,
    )

    overlay = tmp_path / "overlay.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1.0:size=320x240:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(overlay),
        ],
        check=True,
        capture_output=True,
    )

    out = tmp_path / "with_broll.mp4"
    insert_broll(base, [(overlay, 0.5, 1.0)], out)
    assert out.exists()

    first = _mean_frame(out, 0.6)
    second = _mean_frame(out, 0.8)
    # A playing testsrc overlay produces different mean luma at different
    # times; a frozen last frame would be identical.
    assert abs(second - first) > 1.0, f"overlay appears frozen: mean at 0.6s={first}, 0.8s={second}"


def test_generate_placeholder_handles_special_characters(
    tmp_path: Path,
) -> None:
    """C33 regression: labels with apostrophes, percent signs, and braces
    must not break the placeholder render."""
    _require_ffmpeg()
    out = tmp_path / "placeholder.mp4"
    result = generate_placeholder(
        out,
        duration=0.5,
        width=320,
        height=240,
        label="it's 100% {ok}",
    )

    assert result == out
    assert out.exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "codec_name=h264" in probe.stdout
