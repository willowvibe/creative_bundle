"""#272: loudness normalisation must pass the QA gate it feeds.

The shipped short's narration is hot at the peak (-0.35 dBTP) and low in
integrated loudness (-16.31 LUFS), which is exactly the shape single-pass
``loudnorm`` handles worst: it runs its dynamic mode and landed at -15.1 LUFS
after targeting -14, so a build that *was* normalised still failed QA.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_pipelines.explainer_pipeline import (
    LOUDNESS_TARGET_LUFS,
    measure_loudness,
    normalize_loudness,
)
from video_editor_pipelines.explainer_qa import (
    LOUDNESS_TOLERANCE_LU,
    TRUE_PEAK_CEILING_DB,
)

from tests.conftest import _require_ffmpeg


def _make_video(path: Path, audio_expression: str, duration: float = 6.0) -> Path:
    """A 320x240 H.264/AAC clip whose audio is ``audio_expression``."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration}:size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{audio_expression}':d={duration}:s=44100",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
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


def test_normalize_loudness_meets_the_qa_gate_with_hot_peaks(tmp_path: Path) -> None:
    """A quiet bed plus a near-0 dBFS click must still land at -14 / <= -1.

    The old single pass measured -23.0 LUFS on this signal: the limiter
    cannot raise the bed without the click setting the gain, so the
    dynamic-mode pass simply refuses to apply the gain.
    """
    _require_ffmpeg()
    source = _make_video(
        tmp_path / "hot.mp4",
        "0.04*sin(2*PI*220*t)+if(lt(abs(t-3),0.03),0.9*sin(2*PI*900*t),0)",
    )
    output = tmp_path / "normalized.mp4"

    normalize_loudness(source, output)

    assert output.exists()
    loudness, true_peak = measure_loudness(output)
    assert abs(loudness - LOUDNESS_TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU, loudness
    assert true_peak <= TRUE_PEAK_CEILING_DB, true_peak


def test_normalize_loudness_leaves_a_settled_source_settled(tmp_path: Path) -> None:
    """A source already near the target is nudged, not over-driven."""
    _require_ffmpeg()
    source = _make_video(tmp_path / "settled.mp4", "0.25*sin(2*PI*220*t)")
    output = tmp_path / "normalized.mp4"

    normalize_loudness(source, output)

    loudness, true_peak = measure_loudness(output)
    assert abs(loudness - LOUDNESS_TARGET_LUFS) <= LOUDNESS_TOLERANCE_LU, loudness
    assert true_peak <= TRUE_PEAK_CEILING_DB, true_peak


def test_a_peak_overshoot_lowers_the_limiter_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The January build decoded 1.2 dB over its pre-encode ceiling.

    Correcting only loudness kept adding gain until the peak failed the gate;
    the ceiling must follow the *measured* overshoot too.
    """
    from video_editor_pipelines import explainer_pipeline as pipeline

    ceilings: list[float] = []
    measurements = [
        (-16.25, -0.18),  # input
        (-14.80, -0.80),  # first encode: loudness short, peak over
        (-14.40, -1.30),  # corrected: inside both targets
    ]

    def fake_pass(video: Path, output: Path, gain_db: float, ceiling_db: float) -> None:
        ceilings.append(ceiling_db)
        Path(output).write_bytes(b"encoded")

    monkeypatch.setattr(pipeline, "_normalize_pass", fake_pass)
    monkeypatch.setattr(pipeline, "measure_loudness", lambda path: measurements.pop(0))

    output = tmp_path / "final.mp4"
    pipeline.normalize_loudness(tmp_path / "voiced.mp4", output)

    assert output.exists()
    assert ceilings[0] == pipeline.LOUDNESS_CEILING_DB
    assert ceilings[1] < ceilings[0]
