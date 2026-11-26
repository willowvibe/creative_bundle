"""Tests for joining chunk videos and mixing audio cues with ffmpeg."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core.chunk_cache import count_video_frames
from video_editor_core.chunk_media import concat_videos, mix_audio_cues, mux_audio
from video_editor_core.composition import AudioCue

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _clip(path: Path, frames: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30",
         "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _tone(path: Path, seconds: float) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         str(path)],
        check=True,
    )
    return path


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def _max_volume(path: Path, start: float, length: float) -> float:
    err = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", str(start), "-t", str(length), "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    match = re.search(r"max_volume: (-?[\d.]+|-inf) dB", err)
    assert match, err
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


def test_concat_joins_frames_exactly(tmp_path: Path) -> None:
    out = concat_videos([_clip(tmp_path / "a.mp4", 45), _clip(tmp_path / "b.mp4", 30)], tmp_path / "j.mp4")
    assert count_video_frames(out) == 75


def test_mix_places_cues_and_pads_to_duration(tmp_path: Path) -> None:
    a = AudioCue(id="a", source=str(_tone(tmp_path / "a.wav", 1.0)), start=0.0, duration=1.0)
    b = AudioCue(id="b", source=str(_tone(tmp_path / "b.wav", 1.0)), start=1.5, duration=1.0)
    out = mix_audio_cues([a, b], 3.0, tmp_path / "mix.wav")
    assert _duration(out) == pytest.approx(3.0, abs=0.01)
    assert _max_volume(out, 1.1, 0.3) < -60
    assert _max_volume(out, 1.6, 0.3) > -30  # lavfi sine peaks at -18 dBFS


def test_mix_upmixes_mono_without_attenuation(tmp_path: Path) -> None:
    source = _tone(tmp_path / "mono.wav", 1.0)
    out = mix_audio_cues(
        [AudioCue(id="a", source=str(source), start=0.0, duration=1.0)], 1.0, tmp_path / "mix.wav"
    )
    assert _max_volume(out, 0.0, 1.0) == pytest.approx(_max_volume(source, 0.0, 1.0), abs=0.5)


def test_mix_places_the_bundled_coin_cue_at_its_time_and_gain(tmp_path: Path) -> None:
    from video_editor_core.adapters.from_scene_plan import from_scene_plan

    plan = {
        "job_id": "coin_mix",
        "aspect_ratio": "16:9",
        "scenes": [
            {"id": f"s{i}", "type": "narration", "duration_seconds": 3.0,
             "props": {"tally": {"label": "cash", "value": value}}}
            for i, value in enumerate([0, 500])
        ],
    }
    (cue,) = [c for c in from_scene_plan(plan).audio if c.id.endswith("_tally_coin")]
    out = mix_audio_cues([cue], 6.0, tmp_path / "coin.wav")
    assert _max_volume(out, 0.0, 3.0) < -60  # silent before the count
    # coin_in peaks at -3 dBFS, placed -8 dB down: about -11 dBFS
    assert _max_volume(out, cue.start, 0.5) == pytest.approx(-11.0, abs=1.5)


def test_mux_keeps_video_frames_and_adds_audio(tmp_path: Path) -> None:
    video = _clip(tmp_path / "v.mp4", 60)
    audio = mix_audio_cues(
        [AudioCue(id="a", source=str(_tone(tmp_path / "t.wav", 2.0)), start=0.0)], 2.0, tmp_path / "m.wav"
    )
    out = mux_audio(video, audio, tmp_path / "out.mp4")
    assert count_video_frames(out) == 60
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(streams) == ["audio", "video"]
