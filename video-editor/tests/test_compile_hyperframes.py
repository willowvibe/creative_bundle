"""Tests for the HyperFrames composition compiler."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.compile_hyperframes import compile_hyperframes
from video_editor_core.hyperframes_cli import lint_plan
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph, Timing


def _graph(**overrides: object) -> RenderGraph:
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": "source.mp4",
        "cuts": [
            Cut(
                source_start=0.0,
                source_end=5.0,
                timeline_start=0.0,
                timeline_end=5.0,
            )
        ],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def test_plan_canvas_and_duration() -> None:
    plan = compile_hyperframes(_graph(), "cut.mp4")
    assert plan["width"] == 1920
    assert plan["height"] == 1080
    assert plan["fps"] == 30
    assert plan["duration"] == 5.0


def test_plan_has_single_base_video_layer() -> None:
    plan = compile_hyperframes(_graph(), "cut.mp4")
    base = plan["layers"][0]
    assert base["type"] == "video"
    assert base["name"] == "base_video"
    assert base["source"] == "cut.mp4"
    assert base["width"] == 1920 and base["height"] == 1080


def test_overlays_become_timed_layers() -> None:
    caption = Layer(
        kind="text",
        name="caption",
        rect=Rect(x=0, y=900, width=1920, height=200),
        text="hello",
        timing=Timing(start=1.0, end=2.0),
    )
    plan = compile_hyperframes(_graph(overlays=[caption]), "cut.mp4")
    assert len(plan["layers"]) == 2
    text = plan["layers"][1]
    assert text["type"] == "text"
    assert text["text"] == "hello"
    assert text["timing"] == {"start": 1.0, "end": 2.0}


def test_plan_passes_lint_when_sources_exist(tmp_path: Path) -> None:
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"x")
    logo = tmp_path / "brand.png"
    logo.write_bytes(b"png")
    layer = Layer(
        kind="logo",
        name="brand",
        rect=Rect(x=1700, y=1000, width=180, height=60),
        source=str(logo),
    )
    plan = compile_hyperframes(_graph(overlays=[layer]), str(cut))
    assert lint_plan(plan) == []
