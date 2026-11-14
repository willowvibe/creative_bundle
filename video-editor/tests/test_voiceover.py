"""Tests for the external-voiceover muxing helper (Issue A).

``ve audio`` muxes a separate voiceover file onto a source video's audio
track so the result can be fed into ``ve run`` for any
narration-over-existing-video workflow — the engine's ``mix_background_music``
assumes the speech is already in the source's audio track.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_ffmpeg.voiceover import mux_voiceover


def _make_video(path: Path, duration: float, freq: float = 100.0) -> Path:
    """Create a synthetic video whose audio is a distinct tone (so we can
    tell it apart from the voiceover by frequency)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=320x240:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _band_mean_db(path: Path, freq: float, width: float = 120.0) -> float:
    """Mean volume in dB of a bandpass slice — used to detect which tone
    is present in the output audio."""
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
    import re

    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", result.stderr)
    if not match:
        return -100.0  # silence / not detectable
    return float(match.group(1))


def test_mux_voiceover_replaces_source_audio(tmp_path: Path) -> None:
    """The output must carry the voiceover's tone, not the source's tone."""
    # Source video: audio is a 100 Hz tone (silent-ish placeholder).
    source = _make_video(tmp_path / "source.mp4", duration=2.0, freq=100.0)
    # Voiceover: a 1000 Hz tone (clearly distinguishable).
    voiceover = tmp_path / "voiceover.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2.0",
            "-c:a",
            "libmp3lame",
            str(voiceover),
        ],
        check=True,
        capture_output=True,
    )

    output = mux_voiceover(
        source_video=source,
        voiceover_path=voiceover,
        output_path=tmp_path / "out" / "with_vo.mp4",
    )
    assert output.exists()

    # The 1000 Hz voiceover tone should be present in the output...
    vo_band = _band_mean_db(output, freq=1000.0)
    # ...and the source's 100 Hz tone should be gone (replaced, not mixed).
    src_band = _band_mean_db(output, freq=100.0)
    assert vo_band > -40.0, f"voiceover tone missing in output ({vo_band:.1f} dB)"
    assert src_band < vo_band - 15.0, (
        f"source audio leaked into output (src_band={src_band:.1f} dB, "
        f"vo_band={vo_band:.1f} dB) — mux_voiceover should replace, not sum"
    )


def test_mux_voiceover_preserves_video_dimensions(tmp_path: Path) -> None:
    """The visual track is passed through; only the audio changes."""
    source = _make_video(tmp_path / "source.mp4", duration=2.0, freq=100.0)
    voiceover = tmp_path / "voiceover.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2.0",
            "-c:a",
            "libmp3lame",
            str(voiceover),
        ],
        check=True,
        capture_output=True,
    )

    output = mux_voiceover(
        source_video=source,
        voiceover_path=voiceover,
        output_path=tmp_path / "out.mp4",
    )
    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(output)
    assert info.has_audio
    assert (info.width, info.height) == (320, 240)


def test_mux_voiceover_errors_when_voiceover_missing(tmp_path: Path) -> None:
    """A missing voiceover file surfaces a clear error, not an FFmpeg traceback."""
    source = _make_video(tmp_path / "source.mp4", duration=2.0, freq=100.0)
    with pytest.raises((FileNotFoundError, RuntimeError, subprocess.CalledProcessError)):
        mux_voiceover(
            source_video=source,
            voiceover_path=tmp_path / "no_such.mp3",
            output_path=tmp_path / "out.mp4",
        )


def test_mux_voiceover_does_not_truncate_when_voiceover_shorter(
    tmp_path: Path,
) -> None:
    """Bug #99: default behaviour preserves the full video even when VO is shorter."""
    source = _make_video(tmp_path / "source.mp4", duration=5.0, freq=100.0)
    voiceover = tmp_path / "voiceover.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2.0",
            "-c:a",
            "libmp3lame",
            str(voiceover),
        ],
        check=True,
        capture_output=True,
    )

    output = mux_voiceover(
        source_video=source,
        voiceover_path=voiceover,
        output_path=tmp_path / "out.mp4",
    )

    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(output)
    assert info.duration is not None
    assert info.duration >= 4.5, (
        f"video was truncated to {info.duration:.1f}s; VO was shorter than source"
    )
    assert (info.width, info.height) == (320, 240)

    # The voiceover tone is present in the first part of the output.
    vo_band = _band_mean_db(output, freq=1000.0)
    assert vo_band > -40.0


def test_mux_voiceover_shortest_truncates_when_requested(
    tmp_path: Path,
) -> None:
    """The legacy ``-shortest`` path is still available via ``shortest=True``."""
    source = _make_video(tmp_path / "source.mp4", duration=5.0, freq=100.0)
    voiceover = tmp_path / "voiceover.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2.0",
            "-c:a",
            "libmp3lame",
            str(voiceover),
        ],
        check=True,
        capture_output=True,
    )

    output = mux_voiceover(
        source_video=source,
        voiceover_path=voiceover,
        output_path=tmp_path / "out_short.mp4",
        shortest=True,
    )

    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(output)
    assert info.duration is not None
    assert 1.5 <= info.duration <= 2.5


def test_mux_voiceover_copy_opt_in(tmp_path: Path) -> None:
    """C34 regression: ``copy=True`` still works for keyframe-aligned callers;
    the default is a safe re-encode."""
    source = _make_video(tmp_path / "source.mp4", duration=2.0, freq=100.0)
    voiceover = tmp_path / "voiceover.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=2.0",
            "-c:a",
            "libmp3lame",
            str(voiceover),
        ],
        check=True,
        capture_output=True,
    )

    output = mux_voiceover(
        source_video=source,
        voiceover_path=voiceover,
        output_path=tmp_path / "out_copy.mp4",
        copy=True,
    )

    from video_editor_ffmpeg.probe import probe_media

    info = probe_media(output)
    assert info.duration is not None
    assert info.duration >= 1.5
