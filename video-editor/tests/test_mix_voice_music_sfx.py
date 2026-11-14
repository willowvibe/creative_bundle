"""Tests for the voice + ducked music + SFX mix."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from music.mix_sfx import MixError, mix_voice_music_sfx
from video_editor_ffmpeg.probe import probe_media

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")


def _make_video(path: Path, duration: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _make_tone(
    path: Path,
    *,
    frequency: int,
    duration: float,
    volume: float = 1.0,
) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={duration}",
            "-af",
            f"volume={volume}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(path),
        ],
        check=True,
    )
    return path


def _window_mean_db(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
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
    match = re.search(r"mean_volume: (-?[0-9.]+) dB", result.stderr)
    assert match is not None, result.stderr[-500:]
    return float(match.group(1))


def test_the_sfx_lands_on_its_cue_over_the_ducked_bed(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "video.mp4")
    # Quiet voice and music leave headroom for the SFX, so the window means
    # measure the cue rather than clipping.
    voice = _make_tone(tmp_path / "voice.mp3", frequency=440, duration=2.0, volume=0.2)
    music = _make_tone(tmp_path / "music.mp3", frequency=220, duration=1.0, volume=0.2)
    sfx = _make_tone(tmp_path / "sfx.mp3", frequency=2000, duration=0.2, volume=1.0)
    output = tmp_path / "mixed.mp4"

    mix_voice_music_sfx(
        video,
        voice,
        music,
        [(sfx, 1.0)],
        output,
        sfx_gain_db=-6.0,
    )

    assert output.is_file()
    duration = probe_media(output).duration
    assert duration is not None and abs(duration - 2.0) < 0.15
    bed = _window_mean_db(output, 0.4, 0.2)
    cue = _window_mean_db(output, 1.0, 0.2)
    assert cue > bed + 3.0, f"cue {cue:.1f} dB vs bed {bed:.1f} dB"


def test_an_empty_timeline_still_mixes_voice_and_music(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "video.mp4")
    voice = _make_tone(tmp_path / "voice.mp3", frequency=440, duration=2.0)
    music = _make_tone(tmp_path / "music.mp3", frequency=220, duration=1.0)
    output = tmp_path / "mixed.mp4"

    mix_voice_music_sfx(video, voice, music, [], output)

    assert output.is_file()
    duration = probe_media(output).duration
    assert duration is not None and abs(duration - 2.0) < 0.15
    assert _window_mean_db(output, 0.5, 0.2) > -60.0


def test_a_missing_input_names_itself(tmp_path: Path) -> None:
    video = _make_video(tmp_path / "video.mp4")
    voice = _make_tone(tmp_path / "voice.mp3", frequency=440, duration=1.0)
    music = _make_tone(tmp_path / "music.mp3", frequency=220, duration=1.0)
    with pytest.raises(MixError, match="voice file not found"):
        mix_voice_music_sfx(video, tmp_path / "missing.mp3", music, [], tmp_path / "out.mp4")
    with pytest.raises(MixError, match="SFX clip not found"):
        mix_voice_music_sfx(
            video,
            voice,
            music,
            [(tmp_path / "missing.mp3", 0.5)],
            tmp_path / "out.mp4",
        )
