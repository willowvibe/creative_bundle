"""Tests for the standalone FFmpeg musical-video composer script.

These tests exercise the pure filtergraph-builder functions
(``_build_xfade_chain`` and ``_build_filtergraph``) so they need no ffmpeg
binary and run fast.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import _require_ffmpeg

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compose_musical_video.py"
spec = importlib.util.spec_from_file_location("compose_musical_video", _SCRIPT)
assert spec is not None and spec.loader is not None
compose_musical_video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose_musical_video)


def _xfade_transitions(filtergraph: str) -> list[tuple[float, float]]:
    """Extract ``(offset, duration)`` for each xfade in a filtergraph."""
    matches = re.findall(
        r"xfade=transition=\w+:duration=([0-9.]+):offset=([0-9.]+)",
        filtergraph,
    )
    return [(float(d), float(o)) for (d, o) in matches]


def _build_filtergraph_for_durations(
    source_durations: list[float],
    segment_duration: float,
    transition_duration: float,
) -> str:
    """Build only the xfade chain from a given source-durations list."""
    return str(
        compose_musical_video._build_xfade_chain(
            source_durations=source_durations,
            segment_duration=segment_duration,
            transition_duration=transition_duration,
            fps=30,
        )
    )


# ---------------------------------------------------------------------------
# Bug #193 — xfade offset math breaks when an intermediate source_dur is
# clamped by the music duration.
# ---------------------------------------------------------------------------


def test_xfade_chain_non_clamped_offsets_match_segment_duration() -> None:
    """Regression guard: when no segment is clamped, the xfade offset for
    transition ``i`` is exactly ``segment_duration * (i + 1)`` and the
    transition duration is the full ``transition_duration`` (the original,
    pre-#193 behaviour must be preserved)."""
    seg, tr = 8.0, 1.5
    # Three full non-clamped segments: each non-last has seg+tr.
    source_durations = [seg + tr, seg + tr, seg]
    fg = _build_filtergraph_for_durations(source_durations, seg, tr)
    transitions = _xfade_transitions(fg)
    assert len(transitions) == 2
    # xfade 0: offset = seg, duration = tr
    assert transitions[0] == pytest.approx((tr, seg))
    # xfade 1: offset = 2*seg, duration = tr
    assert transitions[1] == pytest.approx((tr, seg * 2))


def test_xfade_chain_clamps_transition_when_intermediate_source_clamped() -> None:
    """Bug #193: music in ``(N*seg, N*seg+tr)`` clamps an intermediate
    segment's ``source_dur`` below ``seg+tr``.

    With seg=8, tr=1.5, music=17.0 (in (16, 17.5)):
    ``_build_filtergraph`` produces ``source_durations = [9.5, 9.0, 1.0]`` —
    segment 1 is clamped (9.0 < 9.5) and segment 2 (the last) is only 1.0 long.

    The hard-coded ``offset = segment_duration * (i+1)`` would set xfade 1 to
    ``offset=16, duration=1.5``, but the previous output ``out1`` is only
    ``8 + 9.0 = 17.0`` long — ``16 + 1.5 = 17.5`` overshoots it, so FFmpeg
    hard-cuts / freezes / errors.

    The fix tracks the actual previous output duration and clamps the
    transition to the available material on both sides, so xfade 1 becomes
    ``duration=1.0`` (the last segment's length), ``offset=16.0``.
    """
    seg, tr = 8.0, 1.5
    music = 17.0
    fg, total, n = compose_musical_video._build_filtergraph(
        media_paths=[Path("a.png"), Path("b.png"), Path("c.png")],
        segment_duration=seg,
        transition_duration=tr,
        music_duration=music,
        width=1920,
        height=1080,
        fps=30,
    )
    # Source durations the filtergraph was built from (mirrored here to
    # document the clamp): seg0=9.5, seg1 clamped to 9.0, seg2=1.0.
    assert n == 3
    transitions = _xfade_transitions(fg)
    assert len(transitions) == 2
    # xfade 0 unchanged: offset=8, duration=1.5
    assert transitions[0] == pytest.approx((1.5, 8.0))
    # xfade 1: the last segment is only 1.0 long, so the transition must
    # clamp to 1.0 (not 1.5) and the offset is prev_dur(17.0) - 1.0 = 16.0.
    assert transitions[1][0] == pytest.approx(1.0)  # duration clamped to 1.0
    assert transitions[1][1] == pytest.approx(16.0)  # offset = prev_dur - trans


# ---------------------------------------------------------------------------
# Bug #225 — transition_duration <= 0 must be rejected up front.
# ---------------------------------------------------------------------------


def test_build_filtergraph_rejects_non_positive_transition_duration() -> None:
    for bad in (0.0, -1.5):
        with pytest.raises(ValueError, match="transition_duration must be positive"):
            compose_musical_video._build_filtergraph(
                media_paths=[Path("a.png")],
                segment_duration=8.0,
                transition_duration=bad,
                music_duration=16.0,
                width=1920,
                height=1080,
                fps=30,
            )


# ---------------------------------------------------------------------------
# Bug #205 / #206 — image branch pre-scales+crop and uses (iw-iw/z) for pan.
# ---------------------------------------------------------------------------


def test_image_segment_filter_prescales_and_crops_before_zoompan() -> None:
    """#205: a 4:3 image into 16:9 must be scale+crop'd to target before
    zoompan so it is not stretched. The image branch must contain
    ``scale=...:force_original_aspect_ratio=increase,crop=...`` before
    ``zoompan``."""
    fg = compose_musical_video._build_segment_filter(
        input_label="[0:v]",
        path=Path("img.png"),
        start=0.0,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30,
        motion_index=0,
    )
    # scale before crop before zoompan, in that order.
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in fg
    scale_pos = fg.index("scale=1920:1080:force_original_aspect_ratio=increase")
    crop_pos = fg.index("crop=1920:1080")
    zoom_pos = fg.index("zoompan=")
    assert scale_pos < crop_pos < zoom_pos


def test_image_segment_filter_pan_uses_zoom_aware_room() -> None:
    """#206/#227: the pan must be zoom-aware (offset is zero at the start
    scale and grows as the zoom grows) without referencing the runtime
    ``z`` variable, which FFmpeg 6.1+ rejects as an undefined constant.

    The new expression is a linear drift over ``on`` frames that mirrors the
    original ``(iw-iw/z)`` shape: at ``on=0`` the offset factor is zero, so
    the frame is centered; at the last frame the offset factor equals the
    available zoom room.
    """
    fg = compose_musical_video._build_segment_filter(
        input_label="[0:v]",
        path=Path("img.png"),
        start=0.0,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30,
        motion_index=0,
    )
    # The old runtime ``z`` reference is gone — FFmpeg 6.1+ rejects it.
    assert "(iw-iw/z)" not in fg
    # The pan is driven linearly by the frame counter ``on/frames``.
    assert "on/240" in fg
    # The zoom amplitude is still present and pan-aware.
    assert "zoompan=" in fg


# ---------------------------------------------------------------------------
# Bug #224 — animated GIFs are routed through the video branch.
# ---------------------------------------------------------------------------


def test_animated_gif_routed_through_video_branch() -> None:
    """#224: a .gif must NOT take the zoompan (still-image) branch; it must
    take the video branch (trim/scale/crop/tpad) so its frames are decoded."""
    gif_fg = compose_musical_video._build_segment_filter(
        input_label="[0:v]",
        path=Path("clip.gif"),
        start=0.0,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30,
        motion_index=0,
    )
    assert "zoompan=" not in gif_fg
    assert "tpad=stop_mode=clone" in gif_fg  # video branch marker
    # gif is in VIDEO_EXTS, not IMAGE_EXTS.
    assert "gif" in compose_musical_video.VIDEO_EXTS
    assert "gif" not in compose_musical_video.IMAGE_EXTS


# ---------------------------------------------------------------------------
# Bug #223 — the broad ``stem == "output"`` filter was removed.
# ---------------------------------------------------------------------------


def test_discover_media_no_longer_drops_output_named_source(tmp_path: Path) -> None:
    """#223: a user source named ``output.jpg`` must be discovered, not
    silently skipped by the old ``stem == "output"`` filter."""
    (tmp_path / "output.jpg").write_bytes(b"fake")  # is_file() is true on disk
    # Use a real tiny image so iterdir picks it up; the filter is by name only.
    # _discover_media filters by extension and stem, not by content.
    found = compose_musical_video._discover_media(tmp_path)
    assert any(p.name == "output.jpg" for p in found)


# ---------------------------------------------------------------------------
# Bug #222 — _resolve no longer carries dead try/except OSError.
# ---------------------------------------------------------------------------


def test_resolve_returns_resolved_path_for_nonexistent() -> None:
    """#222: Path.resolve(strict=False) does not raise for non-existent
    paths; _resolve must simply return the resolved path."""
    p = Path("/this/does/not/exist.jpg")
    assert compose_musical_video._resolve(p) == p.resolve()


# ---------------------------------------------------------------------------
# Bug #207 — _probe_duration surfaces a clear error on non-numeric ffprobe
# output (e.g. "N/A").
# ---------------------------------------------------------------------------


def test_probe_duration_raises_clear_error_on_na(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(_path: Path) -> float:
        raise ValueError("N/A")

    monkeypatch.setattr(compose_musical_video, "probe_duration", fake_probe)
    with pytest.raises(RuntimeError, match="non-numeric duration"):
        compose_musical_video._probe_duration(Path("broken.mp4"))


def test_probe_duration_returns_zero_for_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_editor_ffmpeg.probe import MediaInfoError

    def fake_probe(_path: Path) -> float:
        raise MediaInfoError("duration missing")

    monkeypatch.setattr(compose_musical_video, "probe_duration", fake_probe)
    assert compose_musical_video._probe_duration(Path("empty.mp4")) == 0.0


# ---------------------------------------------------------------------------
# Bug #208 — _run_ffmpeg surfaces stderr in the raised RuntimeError.
# ---------------------------------------------------------------------------


def test_run_ffmpeg_raises_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "Invalid data found when processing input\n"

    monkeypatch.setattr(
        compose_musical_video.subprocess,
        "run",
        lambda *a, **k: _Completed(),
    )
    with pytest.raises(RuntimeError, match="Invalid data found"):
        compose_musical_video._run_ffmpeg(["ffmpeg", "-version"])


def test_run_ffmpeg_no_raise_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        compose_musical_video.subprocess,
        "run",
        lambda *a, **k: _Completed(),
    )
    # Must not raise.
    compose_musical_video._run_ffmpeg(["ffmpeg", "-version"])


# ---------------------------------------------------------------------------
# Bug #204 / #221 — compose() creates the output parent and validates inputs.
# ---------------------------------------------------------------------------


def test_compose_raises_clear_error_for_missing_music(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "a.png").write_bytes(b"x")
    with pytest.raises(FileNotFoundError, match="Music file not found"):
        compose_musical_video.compose(
            music_path=tmp_path / "missing.mp3",
            media_dir=media_dir,
            output_path=tmp_path / "out" / "final.mp4",
        )


def test_compose_raises_clear_error_for_missing_media_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    music = tmp_path / "music.mp3"
    music.write_bytes(b"x")
    with pytest.raises(NotADirectoryError, match="Media directory not found"):
        compose_musical_video.compose(
            music_path=music,
            media_dir=tmp_path / "no_such_dir",
            output_path=tmp_path / "out" / "final.mp4",
        )


def test_compose_creates_output_parent_and_runs_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#204: compose() must mkdir the output parent before invoking ffmpeg."""
    music = tmp_path / "music.mp3"
    music.write_bytes(b"x")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "a.png").write_bytes(b"x")
    (media_dir / "b.png").write_bytes(b"x")

    # Stub ffprobe (duration) and ffmpeg (render) so no real binaries run.
    class _ProbeResult:
        stdout = "16.0\n"
        stderr = ""

    class _FfmpegResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if cmd[0] == "ffprobe":
            return _ProbeResult()
        return _FfmpegResult()

    monkeypatch.setattr(compose_musical_video.subprocess, "run", fake_run)
    monkeypatch.setattr(compose_musical_video, "probe_duration", lambda _path: 16.0)

    out = tmp_path / "deeply" / "nested" / "missing" / "final.mp4"
    compose_musical_video.compose(
        music_path=music,
        media_dir=media_dir,
        output_path=out,
        segment_seconds=8.0,
        transition_seconds=1.5,
    )
    assert out.parent.is_dir()  # #204: parent was created


