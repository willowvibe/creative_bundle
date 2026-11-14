"""Complex-script text is shaped (Pillow + libraqm) and overlaid, never drawn by drawtext (#322)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageFont
from video_editor_core.shaped_text import (
    PngOverlay,
    font_for,
    linear_graph,
    needs_shaping,
    render_shaped_text,
    script_of,
)
from video_editor_ffmpeg.run import run_ffmpeg

TELUGU = "మంత్ర రహస్యం"


def test_only_complex_scripts_need_shaping():
    assert script_of(TELUGU) == "Telugu"
    assert script_of("नमस्ते") == "Devanagari"
    assert not needs_shaping("Hello, Ganges: 2,525 km")
    assert not needs_shaping("日本語")  # CJK has no conjuncts; drawtext handles it


def test_shaping_joins_the_conjuncts_drawtext_breaks_apart():
    try:
        font = font_for("Telugu")
    except Exception:  # noqa: BLE001
        pytest.skip("no Noto Telugu font installed")
    shaped = ImageFont.truetype(str(font), 90, layout_engine=ImageFont.Layout.RAQM)
    unshaped = ImageFont.truetype(str(font), 90, layout_engine=ImageFont.Layout.BASIC)
    # Glyph-by-glyph layout (what drawtext does) is ~20% wider: ్ర and స్య stay as separate marks.
    assert shaped.getlength(TELUGU) < 0.9 * unshaped.getlength(TELUGU)


def test_linear_graph_splices_png_overlays_into_a_plain_chain(tmp_path: Path):
    png = tmp_path / "t.png"
    graph = linear_graph(
        ["scale=320:180", PngOverlay(png, "(W-w)/2", "10", "between(t,1,2)"), "format=yuv420p"]
    )
    assert graph.startswith("scale=320:180[shp1b];movie=")
    assert "[shp1b][shp1t]overlay=x=(W-w)/2:y=10:enable='between(t,1,2)',format=yuv420p" in graph
    assert linear_graph(["null"], source="[a]", sink="[b]") == "[a]null[b]"


def test_telugu_renders_through_ffmpeg_as_visible_shaped_text(tmp_path: Path):
    try:
        font_for("Telugu")
    except Exception:  # noqa: BLE001
        pytest.skip("no Noto Telugu font installed")
    shaped = render_shaped_text(
        TELUGU, tmp_path / "title.png", font_size=72, color="white", border_width=4
    )
    out = tmp_path / "frame.png"
    run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=800x200:d=1",
            "-frames:v",
            "1",
            "-vf",
            linear_graph([PngOverlay(shaped.path, "(W-w)/2", "(H-h)/2")]),
            str(out),
        ]
    )
    frame = Image.open(out).convert("L")
    bright = sum(1 for value in frame.getdata() if value > 200)
    assert bright > 2000  # the white glyphs landed on the frame
