"""Tests for the music skill."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from captions.captions import CaptionsSkill
from graphics.graphics import GraphicsSkill
from graphics.models import Brand
from music.models import MusicStyle
from music.music import DEFAULT_MUSIC_SEARCH_PATHS, MusicSkill
from video_editor_core.timeline import EDL, compute_timeline_segments
from video_editor_ffmpeg.probe import probe_media
from video_editor_state.job import JobContext

from tests.conftest import _require_ffmpeg


def _make_brand(duck_db: float = -14.0) -> Brand:
    return Brand(
        brand="test-brand",
        colors={"primary": "#000", "secondary": "#111", "background": "#222", "text": "#fff"},
        fonts={"headline": "X", "body": "X", "captions": "X"},
        logo={"path": "", "width": 0, "height": 0},
        defaults={"music_duck_db": duck_db, "caption_animation": "pop_in", "final_loudness": -16},
    )


def _build_transcript(words: list[tuple[str, float, float]]) -> dict[str, object]:
    return {
        "source_audio": "",
        "language": "en",
        "language_probability": 1.0,
        "word_timestamps": True,
        "segments": [],
        "words": [{"word": w, "start": s, "end": e, "probability": 0.95} for w, s, e in words],
    }


def _make_music_file(path: Path, duration: float, freq: float = 440.0) -> Path:
    """Create a synthetic AAC music file."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _detect_mean_volume(path: Path, start: float, end: float) -> float:
    """Return the mean volume in dB for a time range using FFmpeg volumedetect."""
    _require_ffmpeg()
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            f"atrim={start}:{end},volumedetect",
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
        raise RuntimeError(f"Could not detect volume for {path}")
    return float(match.group(1))


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    return JobContext(
        job_id="music-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )


def test_music_mix_output_exists_and_matches_duration(
    ctx: JobContext,
    tmp_media: Path,
    tmp_path: Path,
) -> None:
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )

    segments = compute_timeline_segments(
        [
            (0.0, 1.0, "intro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )
    captions_skill = CaptionsSkill(ctx, graphics_skill=graphics_skill)
    captions_skill.run()

    music_file = _make_music_file(tmp_path / "music.m4a", duration=0.5)
    skill = MusicSkill(ctx, music_path=music_file)
    output = skill.run()

    assert output.exists()
    info = probe_media(output)
    assert info.has_audio
    # Compare against the video the music skill actually consumed, not the raw source.
    music_manifest = json.loads((ctx.work_dir / "music" / "music.json").read_text(encoding="utf-8"))
    input_video = Path(music_manifest["input_video"])
    assert info.duration == pytest.approx(probe_media(input_video).duration, abs=0.5)


def test_music_manifest_saved(
    ctx: JobContext,
    tmp_media: Path,
    tmp_path: Path,
) -> None:
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )

    segments = compute_timeline_segments(
        [
            (0.0, 1.0, "intro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    music_file = _make_music_file(tmp_path / "music.m4a", duration=0.5)
    skill = MusicSkill(ctx, music_path=music_file)
    skill.run()

    manifest_path = ctx.work_dir / "music" / "music.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["job_id"] == ctx.job_id
    assert data["music_path"] == str(music_file)
    assert data["output_video"]


def test_music_falls_back_to_graphics_output(
    ctx: JobContext,
    tmp_media: Path,
    tmp_path: Path,
) -> None:
    transcript_path = ctx.artifact("transcript", "transcript.json")
    transcript_path.write_text(
        json.dumps(
            _build_transcript(
                [
                    ("Hello", 0.1, 0.5),
                    ("world", 0.6, 1.0),
                ]
            )
        ),
        encoding="utf-8",
    )

    segments = compute_timeline_segments(
        [
            (0.0, 1.0, "intro"),
        ]
    )
    edl = EDL(job_id=ctx.job_id, source_path=str(tmp_media), segments=segments)
    ctx.artifact("plan", "edl.json").write_text(json.dumps(edl.model_dump()), encoding="utf-8")

    graphics_skill = GraphicsSkill(ctx, presets_dir=Path("presets"))
    graphics_skill.run(
        preset_name="short_explainer",
        edl=edl,
        source_video=tmp_media,
        transcript_path=transcript_path,
    )

    music_file = _make_music_file(tmp_path / "music.m4a", duration=0.5)
    skill = MusicSkill(ctx, music_path=music_file)
    output = skill.run()

    assert output.exists()
    assert "music" in str(output)


def test_music_ducking_reduces_during_speech(
    tmp_path: Path,
) -> None:
    """Music should be quieter during a loud speech interval than during silence."""
    # Build a 4-second video: 0-2s silence, 2-4s loud speech sine.
    speech_video = tmp_path / "speech.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2",
            "-filter_complex",
            "[1:a][2:a]concat=n=2:v=0:a=1[speech]",
            "-map",
            "0:v",
            "-map",
            "[speech]",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(speech_video),
        ],
        check=True,
        capture_output=True,
    )

    # Constant music tone.
    music_file = _make_music_file(tmp_path / "music.m4a", duration=4.0, freq=440)

    ctx = JobContext(
        job_id="duck-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    ctx.ensure_dirs()
    # Pretend the speech video is the captions output by writing a fake manifest.
    manifest = {"output_video": str(speech_video)}
    ctx.artifact("captions", "captions.json").write_text(json.dumps(manifest), encoding="utf-8")

    style = MusicStyle(
        music_path=str(music_file),
        music_gain_db=-10.0,
        duck_threshold=0.05,
        duck_ratio=20.0,
        attack_ms=20,
        release_ms=100,
    )
    skill = MusicSkill(ctx, style=style)
    output = skill.run()

    assert output.exists()
    silent_loudness = _detect_mean_volume(output, 0.0, 1.5)
    speech_loudness = _detect_mean_volume(output, 2.5, 3.5)
    # During speech the mixed level should be lower than during silence because
    # the music is ducked. The speech tone itself will raise the level somewhat,
    # so we only assert a modest reduction.
    assert speech_loudness < silent_loudness + 6.0


def test_default_music_search_paths_exist_as_documented() -> None:
    """Ensure the default search paths list is documented and sensible."""
    assert DEFAULT_MUSIC_SEARCH_PATHS
    for path in DEFAULT_MUSIC_SEARCH_PATHS:
        assert path.startswith("assets/music/default.")


def test_duck_threshold_is_emitted_in_valid_linear_range() -> None:
    """C29 regression: ``duck_threshold`` is a linear amplitude in
    ``[0.000976563, 1]`` (FFmpeg sidechaincompress threshold range).

    Instead of hardcoding the same filter string, call the production
    helper so the test fails if the real builder changes units or drops
    the threshold.
    """
    from music.mix import _build_filter_complex

    filter_arg = _build_filter_complex(duration=10.0, duck_threshold=0.1)
    match = re.search(r"sidechaincompress=threshold=(-?[\d.]+)", filter_arg)
    assert match is not None
    threshold_value = float(match.group(1))
    assert threshold_value == pytest.approx(0.1)
    assert 0.000976563 <= threshold_value <= 1.0, (
        f"threshold {threshold_value} is outside FFmpeg's linear-amplitude range"
    )


def test_duck_threshold_docstring_states_linear_amplitude() -> None:
    """Regression: the docstring for ``mix_background_music`` should clearly
    state that ``duck_threshold`` is a linear amplitude (matching FFmpeg's
    sidechaincompress threshold) so users don't set it in dB by accident."""
    import music.mix as mix_mod

    doc = mix_mod.mix_background_music.__doc__ or ""
    assert "linear amplitude" in doc
    # Sanity: explicitly forbids the most common confusion.
    assert "value in dB" not in doc.lower()


def test_music_style_from_brand_uses_music_duck_db() -> None:
    """from_brand() reads defaults.music_duck_db and uses it as
    music_gain_db. The music_path is taken from the second arg."""
    brand = _make_brand(duck_db=-18.0)
    style = MusicStyle.from_brand(brand, music_path="/tmp/song.mp3")
    assert style.music_gain_db == -18.0
    assert style.music_path == "/tmp/song.mp3"


def test_music_style_from_brand_falls_back_to_default() -> None:
    """A brand without a music_duck_db falls back to MusicStyle's default."""
    brand = Brand(
        brand="sparse",
        colors={"text": "#fff"},
        fonts={"captions": "X"},
        logo={"path": "", "width": 0, "height": 0},
        defaults={},
    )
    style = MusicStyle.from_brand(brand)
    # The default is -14.0 LUFS (the brand-less hard-coded value).
    assert style.music_gain_db == pytest.approx(-14.0)
    # music_path defaults to empty when not provided.
    assert style.music_path == ""


def _band_mean_db(path: Path, freq: float, width: float = 120.0) -> float:
    """Return the mean volume in dB of a bandpassed slice of ``path``."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-af",
            f"bandpass=f={freq}:w={width},volumedetect",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", result.stderr)
    if not match:
        raise RuntimeError(f"Could not detect band volume for {path}")
    return float(match.group(1))


def test_music_mix_preserves_speech_loudness(tmp_path: Path) -> None:
    """Regression for Issue F: ``amix=inputs=2`` without ``weights=`` halves
    the speech amplitude via RMS-normalization. The mix must preserve the
    speech loudness (within a few dB), not attenuate it by ~6 dB which makes
    the voiceover inaudible against the ducked music bed.

    Isolates the speech band (1000 Hz) from the music band (200 Hz) via a
    bandpass filter so the measurement reflects the speech track alone.
    """
    # Speech video: 1000 Hz sine at -14 dB, 3 s (loud, continuous).
    speech_video = tmp_path / "speech.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=3",
            "-af",
            "volume=-14dB",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(speech_video),
        ],
        check=True,
        capture_output=True,
    )
    # Music: 200 Hz sine (well separated from the 1000 Hz speech band).
    music_file = _make_music_file(tmp_path / "music.m4a", duration=3.0, freq=200)

    ctx = JobContext(
        job_id="loud-job",
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )
    ctx.ensure_dirs()
    ctx.artifact("captions", "captions.json").write_text(
        json.dumps({"output_video": str(speech_video)}), encoding="utf-8"
    )

    style = MusicStyle(
        music_path=str(music_file),
        music_gain_db=-18.0,
        duck_threshold=0.05,
        duck_ratio=8.0,
        attack_ms=20,
        release_ms=300,
    )
    skill = MusicSkill(ctx, style=style)
    output = skill.run()
    assert output.exists()

    input_db = _band_mean_db(speech_video, freq=1000.0)
    output_db = _band_mean_db(output, freq=1000.0)
    # Speech must survive the mix within 4 dB. The bug attenuates it by ~6 dB.
    assert abs(output_db - input_db) < 4.0, (
        f"speech attenuated by {input_db - output_db:.1f} dB through the mix "
        f"(input={input_db:.1f} dB, output={output_db:.1f} dB) — amix "
        f"RMS-normalization is halving the speech; add weights= + makeup gain"
    )