# ---------------------------------------------------------------------------
# Bug #220 — main() validates --width/--height/--fps at parse time.
# ---------------------------------------------------------------------------


def test_main_rejects_non_positive_dimensions(capsys: pytest.CaptureFixture[str]) -> None:
    for bad in ("--width", "--height", "--fps"):
        with pytest.raises(SystemExit):
            compose_musical_video.main(
                [
                    "--music",
                    "x.mp3",
                    "--media-dir",
                    "y",
                    "--output",
                    "z.mp4",
                    bad,
                    "0",
                ]
            )
    # Positive values pass validation (compose will fail later on missing
    # files, but argparse/parser.error must not fire).
    with pytest.raises(FileNotFoundError):
        compose_musical_video.main(
            [
                "--music",
                "x.mp3",
                "--media-dir",
                "y",
                "--output",
                "z.mp4",
                "--width",
                "1280",
                "--height",
                "720",
                "--fps",
                "24",
            ]
        )


# ---------------------------------------------------------------------------
# Bug #194 — short video clips are not padded to the segment duration.
# ---------------------------------------------------------------------------


def test_build_segment_filter_pads_short_video_with_tpad() -> None:
    """Bug #194: the video branch slows clips to 50% via
    ``trim=end={duration*0.5},setpts=2*PTS``. If the source is shorter than
    ``duration*0.5`` the segment comes up short and there is no
    freeze-frame pad, so the xfade chain's ``trim=0:{source_durations[i]}``
    has nothing to trim into → hard cut / freeze / error.

    The fix adds ``tpad=stop_mode=clone:stop_duration={duration}`` so the
    segment is always at least ``duration`` long (the xfade trim then cuts
    it to exactly ``source_dur``). This matches the documented template
    caveat "shorter videos freeze on last frame".
    """
    seg = compose_musical_video._build_segment_filter(
        input_label="[0:v]",
        path=Path("clip.mp4"),
        start=0.0,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30,
        motion_index=0,
    )
    # Slow-mo trim + 2x setpts preserved.
    assert "trim=start=0:end=4.0" in seg  # duration * 0.5
    assert "setpts=2*(PTS-STARTPTS)" in seg
    # Freeze-pad to at least the segment duration so the xfade trim has
    # enough material regardless of source length.
    assert "tpad=stop_mode=clone:stop_duration=8.0" in seg


