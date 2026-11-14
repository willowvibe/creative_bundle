"""Tests for the opt-in linear-light (float) compositing path.

The FFmpeg compiler historically blends overlays with ``overlay=`` in gamma /
limited-range space. ``RenderGraph.color_mode == "linear"`` wraps image / logo
/ video overlays in a float, linear-light colour conversion. These tests assert
the filtergraph shape (no FFmpeg execution) plus one optional end-to-end smoke
test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from video_editor_core.compile_ffmpeg import (
    _LINEAR_FLOAT_PIX_FMT,
    compile_ffmpeg,
)
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph
from video_editor_ffmpeg.probe import probe_media

from tests.conftest import _require_ffmpeg


def _cut(src_start: float, src_end: float, tl_start: float, tl_end: float) -> Cut:
    return Cut(
        source_start=src_start,
        source_end=src_end,
        timeline_start=tl_start,
        timeline_end=tl_end,
    )


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "source.mp4",
        "cuts": [_cut(0.0, 5.0, 0.0, 5.0)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def _filter_complex(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def _image_layer() -> Layer:
    return Layer(
        kind="image",
        name="sticker",
        rect=Rect(x=100, y=100, width=200, height=200),
        source="sticker.png",
    )


def _text_layer() -> Layer:
    return Layer(
        kind="text",
        name="caption",
        rect=Rect(x=0, y=900, width=1920, height=200),
        text="hello",
    )


def test_color_mode_defaults_to_gamma() -> None:
    graph = _graph(overlays=[_image_layer()])
    assert graph.color_mode == "gamma"


def test_gamma_image_overlay_is_unchanged() -> None:
    """The default path must not introduce any linear / float filters."""
    fc = _filter_complex(compile_ffmpeg(_graph(overlays=[_image_layer()]), "out.mp4")[0])
    assert "zscale" not in fc
    assert "transfer=linear" not in fc
    assert _LINEAR_FLOAT_PIX_FMT not in fc
    # The historical gamma blend is still used, unchanged.
    assert "overlay=100:100:eof_action=pass" in fc


def test_linear_image_overlay_wraps_in_float_linear() -> None:
    graph = _graph(overlays=[_image_layer()], color_mode="linear")
    fc = _filter_complex(compile_ffmpeg(graph, "out.mp4")[0])

    # Base converted to linear light in the float format before the overlay.
    assert "zscale=transfer=linear" in fc
    assert f"format={_LINEAR_FLOAT_PIX_FMT}" in fc
    # Overlay input converted to the same float format.
    assert f"[ov_0_scaled]format={_LINEAR_FLOAT_PIX_FMT}[ov_0_linear]" in fc
    # Converted back to gamma output after the blend.
    assert "transferin=linear" in fc

    overlay_at = fc.index("overlay=100:100")
    assert fc.index("transfer=linear") < overlay_at
    assert overlay_at < fc.index("transferin=linear")
    # Chain ends by targeting the gamma output format and the mapped label.
    assert "format=yuv420p[" in fc
    assert "]null[vout]" in fc


def test_linear_text_only_does_not_wrap() -> None:
    """Linear mode without a non-text overlay stays byte-identical to gamma."""
    graph = _graph(overlays=[_text_layer()], color_mode="linear")
    fc = _filter_complex(compile_ffmpeg(graph, "out.mp4")[0])
    assert "zscale" not in fc
    assert _LINEAR_FLOAT_PIX_FMT not in fc
    assert "drawtext=" in fc


def test_linear_text_and_image_orders_text_before_wrap() -> None:
    graph = _graph(
        overlays=[_image_layer(), _text_layer()],
        color_mode="linear",
    )
    fc = _filter_complex(compile_ffmpeg(graph, "out.mp4")[0])
    # drawtext runs in gamma on the base, before the linear conversion.
    assert fc.index("drawtext=") < fc.index("transfer=linear")
    # Both overlays are represented and the image is blended in linear light.
    assert "drawtext=" in fc
    assert "overlay=100:100" in fc


def _ffmpeg_has_zscale() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "zscale" in result.stdout


def test_linear_composite_renders_valid_video(tmp_path: Path) -> None:
    """Optional end-to-end smoke test: compile + run + probe a linear render."""
    _require_ffmpeg()
    if not _ffmpeg_has_zscale():
        pytest.skip("zscale filter not available in this ffmpeg")

    source = tmp_path / "source.mp4"
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
            "testsrc=duration=1.5:size=160x120:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    logo = tmp_path / "logo.png"
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
            "color=c=red@0.5:s=32x32,format=rgba",
            "-frames:v",
            "1",
            str(logo),
        ],
        check=True,
        capture_output=True,
    )

    layer = Layer(
        kind="image",
        name="logo",
        rect=Rect(x=10, y=10, width=32, height=32),
        source=str(logo),
    )
    graph = _graph(
        source=str(source),
        width=160,
        height=120,
        has_audio=False,
        overlays=[layer],
        color_mode="linear",
    )
    output = tmp_path / "linear.mp4"
    cmd = compile_ffmpeg(graph, output)[0]
    subprocess.run(cmd, check=True, capture_output=True)

    info = probe_media(output)
    assert info.width == 160
    assert info.height == 120
    assert info.duration is not None and info.duration > 0
