"""Reference-video style measurement (P4.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from video_editor_core.style_from import (
    Shot,
    StyleProfile,
    design_draft,
    detect_cuts,
    dominant_palette,
    measure_style,
    sample_frames,
    style_markdown,
    write_style,
)

from tests.conftest import _require_ffmpeg


def _three_colour_video(tmp_path: Path) -> Path:
    _require_ffmpeg()
    output = tmp_path / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=lime:s=320x180:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def test_markdown_and_design_draft_from_a_profile(tmp_path: Path) -> None:
    profile = StyleProfile(
        source="reference.mp4",
        duration=10.0,
        fps=30.0,
        width=1080,
        height=1920,
        shots=(Shot(0.0, 2.0), Shot(2.0, 5.0), Shot(5.0, 10.0)),
        palette=("#101010", "#f0f0f0", "#d02020"),
    )

    markdown = style_markdown(profile)
    assert "- Cuts: 2 (12.0/min)" in markdown
    assert "median 3.000s" in markdown
    assert "`#d02020`" in markdown
    assert "never the content" in markdown

    draft = design_draft(profile)
    palette = draft["palette"]
    assert isinstance(palette, dict)
    assert palette["bg"] == "#101010"
    assert palette["accent"] == "#d02020"


def test_write_style_writes_both_files(tmp_path: Path) -> None:
    profile = StyleProfile("x", 2.0, 30.0, 1080, 1920, (Shot(0.0, 2.0),), ("#000000",))
    style, design = write_style(
        profile, style_path=tmp_path / "style.md", design_path=tmp_path / "design.json"
    )
    assert style.is_file() and design is not None and design.is_file()
    assert "# Style guide" in style.read_text(encoding="utf-8")


def test_measure_a_three_shot_reference(tmp_path: Path) -> None:
    video = _three_colour_video(tmp_path)

    cuts = detect_cuts(video)
    assert len(cuts) == 2
    assert cuts[0] == pytest.approx(1.0, abs=0.2)
    assert cuts[1] == pytest.approx(2.0, abs=0.2)

    palette = dominant_palette(video, frames=6, colors=3)
    assert len(palette) == 3
    channels = [_rgb(colour) for colour in palette]
    assert any(red > 200 and green < 80 for red, green, _blue in channels)
    assert any(blue > 150 and red < 100 for red, _green, blue in channels)

    profile = measure_style(video, frames=6, colors=3)
    assert profile.aspect == "16:9"
    assert profile.fps == pytest.approx(30.0, abs=0.5)
    assert len(profile.shots) == 3
    assert profile.cut_rate_per_minute == pytest.approx(40.0, abs=5.0)


def test_sample_frames_extracts_pngs(tmp_path: Path) -> None:
    video = _three_colour_video(tmp_path)
    frames = sample_frames(video, tmp_path / "frames", frames=3, width=80)
    assert len(frames) == 3
    assert all(frame.suffix == ".png" for frame in frames)