def test_build_segment_filter_image_branch_has_no_tpad() -> None:
    """Images render via ``zoompan`` which emits exactly ``duration*fps``
    frames, so they are already exactly ``duration`` long and must not be
    freeze-padded (padding would duplicate the last zoompan frame)."""
    seg = compose_musical_video._build_segment_filter(
        input_label="[0:v]",
        path=Path("img.png"),
        start=0.0,
        duration=8.0,
        width=1920,
        height=1080,
        fps=30,
        motion_index=0,
    )
    assert "tpad" not in seg
    assert "zoompan" in seg


# ---------------------------------------------------------------------------
# Bug #227 follow-up — real-image end-to-end composer test.
# ---------------------------------------------------------------------------


def test_compose_end_to_end_with_real_png_and_music(
    tmp_path: Path,
) -> None:
    """Regression guard: a real PNG must render through the full FFmpeg chain.

    Bug #227 only surfaced with real images because the zoompan x=/y=
    workaround depends on actual input dimensions. Synthetic byte files pass
    validation but fail inside ffmpeg, so this test synthesizes a valid
    1280×720 PNG plus a short AAC music track and asserts a probeable MP4.
    """
    _require_ffmpeg()

    from PIL import Image

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    img_path = media_dir / "real.png"
    Image.new("RGB", (1280, 720), color=(10, 60, 120)).save(img_path)

    music_path = tmp_path / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=5",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(music_path),
        ],
        check=True,
        capture_output=True,
    )

    output_path = tmp_path / "final.mp4"
    compose_musical_video.compose(
        music_path=music_path,
        media_dir=media_dir,
        output_path=output_path,
        segment_seconds=5.0,
        transition_seconds=1.0,
        width=1280,
        height=720,
        fps=30,
    )

    assert output_path.is_file()
    duration = compose_musical_video._probe_duration(output_path)
    assert duration > 0.0
