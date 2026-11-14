"""Tests for the anti-slide film gate (QA skill).

Ported from ``huashu-art-motion``'s ``scripts/film_gate.py``
(``../tool-lab/trials/2026-11-11-art-animation-skills/``). The fixtures are
synthesised with FFmpeg and measured on real decoded pixels, so each test
exercises the decode, the transition exclusion and the threshold together
rather than a mock of any of them.

The fast-motion fixture is FFmpeg's ``life`` cellular automaton: every cell
flips on every generation, so the frame-to-frame difference sits far above the
threshold of 9. ``testsrc2`` moves too slowly to be usable here (0.021) and
``mandelbrot`` is a slow zoom (0.000), which the last test pins down.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from qa.film_gate import film_gate_checks, measure_film_gate

from qa import film_gate
from tests.conftest import _require_ffmpeg


def _ffmpeg(*args: str) -> None:
    _require_ffmpeg()
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
    )


def _encode(path: Path, source: str, *, duration: float = 3.0) -> Path:
    """Encode one lavfi source to H.264 at 25 fps.

    ``source`` carries its own ``size``/``rate``; the length is always cut with
    ``-t`` because the ``life`` and ``mandelbrot`` sources have no ``duration``
    option and would reject one.
    """
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        source,
        "-t",
        f"{duration}",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    )
    return path


@pytest.fixture(scope="module")
def still_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One flat colour, held: no edges, no motion, no cuts."""
    path = tmp_path_factory.mktemp("fg") / "still.mp4"
    return _encode(path, "color=c=0x445566:size=320x180:rate=25")


@pytest.fixture(scope="module")
def fast_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every pixel flips every frame -- unambiguously in-shot fast motion."""
    path = tmp_path_factory.mktemp("fg") / "fast.mp4"
    return _encode(
        path,
        "life=size=320x180:rate=25:mold=10:ratio=0.3:death_color=#202020:life_color=#f0f0f0",
    )


@pytest.fixture(scope="module")
def slideshow_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Three flat colour fields joined by hard cuts: a deck in disguise.

    Nine seconds rather than three: at 12 fps a three-second clip has so few
    frame pairs that the one or two the encoder smears across a cut are already
    a few percent, and the fixture would measure its own length instead of the
    gate.
    """
    path = tmp_path_factory.mktemp("fg") / "slideshow.mp4"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=0x333333:size=320x180:rate=25:duration=3",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x777777:size=320x180:rate=25:duration=3",
        "-f",
        "lavfi",
        "-i",
        "color=c=0xAAAAAA:size=320x180:rate=25:duration=3",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    )
    return path


@pytest.fixture(scope="module")
def empty_insert_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A moving test pattern with two seconds of blank grey in the middle."""
    path = tmp_path_factory.mktemp("fg") / "empty-insert.mp4"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=25:duration=4",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x808080:size=320x180:rate=25:duration=4",
        "-filter_complex",
        "[0:v][1:v]overlay=enable='between(t,1.0,3.0)'",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(path),
    )
    return path


def test_flat_still_fails_film_motion(still_video: Path) -> None:
    """A held still has no in-shot motion at all."""
    metrics = measure_film_gate(still_video)

    assert metrics.fast_motion_ratio == 0.0
    assert metrics.shots == 1
    checks = {check.name: check for check in film_gate_checks(still_video, strict=True)}
    assert checks["film_motion"].passed is False
    assert checks["film_motion"].value == 0.0


def test_fast_action_passes_film_motion(fast_video: Path) -> None:
    """Full-frame per-frame change clears the thin line, not just the red line."""
    metrics = measure_film_gate(fast_video)

    assert metrics.fast_motion_ratio >= film_gate._FAST_MOTION_THIN
    checks = {check.name: check for check in film_gate_checks(fast_video, strict=True)}
    assert checks["film_motion"].passed is True


def test_hard_cuts_are_excluded_not_counted_as_motion(slideshow_video: Path) -> None:
    """Three colour fields are three shots; the cuts are not in-shot motion.

    This is the check that keeps the gate honest: a deck full of cuts would
    otherwise score as a fast-moving film simply because every cut is a large
    frame difference.
    """
    metrics = measure_film_gate(slideshow_video)

    assert metrics.shots >= 3
    assert metrics.fast_motion_ratio < film_gate._FAST_MOTION_RED


def test_blank_insert_is_reported_with_its_start(empty_insert_video: Path) -> None:
    """Two seconds of edge-less grey is a hard finding, and it says where.

    The reported run is shorter than the insert: the frames on either side of
    the two transitions into and out of it are dropped, as in the source, so a
    clean 2.0 s insert reads about 1.75 s at 12 fps.
    """
    metrics = measure_film_gate(empty_insert_video)

    assert 1.5 <= metrics.empty_run_seconds <= 2.1
    assert metrics.empty_run_start is not None
    assert 0.8 <= metrics.empty_run_start <= 1.3
    checks = {check.name: check for check in film_gate_checks(empty_insert_video, strict=True)}
    assert checks["film_empty_frames"].passed is False
    assert "starting at" in checks["film_empty_frames"].message


def test_blue_purple_ground_fails_and_warm_ground_passes(tmp_path: Path) -> None:
    """The house palette rule: violet ground reads as generated slop."""
    cool = _encode(tmp_path / "cool.mp4", "color=c=0x4b2fbf", duration=2.0)
    warm = _encode(tmp_path / "warm.mp4", "color=c=0xd98a2b", duration=2.0)

    assert measure_film_gate(cool).blue_purple_share >= film_gate._BP_RED
    assert measure_film_gate(warm).blue_purple_share == 0.0
    checks = {check.name: check for check in film_gate_checks(cool, strict=True)}
    assert checks["film_blue_purple"].passed is False


def test_checks_are_advisory_unless_strict(still_video: Path) -> None:
    """Drama holds static frames on purpose; only opted-in callers fail on this."""
    for check in film_gate_checks(still_video, strict=False):
        assert check.passed is True
        assert check.threshold == "advisory"


def test_truncation_is_visible_never_silent(
    monkeypatch: pytest.MonkeyPatch, still_video: Path
) -> None:
    """A long file is read up to the cap and the message says so."""
    monkeypatch.setattr(film_gate, "_MAX_ANALYSIS_SECONDS", 1.0)

    metrics = measure_film_gate(still_video)

    assert metrics.truncated
    assert metrics.analysed_seconds < metrics.source_seconds
    checks = {check.name: check for check in film_gate_checks(still_video, strict=False)}
    assert "analysed the first" in checks["film_motion"].message


def test_gates_share_one_grey_decode(monkeypatch: pytest.MonkeyPatch, fast_video: Path) -> None:
    """Motion and empty-frame detection read the same frames; only colour re-decodes."""
    calls: list[str] = []
    original = film_gate._decode_frames

    def spy(path, *, fps, width, pix_fmt, seconds=None):  # type: ignore[no-untyped-def]
        calls.append(pix_fmt)
        return original(path, fps=fps, width=width, pix_fmt=pix_fmt, seconds=seconds)

    monkeypatch.setattr(film_gate, "_decode_frames", spy)
    measure_film_gate(fast_video)

    assert calls.count("gray") == 1
    assert calls.count("rgb24") == 1


def test_slow_zoom_is_not_fast_motion(tmp_path: Path) -> None:
    """Mandelbrot's continuous zoom reads as calm: motion must be a frame-diff, not a move."""
    path = _encode(tmp_path / "mandel.mp4", "mandelbrot", duration=2.0)

    assert measure_film_gate(path).fast_motion_ratio < film_gate._FAST_MOTION_RED
