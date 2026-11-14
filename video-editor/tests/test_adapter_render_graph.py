"""Tests for the RenderGraph -> Composition adapter."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.adapters.from_render_graph import from_render_graph, layer_id
from video_editor_core.render_graph import Cut, Layer, Rect, RenderGraph, Timing


def _graph(**overrides: object) -> RenderGraph:
    source = overrides.pop("__source", "source.mp4")
    base: dict[str, object] = {
        "job_id": "job-1",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "source": str(source),
        "has_audio": True,
        "cuts": [Cut(source_start=0.0, source_end=5.0, timeline_start=0.0, timeline_end=5.0)],
    }
    base.update(overrides)
    return RenderGraph(**base)  # type: ignore[arg-type]


def _text(name: str, text: str, start: float, end: float, **style: object) -> Layer:
    return Layer(
        kind="text",
        name=name,
        rect=Rect(y=900, width=1920, height=120),
        text=text,
        timing=Timing(start=start, end=end),
        style={"font_size": 48, "font_color": "#FFFFFF", **style},
    )


def test_the_base_video_is_the_first_layer():
    comp = from_render_graph(_graph())
    layers = comp.scenes[0].layers
    assert layers[0].id == "base_video"
    assert layers[0].media_kind == "video"
    assert layers[0].track == 0 and layers[0].z == 0
    assert comp.audio_mode == "external"


def test_overlays_keep_their_ids_tracks_and_windows():
    layers = [_text("caption_line", "one", 0.0, 1.0), _text("caption_line", "two", 1.0, 2.0)]
    comp = from_render_graph(_graph(overlays=layers))
    overlays = comp.scenes[0].layers[1:]
    assert [layer.id for layer in overlays] == ["caption_line_0", "caption_line_1"]
    assert [layer.track for layer in overlays] == [20, 21]
    assert overlays[0].time.start == 0.0 and overlays[0].time.end == 1.0


def test_a_word_timed_text_layer_becomes_rich_text():
    layer = _text(
        "caption_line",
        "gas and ice",
        2.0,
        3.5,
        highlight_color="#FDB813",
        words=[
            {"text": "gas", "start": 0.0, "end": 0.4},
            {"text": "and", "start": 0.45, "end": 0.7},
            {"text": "ice", "start": 0.8, "end": 1.3},
        ],
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    rich = comp.scenes[0].layers[1]
    assert rich.kind == "rich_text"
    assert [span.text for span in rich.content.spans] == ["gas", "and", "ice"]
    assert rich.content.spans[2].start == 0.8


def test_a_text_layer_without_words_stays_plain_text():
    comp = from_render_graph(_graph(overlays=[_text("card", "Hello", 0.0, 1.0)]))
    assert comp.scenes[0].layers[1].kind == "text"


def test_a_spotlight_shape_keeps_its_preset():
    layer = Layer(
        kind="shape",
        name="spot",
        rect=Rect(x=100, y=100, width=400, height=200),
        timing=Timing(start=0.5, end=2.0),
        style={"effect": "spotlight", "color": "#FDB813"},
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    shape = comp.scenes[0].layers[1]
    assert shape.kind == "shape" and shape.preset == "spotlight"


def test_images_and_videos_map_to_their_media_kinds(tmp_path: Path):
    image = tmp_path / "brand.png"
    image.write_bytes(b"png")
    video = tmp_path / "broll.mp4"
    video.write_bytes(b"mp4")
    layers = [
        Layer(
            kind="logo",
            name="brand",
            rect=Rect(width=180, height=60),
            source=str(image),
            timing=Timing(start=0.0, end=None),
        ),
        Layer(
            kind="video",
            name="broll",
            rect=Rect(x=1700, y=0, width=220, height=220),
            source=str(video),
            timing=Timing(start=0.0, end=2.0),
        ),
    ]
    comp = from_render_graph(_graph(overlays=layers))
    overlays = comp.scenes[0].layers[1:]
    assert overlays[0].kind == "media" and overlays[0].media_kind == "image"
    assert overlays[0].style["object_fit"] == "contain"
    assert overlays[1].media_kind == "video"
    assert overlays[1].style["object_fit"] == "cover"


def test_layers_that_leave_the_canvas_are_clamped():
    layer = Layer(
        kind="text",
        name="edge",
        rect=Rect(x=1900, y=1000, width=400, height=200),
        text="edge",
        timing=Timing(start=0.0, end=1.0),
        style={"font_size": 40},
    )
    comp = from_render_graph(_graph(overlays=[layer]))
    rect = comp.scenes[0].layers[1].rect
    assert rect.x + rect.width <= 1920 and rect.y + rect.height <= 1080


def test_the_base_video_override_is_honoured(tmp_path: Path):
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"cut")
    comp = from_render_graph(_graph(), base_video=cut)
    assert comp.scenes[0].layers[0].content.source == str(cut)


def test_an_overlay_in_the_final_frame_is_clamped_to_the_composition():
    duration = 5.0
    layer = _text("late", "last frame", duration - 0.01, duration)
    comp = from_render_graph(_graph(overlays=[layer]))
    time = comp.scenes[0].layers[1].time
    assert time.end <= comp.duration
    assert time.end > time.start


def test_non_ascii_layer_names_produce_ascii_ids():
    assert layer_id("字幕", 0) == "___0"
