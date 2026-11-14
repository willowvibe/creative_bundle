"""Tests for the HyperFrames HTML composition compiler."""

from __future__ import annotations

from pathlib import Path

from video_editor_core.hyperframes_project import compile_hyperframes_project
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


def _text(name: str, text: str, start: float, end: float) -> Layer:
    return Layer(
        kind="text",
        name=name,
        rect=Rect(y=900, width=1920, height=200),
        text=text,
        timing=Timing(start=start, end=end),
        style={"font_size": 48, "font_color": "#FFFFFF"},
    )


def test_project_writes_index_and_copies_base(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    project = tmp_path / "proj"
    index = compile_hyperframes_project(_graph(__source=source), project)

    assert index == project / "index.html"
    html = index.read_text(encoding="utf-8")
    assert 'data-composition-id="main"' in html
    assert 'data-width="1920"' in html and 'data-height="1080"' in html
    assert 'data-duration="5.000"' in html
    assert 'id="base_video"' in html
    assert "assets/clip.mp4" in html
    assert (project / "assets" / "clip.mp4").read_bytes() == b"video"


def test_text_overlay_becomes_timed_clip(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    layer = _text("caption_line", "Hello world", 1.0, 2.5)
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[layer]), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    assert 'class="clip"' in html
    assert 'data-start="1.000"' in html
    assert 'data-duration="1.500"' in html
    assert "Hello world" in html


def test_logo_overlay_copies_its_asset(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    logo = tmp_path / "brand.png"
    logo.write_bytes(b"png")
    layer = Layer(
        kind="logo",
        name="brand",
        rect=Rect(x=1700, y=1000, width=180, height=60),
        source=str(logo),
        timing=Timing(start=0.0, end=None),
    )
    project = tmp_path / "proj"
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[layer]), project
    ).read_text(encoding="utf-8")
    assert "<img" in html
    assert "assets/brand.png" in html
    # full-duration logo: duration = graph duration
    assert 'data-duration="5.000"' in html
    assert (project / "assets" / "brand.png").exists()


def test_base_video_override_is_used(tmp_path: Path) -> None:
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(b"cut")
    project = tmp_path / "proj"
    html = compile_hyperframes_project(_graph(), project, base_video=cut).read_text(
        encoding="utf-8"
    )
    assert "assets/cut.mp4" in html
    assert (project / "assets" / "cut.mp4").exists()


def test_text_is_html_escaped(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    layer = _text("caption_line", '<b>A & "B"</b>', 0.0, 1.0)
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[layer]), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert "<b>A" not in html


def test_duplicate_overlay_tracks_are_distinct(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    layers = [_text("caption_line", "one", 0.0, 1.0), _text("caption_line", "two", 1.0, 2.0)]
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=layers), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    assert 'data-track-index="20"' in html
    assert 'data-track-index="21"' in html
    assert 'id="caption_line_0"' in html
    assert 'id="caption_line_1"' in html


def test_root_opts_out_of_timeline_poll(tmp_path: Path) -> None:
    """Pure-CSS compositions must set data-no-timeline, or HyperFrames polls
    45s for a ``window.__timelines`` registration on every render (#239)."""
    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    html = compile_hyperframes_project(_graph(__source=source), tmp_path / "proj").read_text(
        encoding="utf-8"
    )
    assert "data-no-timeline" in html


def _karaoke_layer() -> Layer:
    return Layer(
        kind="text",
        name="caption_line",
        rect=Rect(y=900, width=1920, height=120),
        text="gas and ice",
        timing=Timing(start=2.0, end=3.5),
        style={
            "font": "Montserrat",
            "font_size": 58,
            "font_weight": 800,
            "font_color": "#FFFFFF",
            "text_effect": "shadow",
            "highlight_color": "#FDB813",
            "words": [
                {"text": "gas", "start": 0.0, "end": 0.4},
                {"text": "and", "start": 0.45, "end": 0.7},
                {"text": "ice", "start": 0.8, "end": 1.3},
            ],
        },
    )


def test_text_layer_embeds_bundled_font(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    project = tmp_path / "proj"
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[_karaoke_layer()]), project
    ).read_text(encoding="utf-8")
    assert "@font-face" in html and "font-family: 'Montserrat'" in html
    assert (project / "assets" / "fonts" / "Montserrat.ttf").is_file()
    assert "font-family:'Montserrat'," in html
    assert "font-weight:800" in html


def test_shadow_effect_replaces_glyph_stroke(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[_karaoke_layer()]), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    clip = next(line for line in html.splitlines() if "caption_line" in line)
    assert "-webkit-text-stroke" not in clip
    assert "text-shadow" in clip


def test_karaoke_words_highlight_at_their_own_times(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[_karaoke_layer()]), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    assert "@keyframes ve_word_hl" in html
    assert html.count('class="ve-word"') == 3
    # Delays are relative to the clip's data-start (HyperFrames seeks CSS
    # animations from the clip start).
    assert "ve_word_hl 0.400s step-end 0.000s" in html
    assert "ve_word_hl 0.500s step-end 0.800s" in html
    assert "--ve-hl:#FDB813" in html


def test_plain_text_layer_keeps_default_stroke(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[_text("caption_line", "Hello", 0.0, 1.0)]),
        tmp_path / "proj",
    ).read_text(encoding="utf-8")
    assert "-webkit-text-stroke:4px" in html
    assert 'class="ve-word"' not in html


def test_karaoke_words_share_one_inline_line(tmp_path: Path) -> None:
    """The caption box is a flex container; bare word spans would each become
    a flex item and the spaces between them would collapse."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    html = compile_hyperframes_project(
        _graph(__source=source, overlays=[_karaoke_layer()]), tmp_path / "proj"
    ).read_text(encoding="utf-8")
    clip = next(line for line in html.splitlines() if "caption_line" in line)
    body = clip.split(">", 1)[1]
    assert body.startswith('<span class="ve-line">')
    assert "</span> <span" in body
