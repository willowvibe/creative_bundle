"""#341: captions pick dark text on light backdrops, and QA checks both."""

from __future__ import annotations

import pytest
from captions.captions import caption_graph_layers
from captions.models import CaptionLine, CaptionStyle, CaptionWord
from captions.theme import (
    MIN_CAPTION_CONTRAST,
    contrast_ratio,
    relative_luminance,
    theme_for_backdrop,
)
from video_editor_core.render_graph import Rect
from video_editor_pipelines.caption_qa import caption_contrast_check, caption_tokens_check


def test_relative_luminance_and_contrast_follow_wcag():
    assert relative_luminance("#FFFFFF") == pytest.approx(1.0)
    assert relative_luminance("#000000") == pytest.approx(0.0)
    assert contrast_ratio(1.0, 0.0) == pytest.approx(21.0)


def test_dark_backdrop_keeps_the_preset_look():
    style = CaptionStyle.preset("karaoke")
    assert theme_for_backdrop(0.05, style) == {}


def test_cream_plate_gets_dark_text_that_passes_contrast():
    style = CaptionStyle.preset("karaoke")
    cream = relative_luminance("#F5F2EB")
    theme = theme_for_backdrop(cream, style)
    assert theme["font_color"] != style.font_color
    assert theme["text_effect"] == "halo"
    text = relative_luminance(str(theme["font_color"]))
    highlight = relative_luminance(str(theme["highlight_color"]))
    assert contrast_ratio(text, cream) >= 4.5
    assert contrast_ratio(highlight, cream) >= MIN_CAPTION_CONTRAST


def test_mid_bright_backdrop_lifts_only_the_active_word():
    style = CaptionStyle.preset("karaoke")
    theme = theme_for_backdrop(0.25, style)
    assert theme == {"highlight_color": "#FFF3C4"}
    active = relative_luminance(str(theme["highlight_color"]))
    assert contrast_ratio(active, 0.25) >= MIN_CAPTION_CONTRAST


def test_no_light_highlight_passes_so_the_active_word_shares_the_text_colour():
    style = CaptionStyle.preset("karaoke")
    theme = theme_for_backdrop(0.28, style)
    assert theme == {"highlight_color": style.font_color}


def test_contrast_check_fails_an_active_word_under_the_floor():
    row = {
        "start": 0.0,
        "text": "Monsoon",
        "font_color": "#FFFFFF",
        "highlight_color": "#FDB813",
        "backdrop": 0.25,
    }
    failed = caption_contrast_check([row])
    assert not failed.ok and "active" in failed.detail
    body_only = caption_contrast_check([{k: v for k, v in row.items() if k != "highlight_color"}])
    assert body_only.ok


def test_caption_layers_take_per_line_overrides():
    style = CaptionStyle.preset("karaoke")
    lines = [
        CaptionLine(words=[CaptionWord(text="one", start=0.0, end=0.5)]),
        CaptionLine(words=[CaptionWord(text="two", start=0.6, end=1.0)]),
    ]
    layers = caption_graph_layers(
        lines, Rect(y=900, width=1920, height=150), style, overrides=[{}, {"font_color": "#111"}]
    )
    assert layers[0].style["font_color"] == style.font_color
    assert layers[1].style["font_color"] == "#111"


def test_contrast_check_fails_white_text_on_a_cream_plate():
    cream = relative_luminance("#F5F2EB")
    bad = [{"start": 0.0, "text": "Monsoon", "font_color": "#FFFFFF", "backdrop": cream}]
    good = [{"start": 0.0, "text": "Monsoon", "font_color": "#1C1917", "backdrop": cream}]
    assert not caption_contrast_check(bad).ok
    assert caption_contrast_check(good).ok


def test_tokens_check_catches_glued_and_orphan_words():
    script = "Two thousand four hundred kilometres — a wall, a plain."
    assert caption_tokens_check(
        ["Two thousand four hundred", "kilometres — a wall, a plain."], script
    ).ok
    glued = caption_tokens_check(["kilometresa wall, a plain."], script)
    assert not glued.ok and "kilometresa" in glued.detail
    orphan = caption_tokens_check(["Two thousand four hundred kilometres — a wall,", "a"], script)
    assert not orphan.ok


def test_band_luminance_reads_a_cream_plate_and_a_black_frame(tmp_path):
    import subprocess

    from video_editor_pipelines.explainer_pipeline import band_luminance

    clip = tmp_path / "two.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xF5F2EB:s=320x240:d=1:r=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1:r=10",
            "-filter_complex",
            "[0][1]concat=n=2:v=1",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
    )
    series = band_luminance(clip, Rect(x=0, y=160, width=320, height=60))
    early = [lum for t, lum in series if t < 0.8]
    late = [lum for t, lum in series if t > 1.2]
    assert min(early) > 0.75
    assert max(late) < 0.02


def test_line_backdrop_averages_the_samples_inside_the_line():
    from video_editor_pipelines.explainer_pipeline import line_backdrop

    series = [(0.0, 0.9), (0.25, 0.9), (0.5, 0.1), (0.75, 0.1)]
    assert line_backdrop(series, 0.0, 0.3) == pytest.approx(0.9)
    assert line_backdrop(series, 0.4, 0.8) == pytest.approx(0.1)
    # A line shorter than the sampling step takes the nearest sample.
    assert line_backdrop(series, 0.6, 0.61) == pytest.approx(0.1)


def test_tokens_check_allows_a_one_word_sentence():
    script = "No. The Thar desert is vast."
    assert caption_tokens_check(["No.", "The Thar desert is vast."], script).ok
    # A short word stranded from its sentence is still an orphan.
    assert not caption_tokens_check(["The desert is a", "sea."], "The desert is a sea.").ok
